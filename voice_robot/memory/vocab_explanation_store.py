from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path


class VocabExplanationStore:
    """Persists pre-generated 「生字管理」 explanations (word + writing breakdown)
    and their synthesized TTS audio so the console can show prior records and
    offer playback across sessions. Mirrors VocabularyStore's atomic-write /
    tolerant-read pattern; TTS PCM is stored as a sidecar .pcm file referenced
    by a relative path in the index (no base64 bloat in the JSON)."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def upsert(
        self,
        user_id: str,
        *,
        character: str,
        word: str,
        explanation: str,
        source: str,
        tts_audio: bytes | None = None,
        tts_sample_rate_hz: int | None = None,
    ) -> dict:
        now = self._now()
        entries = self._read_index(user_id)
        current = next(
            (entry for entry in entries if entry.get("character") == character),
            None,
        )
        if current is None:
            current = {
                "character": character,
                "created_at": now,
            }
            entries.append(current)

        current["word"] = word
        current["explanation"] = explanation
        current["source"] = source
        current["updated_at"] = now

        if tts_audio and tts_sample_rate_hz:
            rel_path = self._write_tts_audio(user_id, character, tts_audio)
            current["tts_path"] = rel_path
            current["tts_format"] = "pcm_s16le"
            current["tts_sample_rate_hz"] = int(tts_sample_rate_hz)
            current["tts_byte_length"] = len(tts_audio)
        else:
            # Explanation changed but no fresh audio — drop any stale TTS marker so
            # the console doesn't offer playback of audio that no longer matches.
            for key in ("tts_path", "tts_format", "tts_sample_rate_hz", "tts_byte_length"):
                current.pop(key, None)

        self._write_index(user_id, entries)
        return dict(current)

    def get(self, user_id: str, character: str) -> dict | None:
        for entry in self._read_index(user_id):
            if entry.get("character") == character:
                return dict(entry)
        return None

    def list(self, user_id: str) -> list[dict]:
        return sorted(
            self._read_index(user_id),
            key=lambda entry: (
                str(entry.get("updated_at", "")),
                str(entry.get("character", "")),
            ),
            reverse=True,
        )

    def read_tts_audio(self, user_id: str, character: str) -> tuple[bytes, int] | None:
        entry = self.get(user_id, character)
        if not entry:
            return None
        rel_path = entry.get("tts_path")
        sample_rate_hz = entry.get("tts_sample_rate_hz")
        if not isinstance(rel_path, str) or type(sample_rate_hz) is not int:
            return None
        root = self.root.resolve()
        expected_root = (self._tts_dir(user_id)).resolve()
        audio_path = (root / rel_path).resolve()
        if (
            audio_path.is_relative_to(root)
            and audio_path.is_relative_to(expected_root)
            and audio_path.suffix.lower() == ".pcm"
            and audio_path.exists()
            and audio_path.is_file()
        ):
            return audio_path.read_bytes(), sample_rate_hz
        return None

    def _write_tts_audio(self, user_id: str, character: str, audio: bytes) -> str:
        tts_dir = self._tts_dir(user_id)
        tts_dir.mkdir(parents=True, exist_ok=True)
        path = tts_dir / f"{self._safe_component(character)}.pcm"
        temp_path = path.with_suffix(".pcm.tmp")
        temp_path.write_bytes(audio)
        temp_path.replace(path)
        return path.relative_to(self.root).as_posix()

    def _read_index(self, user_id: str) -> list[dict]:
        path = self._index_path(user_id)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return [entry for entry in payload if self._is_valid_index_entry(entry)]

    def _write_index(self, user_id: str, entries: list[dict]) -> None:
        path = self._index_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = sorted(entries, key=lambda entry: str(entry.get("character", "")))
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(path)

    def _explanation_dir(self, user_id: str) -> Path:
        return self.root / "vocab_explanations" / self._safe_component(user_id)

    def _index_path(self, user_id: str) -> Path:
        return self._explanation_dir(user_id) / "index.json"

    def _tts_dir(self, user_id: str) -> Path:
        return self._explanation_dir(user_id) / "tts"

    @staticmethod
    def _is_valid_index_entry(entry: object) -> bool:
        return (
            isinstance(entry, dict)
            and isinstance(entry.get("character"), str)
            and isinstance(entry.get("word"), str)
            and isinstance(entry.get("explanation"), str)
            and isinstance(entry.get("source"), str)
            and isinstance(entry.get("created_at"), str)
            and isinstance(entry.get("updated_at"), str)
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", value).strip("_") or "item"
