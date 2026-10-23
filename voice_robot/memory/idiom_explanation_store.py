from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

# An idiom carries two narratable artifacts (interpretation + story); each has its
# own pre-synthesized TTS audio so the console can preview/dispatch either one.
_TTS_KINDS = ("explanation", "story")


class IdiomExplanationStore:
    """Persists pre-generated 「成语学习」 content (interpretation + short story)
    and their synthesized TTS audio so the console can show prior records and
    offer playback across sessions. Mirrors VocabExplanationStore's atomic-write /
    tolerant-read pattern; each TTS kind's PCM is stored as a sidecar .pcm file
    referenced by a relative path in the index (no base64 bloat in the JSON)."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def upsert(
        self,
        user_id: str,
        *,
        idiom: str,
        pinyin: str,
        explanation: str,
        story: str,
        source: str,
        explanation_tts: bytes | None = None,
        explanation_sample_rate_hz: int | None = None,
        story_tts: bytes | None = None,
        story_sample_rate_hz: int | None = None,
    ) -> dict:
        now = self._now()
        entries = self._read_index(user_id)
        current = next(
            (entry for entry in entries if entry.get("idiom") == idiom),
            None,
        )
        if current is None:
            current = {
                "idiom": idiom,
                "created_at": now,
            }
            entries.append(current)

        current["pinyin"] = pinyin
        current["explanation"] = explanation
        current["story"] = story
        current["source"] = source
        current["updated_at"] = now

        self._apply_tts(
            user_id, idiom, current, "explanation",
            explanation_tts, explanation_sample_rate_hz,
        )
        self._apply_tts(
            user_id, idiom, current, "story",
            story_tts, story_sample_rate_hz,
        )

        self._write_index(user_id, entries)
        return dict(current)

    def _apply_tts(
        self,
        user_id: str,
        idiom: str,
        entry: dict,
        kind: str,
        audio: bytes | None,
        sample_rate_hz: int | None,
    ) -> None:
        path_key = f"{kind}_tts_path"
        fmt_key = f"{kind}_tts_format"
        sr_key = f"{kind}_tts_sample_rate_hz"
        len_key = f"{kind}_tts_byte_length"
        if audio and sample_rate_hz:
            rel_path = self._write_tts_audio(user_id, idiom, kind, audio)
            entry[path_key] = rel_path
            entry[fmt_key] = "pcm_s16le"
            entry[sr_key] = int(sample_rate_hz)
            entry[len_key] = len(audio)
        else:
            # Text changed but no fresh audio — drop any stale TTS marker so the
            # console doesn't offer playback of audio that no longer matches.
            for key in (path_key, fmt_key, sr_key, len_key):
                entry.pop(key, None)

    def get(self, user_id: str, idiom: str) -> dict | None:
        for entry in self._read_index(user_id):
            if entry.get("idiom") == idiom:
                return dict(entry)
        return None

    def list(self, user_id: str) -> list[dict]:
        return sorted(
            self._read_index(user_id),
            key=lambda entry: (
                str(entry.get("updated_at", "")),
                str(entry.get("idiom", "")),
            ),
            reverse=True,
        )

    def read_tts_audio(
        self, user_id: str, idiom: str, kind: str
    ) -> tuple[bytes, int] | None:
        if kind not in _TTS_KINDS:
            return None
        entry = self.get(user_id, idiom)
        if not entry:
            return None
        rel_path = entry.get(f"{kind}_tts_path")
        sample_rate_hz = entry.get(f"{kind}_tts_sample_rate_hz")
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

    def _write_tts_audio(
        self, user_id: str, idiom: str, kind: str, audio: bytes
    ) -> str:
        tts_dir = self._tts_dir(user_id)
        tts_dir.mkdir(parents=True, exist_ok=True)
        path = tts_dir / f"{self._safe_component(idiom)}-{kind}.pcm"
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
        payload = sorted(entries, key=lambda entry: str(entry.get("idiom", "")))
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(path)

    def _explanation_dir(self, user_id: str) -> Path:
        return self.root / "idiom_explanations" / self._safe_component(user_id)

    def _index_path(self, user_id: str) -> Path:
        return self._explanation_dir(user_id) / "index.json"

    def _tts_dir(self, user_id: str) -> Path:
        return self._explanation_dir(user_id) / "tts"

    @staticmethod
    def _is_valid_index_entry(entry: object) -> bool:
        return (
            isinstance(entry, dict)
            and isinstance(entry.get("idiom"), str)
            and isinstance(entry.get("pinyin"), str)
            and isinstance(entry.get("explanation"), str)
            and isinstance(entry.get("story"), str)
            and isinstance(entry.get("source"), str)
            and isinstance(entry.get("created_at"), str)
            and isinstance(entry.get("updated_at"), str)
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-一-鿿]+", "_", value).strip("_") or "item"
