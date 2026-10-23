from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class TTSResponseCacheStore:
    """Bounded, content-addressed cache for synthesized reply PCM."""

    CACHE_SCHEMA_VERSION = 2

    def __init__(
        self,
        root: Path | str,
        *,
        max_entries: int = 256,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.root = Path(root)
        self.max_entries = max(0, int(max_entries))
        self.max_bytes = max(0, int(max_bytes))

    def get(
        self,
        text: str,
        *,
        provider: str,
        resource_id: str,
        voice_type: str,
        sample_rate_hz: int,
        encoding: str,
        configuration_fingerprint: str = "",
    ) -> bytes | None:
        key = self.cache_key(
            text,
            provider=provider,
            resource_id=resource_id,
            voice_type=voice_type,
            sample_rate_hz=sample_rate_hz,
            encoding=encoding,
            configuration_fingerprint=configuration_fingerprint,
        )
        path = self._audio_path(key)
        if not path.exists() or not path.is_file():
            return None
        try:
            audio = path.read_bytes()
        except OSError:
            return None
        if not audio:
            return None
        try:
            metadata = json.loads(self._metadata_path(key).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (
            not isinstance(metadata, dict)
            or metadata.get("key") != key
            or metadata.get("cache_schema_version") != self.CACHE_SCHEMA_VERSION
            or metadata.get("byte_length") != len(audio)
            or metadata.get("audio_sha256") != hashlib.sha256(audio).hexdigest()
        ):
            return None
        try:
            os.utime(path, None)
        except OSError:
            pass
        return audio

    def put(
        self,
        text: str,
        *,
        provider: str,
        resource_id: str,
        voice_type: str,
        sample_rate_hz: int,
        encoding: str,
        audio: bytes,
        configuration_fingerprint: str = "",
    ) -> str | None:
        if (
            not text.strip()
            or not isinstance(audio, (bytes, bytearray))
            or not audio
            or self.max_entries <= 0
            or self.max_bytes <= 0
            or len(audio) > self.max_bytes
        ):
            return None
        key = self.cache_key(
            text,
            provider=provider,
            resource_id=resource_id,
            voice_type=voice_type,
            sample_rate_hz=sample_rate_hz,
            encoding=encoding,
            configuration_fingerprint=configuration_fingerprint,
        )
        directory = self._cache_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = self._audio_path(key)
        self._atomic_write_bytes(path, bytes(audio))
        self._atomic_write_json(
            self._metadata_path(key),
            {
                "key": key,
                "provider": provider,
                "resource_id": resource_id,
                "voice_type": voice_type,
                "sample_rate_hz": int(sample_rate_hz),
                "encoding": encoding,
                "cache_schema_version": self.CACHE_SCHEMA_VERSION,
                "configuration_fingerprint": configuration_fingerprint,
                "text_chars": len(text),
                "byte_length": len(audio),
                "audio_sha256": hashlib.sha256(audio).hexdigest(),
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
        self._prune()
        return key if path.exists() else None

    def stats(self) -> dict[str, int]:
        files = self._audio_files()
        return {
            "entries": len(files),
            "bytes": sum(self._safe_size(path) for path in files),
            "max_entries": self.max_entries,
            "max_bytes": self.max_bytes,
        }

    @staticmethod
    def cache_key(
        text: str,
        *,
        provider: str,
        resource_id: str,
        voice_type: str,
        sample_rate_hz: int,
        encoding: str,
        configuration_fingerprint: str = "",
    ) -> str:
        identity = json.dumps(
            {
                "cache_schema_version": TTSResponseCacheStore.CACHE_SCHEMA_VERSION,
                "text": text,
                "provider": provider,
                "resource_id": resource_id,
                "voice_type": voice_type,
                "sample_rate_hz": int(sample_rate_hz),
                "encoding": encoding,
                "configuration_fingerprint": configuration_fingerprint,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(identity).hexdigest()

    def _prune(self) -> None:
        files = self._audio_files()
        total = sum(self._safe_size(path) for path in files)
        if len(files) <= self.max_entries and total <= self.max_bytes:
            return
        files.sort(key=self._safe_mtime_ns)
        while files and (len(files) > self.max_entries or total > self.max_bytes):
            path = files.pop(0)
            total -= self._safe_size(path)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            try:
                path.with_suffix(".json").unlink()
            except FileNotFoundError:
                pass

    def _audio_files(self) -> list[Path]:
        directory = self._cache_dir()
        if not directory.exists():
            return []
        return [path for path in directory.glob("*.pcm") if path.is_file()]

    def _cache_dir(self) -> Path:
        return self.root / "tts_response_cache"

    def _audio_path(self, key: str) -> Path:
        return self._cache_dir() / f"{key}.pcm"

    def _metadata_path(self, key: str) -> Path:
        return self._cache_dir() / f"{key}.json"

    @staticmethod
    def _safe_size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    @staticmethod
    def _safe_mtime_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    @staticmethod
    def _atomic_write_bytes(path: Path, payload: bytes) -> None:
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temp.write_bytes(payload)
            temp.replace(path)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temp.replace(path)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
