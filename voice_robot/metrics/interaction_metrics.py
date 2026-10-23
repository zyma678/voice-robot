import json
import math
import re
import threading
from datetime import UTC, datetime
from pathlib import Path


class InteractionMetrics:
    """Aggregate ASR/TTS usage and stage latency from durable turn audit events."""

    _RELEVANT_EVENT_TYPES = {
        "asr_result",
        "tts_start",
        "tts_generation",
        "tts_prepare_error",
    }
    _EVENT_CACHE: dict[Path, tuple[int, int, list[dict]]] = {}
    _EVENT_CACHE_LOCK = threading.Lock()
    _EVENT_CACHE_MAX_PATHS = 64

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def aggregate(self, *, days: int = 7) -> dict:
        min_day = datetime.now(UTC).date().toordinal() - (max(1, days) - 1)
        asr_latencies: list[int] = []
        tts_prepare_latencies: list[int] = []
        asr_calls = 0
        asr_audio_ms = 0
        billable_asr_audio_ms = 0
        asr_legacy_events_skipped = 0
        tts_attempts = 0
        tts_playback_calls = 0
        tts_generation_calls = 0
        tts_failures = 0
        tts_legacy_events_skipped = 0
        tts_cache_hits = 0
        tts_synthesized_chars = 0
        tts_cache_saved_chars = 0
        billable_tts_chars = 0
        unpriced_failed_tts_chars = 0

        for event in self._events():
            if self._day_ordinal(str(event.get("created_at", ""))) < min_day:
                continue
            event_type = event.get("event_type")
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if event_type == "asr_result":
                if not self._is_v1_asr(payload):
                    asr_legacy_events_skipped += 1
                    continue
                audio_ms = self._nonnegative_int(payload.get("audio_duration_ms"))
                latency_ms = self._nonnegative_int(payload.get("latency_ms"))
                asr_calls += 1
                asr_audio_ms += audio_ms
                if payload.get("provider") == "volc":
                    billable_asr_audio_ms += audio_ms
                asr_latencies.append(latency_ms)
            elif event_type == "tts_start":
                if not self._is_v1_tts_start(payload):
                    tts_legacy_events_skipped += 1
                    continue
                chars = self._nonnegative_int(payload.get("characters"))
                prepare_ms = self._nonnegative_int(payload.get("prepare_latency_ms"))
                cache_hit = payload.get("cache_hit") is True
                tts_attempts += 1
                tts_playback_calls += 1
                tts_prepare_latencies.append(prepare_ms)
                if cache_hit:
                    tts_cache_hits += 1
                    # Local debug-tone reuse saves latency/disk work but no cloud bill.
                    if payload.get("provider") == "volc":
                        tts_cache_saved_chars += chars
                else:
                    tts_generation_calls += 1
                    tts_synthesized_chars += chars
                    if payload.get("provider") == "volc":
                        billable_tts_chars += chars
            elif event_type == "tts_generation":
                if not self._is_v1_tts_generation(payload):
                    tts_legacy_events_skipped += 1
                    continue
                chars = self._nonnegative_int(payload.get("characters"))
                latency_ms = self._nonnegative_int(payload.get("latency_ms"))
                tts_attempts += 1
                tts_prepare_latencies.append(latency_ms)
                if payload.get("success") is True:
                    tts_generation_calls += 1
                    tts_synthesized_chars += chars
                    if payload.get("provider") == "volc":
                        billable_tts_chars += chars
                else:
                    tts_failures += 1
                    if payload.get("provider") == "volc":
                        # Providers differ on whether an accepted failed request is
                        # billed. Keep it visible without pretending it is zero or
                        # adding it to the deterministic estimate.
                        unpriced_failed_tts_chars += chars
            elif event_type == "tts_prepare_error":
                if not self._is_v1_tts_prepare_error(payload):
                    tts_legacy_events_skipped += 1
                    continue
                chars = self._nonnegative_int(payload.get("characters"))
                tts_attempts += 1
                tts_failures += 1
                tts_prepare_latencies.append(
                    self._nonnegative_int(payload.get("prepare_latency_ms"))
                )
                if payload.get("provider") == "volc":
                    unpriced_failed_tts_chars += chars

        return {
            "days": max(1, int(days)),
            "asr": {
                "calls": asr_calls,
                "audio_ms": asr_audio_ms,
                "audio_seconds": round(asr_audio_ms / 1000, 3),
                "billable_audio_ms": billable_asr_audio_ms,
                "billable_audio_seconds": round(billable_asr_audio_ms / 1000, 3),
                "legacy_events_skipped": asr_legacy_events_skipped,
                **self._latency_summary(asr_latencies),
            },
            "tts": {
                "calls": tts_attempts,
                "playback_calls": tts_playback_calls,
                "generation_calls": tts_generation_calls,
                "failures": tts_failures,
                "cache_hits": tts_cache_hits,
                "cache_hit_rate": (
                    round(tts_cache_hits * 100 / tts_playback_calls, 2)
                    if tts_playback_calls
                    else 0.0
                ),
                "synthesized_chars": tts_synthesized_chars,
                "cache_saved_chars": tts_cache_saved_chars,
                "billable_chars": billable_tts_chars,
                "unpriced_failed_chars": unpriced_failed_tts_chars,
                "legacy_events_skipped": tts_legacy_events_skipped,
                **self._latency_summary(tts_prepare_latencies),
            },
        }

    @staticmethod
    def _is_v1_asr(payload: dict) -> bool:
        return (
            payload.get("metrics_version") == 1
            and payload.get("provider") in {"local", "volc"}
            and type(payload.get("audio_duration_ms")) is int
            and type(payload.get("latency_ms")) is int
        )

    @staticmethod
    def _is_v1_tts_start(payload: dict) -> bool:
        return (
            payload.get("metrics_version") == 1
            and payload.get("provider") in {"local", "volc"}
            and type(payload.get("characters")) is int
            and type(payload.get("cache_hit")) is bool
            and type(payload.get("prepare_latency_ms")) is int
        )

    @staticmethod
    def _is_v1_tts_generation(payload: dict) -> bool:
        return (
            payload.get("metrics_version") == 1
            and payload.get("provider") in {"local", "volc"}
            and type(payload.get("characters")) is int
            and type(payload.get("success")) is bool
            and type(payload.get("latency_ms")) is int
        )

    @staticmethod
    def _is_v1_tts_prepare_error(payload: dict) -> bool:
        return (
            payload.get("metrics_version") == 1
            and payload.get("provider") in {"local", "volc"}
            and type(payload.get("characters")) is int
            and type(payload.get("prepare_latency_ms")) is int
        )

    def _events(self):
        audit_root = self.root / "audit"
        if not audit_root.exists():
            return
        for path in audit_root.glob("*/events.jsonl"):
            if not path.is_file():
                continue
            for event in self._events_for_path(path):
                yield event

    @classmethod
    def _events_for_path(cls, path: Path) -> list[dict]:
        try:
            stat = path.stat()
        except OSError:
            return []
        resolved = path.resolve()
        with cls._EVENT_CACHE_LOCK:
            cached = cls._EVENT_CACHE.get(resolved)
            if (
                cached is not None
                and cached[0] == stat.st_mtime_ns
                and cached[1] == stat.st_size
            ):
                return cached[2]

        events: list[dict] = []
        try:
            file = path.open("r", encoding="utf-8")
        except OSError:
            return []
        with file:
            for line in file:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(event, dict)
                    and event.get("event_type") in cls._RELEVANT_EVENT_TYPES
                ):
                    events.append(event)

        with cls._EVENT_CACHE_LOCK:
            if len(cls._EVENT_CACHE) >= cls._EVENT_CACHE_MAX_PATHS:
                cls._EVENT_CACHE.pop(next(iter(cls._EVENT_CACHE)))
            cls._EVENT_CACHE[resolved] = (stat.st_mtime_ns, stat.st_size, events)
        return events

    @staticmethod
    def _latency_summary(values: list[int]) -> dict:
        ordered = sorted(values)
        if not ordered:
            return {"latency_samples": 0, "avg_latency_ms": 0.0, "p95_latency_ms": 0}
        return {
            "latency_samples": len(ordered),
            "avg_latency_ms": round(sum(ordered) / len(ordered), 2),
            "p95_latency_ms": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
        }

    @staticmethod
    def _nonnegative_int(value) -> int:
        return max(0, value) if type(value) is int else 0

    @staticmethod
    def _day_ordinal(value: str) -> int:
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value)
        if not match:
            return -1
        try:
            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                tzinfo=UTC,
            ).date().toordinal()
        except ValueError:
            return -1
