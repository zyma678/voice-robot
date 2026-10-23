import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class ConversationStore:
    _REVERSE_READ_BLOCK_SIZE = 64 * 1024
    _MUTATION_LOCKS: dict[str, threading.RLock] = {}
    _MUTATION_LOCKS_GUARD = threading.Lock()
    _REQUIRED_TURN_FIELDS = {
        "turn_id",
        "sequence",
        "created_at",
        "user_text",
        "assistant_text",
        "reply_source",
        "fallback_reason",
        "safety_passed",
    }

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def append_turn(
        self,
        user_id: str,
        *,
        user_text: str,
        assistant_text: str,
        reply_source: str,
        fallback_reason: str | None,
        safety_passed: bool,
    ) -> dict:
        with self._mutation_lock(user_id):
            return self._append_turn_locked(
                user_id,
                user_text=user_text,
                assistant_text=assistant_text,
                reply_source=reply_source,
                fallback_reason=fallback_reason,
                safety_passed=safety_passed,
            )

    def _append_turn_locked(
        self,
        user_id: str,
        *,
        user_text: str,
        assistant_text: str,
        reply_source: str,
        fallback_reason: str | None,
        safety_passed: bool,
    ) -> dict:
        now = self._now()
        meta = self.load_meta(user_id)
        turn = {
            "turn_id": f"turn_{uuid4().hex[:8]}",
            "sequence": meta["next_sequence"],
            "created_at": now,
            "user_text": user_text,
            "assistant_text": assistant_text,
            "reply_source": reply_source,
            "fallback_reason": fallback_reason,
            "safety_passed": safety_passed,
        }

        turns_path = self._turns_path(user_id)
        turns_path.parent.mkdir(parents=True, exist_ok=True)
        with turns_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(turn, ensure_ascii=False) + "\n")

        meta["next_sequence"] += 1
        meta["updated_at"] = now
        self.save_meta(user_id, meta)
        return turn

    def list_recent_turns(self, user_id: str, *, limit: int = 20) -> list[dict]:
        if limit <= 0:
            return []
        turns: list[dict] = []
        iterator = self._iter_turns_reverse(user_id)
        try:
            for turn in iterator:
                turns.append(self._public_turn(turn))
                if len(turns) >= limit:
                    break
        finally:
            iterator.close()
        return sorted(turns, key=lambda turn: turn.get("sequence", 0))

    def get_turn(
        self,
        user_id: str,
        turn_id: str,
        *,
        include_audio_payload: bool = False,
    ) -> dict | None:
        """Return one turn without loading the complete JSONL file into memory.

        Turns are normally requested shortly after creation, so scanning from the
        tail also makes TTS replay lookup cheap for the common case.
        """
        iterator = self._iter_turns_reverse(user_id)
        try:
            for turn in iterator:
                if turn.get("turn_id") != turn_id:
                    continue
                return turn if include_audio_payload else self._public_turn(turn)
        finally:
            iterator.close()
        return None

    def list_turns(
        self,
        user_id: str,
        *,
        start_sequence: int | None = None,
        end_sequence: int | None = None,
        include_audio_payload: bool = False,
    ) -> list[dict]:
        turns = self._read_turns(user_id)
        if start_sequence is not None:
            turns = [turn for turn in turns if turn.get("sequence", 0) >= start_sequence]
        if end_sequence is not None:
            turns = [turn for turn in turns if turn.get("sequence", 0) <= end_sequence]
        turns = sorted(turns, key=lambda turn: turn.get("sequence", 0))
        if include_audio_payload:
            return turns
        return [self._public_turn(turn) for turn in turns]

    def load_meta(self, user_id: str) -> dict:
        path = self._meta_path(user_id)
        if not path.exists():
            return self._default_meta(user_id)
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return self._default_meta(user_id)

        default_meta = self._default_meta(user_id)
        return {
            **default_meta,
            **meta,
            "user_id": meta.get("user_id", user_id),
        }

    def save_meta(self, user_id: str, meta: dict) -> None:
        with self._mutation_lock(user_id):
            self._save_meta_locked(user_id, meta)

    def _save_meta_locked(self, user_id: str, meta: dict) -> None:
        path = self._meta_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "user_id": meta.get("user_id", user_id),
            "next_sequence": meta.get("next_sequence", 1),
            "last_summarized_sequence": meta.get("last_summarized_sequence", 0),
            "updated_at": meta.get("updated_at", self._now()),
        }
        self._atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        )

    def update_turn(self, user_id: str, turn_id: str, patch: dict) -> dict | None:
        with self._mutation_lock(user_id):
            return self._update_turn_locked(user_id, turn_id, patch)

    def _update_turn_locked(
        self, user_id: str, turn_id: str, patch: dict
    ) -> dict | None:
        turns = self._read_turns(user_id)
        updated_index: int | None = None
        for index, turn in enumerate(turns):
            if turn.get("turn_id") == turn_id:
                turns[index] = {
                    **turn,
                    **patch,
                    "turn_id": turn["turn_id"],
                    "sequence": turn["sequence"],
                }
                updated_index = index
                break

        if updated_index is None:
            return None

        rewritten_turns = self._rewrite_turns(user_id, turns)
        return rewritten_turns[updated_index]

    def attach_tts_audio(
        self,
        user_id: str,
        turn_id: str,
        *,
        audio_bytes: bytes,
        audio_format: str,
        sample_rate_hz: int,
        byte_length: int,
    ) -> dict | None:
        if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
            return None
        if audio_format not in {"pcm_s16le"}:
            return None
        if type(sample_rate_hz) is not int or sample_rate_hz <= 0:
            return None
        if type(byte_length) is not int or byte_length < 0:
            return None
        if byte_length != len(audio_bytes):
            return None

        with self._mutation_lock(user_id):
            return self._attach_tts_audio_locked(
                user_id,
                turn_id,
                audio_bytes=bytes(audio_bytes),
                audio_format=audio_format,
                sample_rate_hz=sample_rate_hz,
                byte_length=byte_length,
            )

    def _attach_tts_audio_locked(
        self,
        user_id: str,
        turn_id: str,
        *,
        audio_bytes: bytes,
        audio_format: str,
        sample_rate_hz: int,
        byte_length: int,
    ) -> dict | None:

        turns = self._read_turns(user_id)
        updated_index: int | None = None
        for index, turn in enumerate(turns):
            if turn.get("turn_id") == turn_id:
                updated_index = index
                break
        if updated_index is None:
            return None

        stored_path, digest = self._store_tts_audio(user_id, audio_bytes)
        updated_turn = {
            **turns[updated_index],
            "tts_audio_path": stored_path,
            "tts_audio_sha256": digest,
            "tts_audio_format": audio_format,
            "tts_sample_rate_hz": sample_rate_hz,
            "tts_byte_length": byte_length,
        }
        # A turn may be re-attached while still carrying a historical embedded
        # payload. The sidecar is authoritative after its atomic write succeeds.
        updated_turn.pop("tts_audio_base64", None)
        turns[updated_index] = updated_turn
        # Audio is derived data and the sidecar is already durable before this
        # metadata update. Atomic replace protects the active JSONL; creating a
        # full backup for every spoken turn was the source of multi-GB growth.
        rewritten_turns = self._rewrite_turns(user_id, turns, create_backup=False)
        return rewritten_turns[updated_index]

    def delete_turn(self, user_id: str, turn_id: str) -> bool:
        with self._mutation_lock(user_id):
            return self._delete_turn_locked(user_id, turn_id)

    def _delete_turn_locked(self, user_id: str, turn_id: str) -> bool:
        turns = self._read_turns(user_id)
        remaining_turns = [turn for turn in turns if turn.get("turn_id") != turn_id]
        if len(remaining_turns) == len(turns):
            return False

        self._rewrite_turns(user_id, remaining_turns)
        return True

    def delete_all_turns(self, user_id: str) -> int:
        """Clear every turn for the user. Returns the number of turns removed.

        The existing turns.jsonl is backed up (via ``_rewrite_turns``) before
        the file is truncated, so the data can still be recovered from a .bak.
        Conversation meta (sequence counters) is left untouched to keep summary
        ranges consistent.
        """
        with self._mutation_lock(user_id):
            turns = self._read_turns(user_id)
            if not turns:
                return 0
            self._rewrite_turns(user_id, [])
            return len(turns)

    def _read_turns(self, user_id: str) -> list[dict]:
        path = self._turns_path(user_id)
        if not path.exists():
            return []

        turns = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                turn = self._parse_turn_line(line)
                if turn is not None:
                    turns.append(turn)
        return turns

    def _iter_turns_reverse(self, user_id: str) -> Iterator[dict]:
        path = self._turns_path(user_id)
        if not path.exists():
            return

        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            position = file.tell()
            pending = b""
            while position > 0:
                read_size = min(self._REVERSE_READ_BLOCK_SIZE, position)
                position -= read_size
                file.seek(position)
                pending = file.read(read_size) + pending
                lines = pending.split(b"\n")
                pending = lines[0]
                for raw_line in reversed(lines[1:]):
                    turn = self._parse_turn_line(raw_line)
                    if turn is not None:
                        yield turn

            turn = self._parse_turn_line(pending)
            if turn is not None:
                yield turn

    def _rewrite_turns(
        self,
        user_id: str,
        turns: list[dict],
        *,
        create_backup: bool = True,
    ) -> list[dict]:
        path = self._turns_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        rewritten_turns = [
            self._externalize_embedded_tts(user_id, turn) for turn in turns
        ]
        payload = "".join(
            json.dumps(turn, ensure_ascii=False) + "\n" for turn in rewritten_turns
        )
        temp_path = self._temporary_path(path)
        try:
            self._write_text_and_sync(temp_path, payload)
            if create_backup and path.exists():
                backup_path = path.with_name(
                    f"{path.name}.{self._backup_stamp()}.{uuid4().hex[:8]}.bak"
                )
                # Copy instead of moving the live file away. A crash before the
                # final replace therefore leaves the current JSONL untouched.
                shutil.copy2(path, backup_path)
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        return rewritten_turns

    def _externalize_embedded_tts(self, user_id: str, turn: dict) -> dict:
        """Move a valid historical Base64 PCM payload to a hashed sidecar.

        Malformed or incomplete legacy metadata is retained verbatim instead of
        being silently discarded. Existing backups are never modified.
        """
        encoded = turn.get("tts_audio_base64")
        if not isinstance(encoded, str) or not encoded:
            return dict(turn)
        if turn.get("tts_audio_format") != "pcm_s16le":
            return dict(turn)
        sample_rate_hz = turn.get("tts_sample_rate_hz")
        if type(sample_rate_hz) is not int or sample_rate_hz <= 0:
            return dict(turn)
        try:
            audio_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return dict(turn)
        if not audio_bytes:
            return dict(turn)
        declared_length = turn.get("tts_byte_length")
        if type(declared_length) is int and declared_length != len(audio_bytes):
            return dict(turn)

        stored_path, digest = self._store_tts_audio(user_id, audio_bytes)
        migrated = {
            **turn,
            "tts_audio_path": stored_path,
            "tts_audio_sha256": digest,
            "tts_byte_length": len(audio_bytes),
        }
        migrated.pop("tts_audio_base64", None)
        return migrated

    def _store_tts_audio(self, user_id: str, audio_bytes: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(audio_bytes).hexdigest()
        path = self._tts_dir(user_id) / f"{digest}.pcm"
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            existing_is_valid = (
                path.is_file()
                and path.stat().st_size == len(audio_bytes)
                and self._sha256_file(path) == digest
            )
            if not existing_is_valid:
                # Never overwrite an unexpected runtime artifact. A file at a
                # content-addressed path must already contain exactly that content.
                raise OSError(f"invalid existing TTS sidecar: {path}")
        else:
            self._atomic_write_bytes(path, audio_bytes)
        return str(path.relative_to(self.root)), digest

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _parse_turn_line(cls, line: str | bytes) -> dict | None:
        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError:
                return None
        if not line.strip():
            return None
        try:
            turn = json.loads(line)
        except json.JSONDecodeError:
            return None
        return turn if cls._is_valid_turn(turn) else None

    @classmethod
    def _atomic_write_text(cls, path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cls._temporary_path(path)
        try:
            cls._write_text_and_sync(temp_path, payload)
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @classmethod
    def _atomic_write_bytes(cls, path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cls._temporary_path(path)
        try:
            with temp_path.open("wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def _write_text_and_sync(path: Path, payload: str) -> None:
        with path.open("w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())

    @staticmethod
    def _temporary_path(path: Path) -> Path:
        return path.with_name(f".{path.name}.{uuid4().hex}.tmp")

    @staticmethod
    def _public_turn(turn: dict) -> dict:
        sanitized = dict(turn)
        sanitized.pop("tts_audio_base64", None)
        return sanitized

    def _conversation_dir(self, user_id: str) -> Path:
        return self.root / "conversations" / self._safe_component(user_id)

    def _mutation_lock(self, user_id: str) -> threading.RLock:
        # Use the normalized on-disk directory as the key, so aliases such as
        # "../u" and "u" cannot bypass serialization. This protects the common
        # single-process deployment from append-vs-rewrite record loss. Atomic
        # replace remains the crash-safety boundary; multi-worker deployments
        # should move this store to SQLite or add an OS-level file lock.
        key = str(self._conversation_dir(user_id).resolve())
        with self._MUTATION_LOCKS_GUARD:
            lock = self._MUTATION_LOCKS.get(key)
            if lock is None:
                lock = threading.RLock()
                self._MUTATION_LOCKS[key] = lock
            return lock

    def _tts_dir(self, user_id: str) -> Path:
        return self._conversation_dir(user_id) / "tts"

    def _turns_path(self, user_id: str) -> Path:
        return self._conversation_dir(user_id) / "turns.jsonl"

    def _meta_path(self, user_id: str) -> Path:
        return self._conversation_dir(user_id) / "meta.json"

    @classmethod
    def _default_meta(cls, user_id: str) -> dict:
        return {
            "user_id": user_id,
            "next_sequence": 1,
            "last_summarized_sequence": 0,
            "updated_at": cls._now(),
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _backup_stamp() -> str:
        return datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")

    @classmethod
    def _is_valid_turn(cls, turn: object) -> bool:
        if not isinstance(turn, dict):
            return False
        if not cls._REQUIRED_TURN_FIELDS.issubset(turn):
            return False
        return (
            isinstance(turn["turn_id"], str)
            and type(turn["sequence"]) is int
            and isinstance(turn["created_at"], str)
            and isinstance(turn["user_text"], str)
            and isinstance(turn["assistant_text"], str)
            and isinstance(turn["reply_source"], str)
            and (
                turn["fallback_reason"] is None
                or isinstance(turn["fallback_reason"], str)
            )
            and isinstance(turn["safety_passed"], bool)
        )

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", value).strip("_") or "item"
