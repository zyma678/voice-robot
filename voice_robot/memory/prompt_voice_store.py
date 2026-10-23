from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path


def prompt_text_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


class PromptVoiceCacheStore:
    """持久化缓存场景固定语音提示（进入/退出）的 TTS 音频，避免每次进出场景
    重复消耗 TTS Token。镜像 `VocabExplanationStore` 的 sidecar `.pcm` + 容错读 /
    原子写模式：JSON 索引只存相对路径与缓存键，不塞 base64。

    缓存命中需 `text_hash + voice_type + sample_rate_hz + encoding` 全一致——文案、
    音色、采样率或编码任一变化都会 miss 并触发重合成（杜绝「改了文案还放旧音」）。
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def get_cached(
        self,
        scene_id: str,
        kind: str,
        *,
        text_hash: str,
        voice_type: str,
        sample_rate_hz: int,
        encoding: str,
    ) -> tuple[bytes, int] | None:
        entry = self._get_entry(scene_id, kind)
        if not entry:
            return None
        if (
            entry.get("text_hash") == text_hash
            and entry.get("voice_type") == voice_type
            and entry.get("sample_rate_hz") == int(sample_rate_hz)
            and entry.get("encoding") == encoding
        ):
            return self._read_audio(entry)
        return None

    def store(
        self,
        scene_id: str,
        kind: str,
        *,
        text_hash: str,
        voice_type: str,
        sample_rate_hz: int,
        encoding: str,
        audio: bytes,
    ) -> None:
        if not audio:
            return
        entries = self._read_index()
        key = self._key(scene_id, kind)
        rel_path = self._write_audio(scene_id, kind, audio)
        now = datetime.now(UTC).isoformat()
        entries = [e for e in entries if e.get("key") != key]
        entries.append(
            {
                "key": key,
                "scene_id": scene_id,
                "kind": kind,
                "text_hash": text_hash,
                "voice_type": voice_type,
                "sample_rate_hz": int(sample_rate_hz),
                "encoding": encoding,
                "tts_path": rel_path,
                "byte_length": len(audio),
                "updated_at": now,
            }
        )
        self._write_index(entries)

    # --- internals -----------------------------------------------------------

    @staticmethod
    def _key(scene_id: str, kind: str) -> str:
        return f"{scene_id}:{kind}"

    def _get_entry(self, scene_id: str, kind: str) -> dict | None:
        key = self._key(scene_id, kind)
        for entry in self._read_index():
            if entry.get("key") == key:
                return entry
        return None

    def _read_audio(self, entry: dict) -> tuple[bytes, int] | None:
        rel_path = entry.get("tts_path")
        sample_rate_hz = entry.get("sample_rate_hz")
        if not isinstance(rel_path, str) or type(sample_rate_hz) is not int:
            return None
        root = self.root.resolve()
        expected_root = self._tts_dir().resolve()
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

    def _write_audio(self, scene_id: str, kind: str, audio: bytes) -> str:
        tts_dir = self._tts_dir()
        tts_dir.mkdir(parents=True, exist_ok=True)
        name = f"{self._safe_component(scene_id)}-{self._safe_component(kind)}.pcm"
        path = tts_dir / name
        temp_path = path.with_suffix(".pcm.tmp")
        temp_path.write_bytes(audio)
        temp_path.replace(path)
        return path.relative_to(self.root).as_posix()

    def _read_index(self) -> list[dict]:
        path = self._index_path()
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return [entry for entry in payload if isinstance(entry, dict) and entry.get("key")]

    def _write_index(self, entries: list[dict]) -> None:
        path = self._index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = sorted(entries, key=lambda entry: str(entry.get("key", "")))
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(path)

    def _base_dir(self) -> Path:
        return self.root / "prompt_voice"

    def _index_path(self) -> Path:
        return self._base_dir() / "index.json"

    def _tts_dir(self) -> Path:
        return self._base_dir() / "tts"

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-一-鿿]+", "_", value).strip("_") or "item"
