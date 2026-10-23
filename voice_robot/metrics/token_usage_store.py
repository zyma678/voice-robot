import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class TokenUsageStore:
    """Append-only token usage events and lightweight aggregations."""

    _READ_CACHE: dict[Path, tuple[int, int, list[dict]]] = {}
    _READ_CACHE_MAX_PATHS = 16

    _REQUIRED_FIELDS = {
        "event_id",
        "created_at",
        "user_id",
        "scenario",
        "vendor",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def append_event(
        self,
        *,
        user_id: str,
        scenario: str,
        vendor: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        success: bool = True,
        request_kind: str = "unknown",
        request_max_tokens: int | None = None,
        latency_ms: int | None = None,
        prompt_chars: int = 0,
        completion_chars: int = 0,
        error_code: str = "",
        extra: dict | None = None,
    ) -> dict:
        event = {
            "event_id": f"tok_{uuid4().hex[:10]}",
            "created_at": self._now(),
            "user_id": str(user_id),
            "scenario": str(scenario),
            "vendor": str(vendor),
            "model": str(model),
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "total_tokens": int(total_tokens),
            "success": bool(success),
            "request_kind": str(request_kind),
            "prompt_chars": max(0, int(prompt_chars)),
            "completion_chars": max(0, int(completion_chars)),
        }
        if latency_ms is not None:
            event["latency_ms"] = max(0, int(latency_ms))
        if request_max_tokens is not None:
            event["request_max_tokens"] = max(0, int(request_max_tokens))
        if error_code:
            event["error_code"] = str(error_code)
        if isinstance(extra, dict) and extra:
            event["extra"] = extra

        path = self._events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._READ_CACHE.pop(path.resolve(), None)
        return event

    def list_events(self, *, limit: int = 200) -> list[dict]:
        if limit <= 0:
            return []
        events = self._read_events()
        return events[-limit:]

    def aggregate(
        self,
        *,
        group_by: list[str] | None = None,
        days: int = 7,
    ) -> dict:
        normalized_group_by = [str(item).strip() for item in (group_by or []) if str(item).strip()]
        allowed = {"user_id", "scenario", "vendor", "model", "request_kind"}
        normalized_group_by = [item for item in normalized_group_by if item in allowed]

        events = self._read_events()
        if days > 0:
            min_day = (datetime.now(UTC).date()).toordinal() - (days - 1)
            filtered = []
            for event in events:
                created_at = str(event.get("created_at", ""))
                day = self._to_day_ordinal(created_at)
                if day is None or day < min_day:
                    continue
                filtered.append(event)
            events = filtered

        totals = self._sum_events(events)

        buckets: dict[tuple, dict] = {}
        for event in events:
            key = tuple(str(event.get(field, "")) for field in normalized_group_by)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = {
                    **{
                        field: str(event.get(field, ""))
                        for field in normalized_group_by
                    },
                    "_metrics": self._empty_metrics(),
                }
                buckets[key] = bucket
            self._accumulate_metrics(bucket["_metrics"], event)

        rows = [
            {
                **{field: bucket[field] for field in normalized_group_by},
                **self._finalize_metrics(bucket["_metrics"]),
            }
            for bucket in buckets.values()
        ]
        rows.sort(key=lambda row: row.get("total_tokens", 0), reverse=True)

        return {
            "days": int(days),
            "group_by": normalized_group_by,
            # Keep the pricing UI honest when runtime configuration changes:
            # one global input/output price cannot represent several Ark models.
            # Local-rule/fallback rows are intentionally excluded because they
            # have no provider token price.
            "models": sorted(
                {
                    str(event.get("model", "")).strip()
                    for event in events
                    if str(event.get("vendor", "")).strip() == "ark"
                    and str(event.get("model", "")).strip()
                }
            ),
            "total": totals,
            "rows": rows,
        }

    def daily_breakdown(self, *, days: int = 7) -> dict:
        """Per-day + per-scenario aggregation for charts."""
        events = self._read_events()
        today = datetime.now(UTC).date()
        min_ord = today.toordinal() - (days - 1)

        by_day: dict[str, dict] = {}
        by_scenario: dict[str, dict] = {}

        for event in events:
            created_at = str(event.get("created_at", ""))
            ord_val = self._to_day_ordinal(created_at)
            if ord_val is None or ord_val < min_ord:
                continue
            match = re.match(r"^(\d{4}-\d{2}-\d{2})", created_at)
            day_str = match.group(1) if match else "unknown"

            if day_str not in by_day:
                by_day[day_str] = self._empty_metrics()
            self._accumulate_metrics(by_day[day_str], event)

            scenario = str(event.get("scenario", "unknown"))
            if scenario not in by_scenario:
                by_scenario[scenario] = self._empty_metrics()
            self._accumulate_metrics(by_scenario[scenario], event)

        daily = [
            {"date": key, **self._finalize_metrics(metrics)}
            for key, metrics in sorted(by_day.items())
        ]
        scenarios = [
            {"scenario": key, **self._finalize_metrics(metrics)}
            for key, metrics in by_scenario.items()
        ]
        scenarios.sort(key=lambda item: item["total_tokens"], reverse=True)

        return {"days": days, "daily": daily, "by_scenario": scenarios}

    def _read_events(self) -> list[dict]:
        path = self._events_path()
        if not path.exists():
            return []
        resolved = path.resolve()
        try:
            stat = path.stat()
        except OSError:
            return []
        cached = self._READ_CACHE.get(resolved)
        if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
        events: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if self._is_valid_event(payload):
                events.append(payload)
        if len(self._READ_CACHE) >= self._READ_CACHE_MAX_PATHS:
            self._READ_CACHE.pop(next(iter(self._READ_CACHE)))
        self._READ_CACHE[resolved] = (stat.st_mtime_ns, stat.st_size, events)
        return events

    @classmethod
    def _is_valid_event(cls, event: object) -> bool:
        if not isinstance(event, dict):
            return False
        if not cls._REQUIRED_FIELDS.issubset(event):
            return False
        for key in ("event_id", "created_at", "user_id", "scenario", "vendor", "model"):
            if not isinstance(event.get(key), str):
                return False
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if type(event.get(key)) is not int:
                return False
        if "success" in event and type(event.get("success")) is not bool:
            return False
        for key in ("request_kind", "error_code"):
            if key in event and not isinstance(event.get(key), str):
                return False
        for key in (
            "latency_ms",
            "request_max_tokens",
            "prompt_chars",
            "completion_chars",
        ):
            if key in event and (
                type(event.get(key)) is not int or int(event.get(key, 0)) < 0
            ):
                return False
        return True

    @classmethod
    def _sum_events(cls, events: list[dict]) -> dict:
        totals = cls._empty_metrics()
        for event in events:
            cls._accumulate_metrics(totals, event)
        return cls._finalize_metrics(totals)

    @staticmethod
    def _empty_metrics() -> dict:
        return {
            "calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "provider_calls": 0,
            "provider_successful_calls": 0,
            "provider_failed_calls": 0,
            "local_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "prompt_chars": 0,
            "completion_chars": 0,
            "_latencies": [],
            "_provider_latencies": [],
        }

    @staticmethod
    def _accumulate_metrics(metrics: dict, event: dict) -> None:
        metrics["calls"] += 1
        if event.get("success", True) is False:
            metrics["failed_calls"] += 1
        else:
            # Legacy events did not carry a success flag and represented completed calls.
            metrics["successful_calls"] += 1
        if str(event.get("vendor", "")).strip() == "local":
            metrics["local_calls"] += 1
        else:
            metrics["provider_calls"] += 1
            if event.get("success", True) is False:
                metrics["provider_failed_calls"] += 1
            else:
                metrics["provider_successful_calls"] += 1
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_chars",
            "completion_chars",
        ):
            metrics[key] += int(event.get(key, 0))
        latency_ms = event.get("latency_ms")
        if type(latency_ms) is int and latency_ms >= 0:
            metrics["_latencies"].append(latency_ms)
            if str(event.get("vendor", "")).strip() != "local":
                metrics["_provider_latencies"].append(latency_ms)

    @staticmethod
    def _finalize_metrics(metrics: dict) -> dict:
        calls = int(metrics["calls"])
        successful_calls = int(metrics["successful_calls"])
        failed_calls = int(metrics["failed_calls"])
        provider_calls = int(metrics["provider_calls"])
        provider_successful_calls = int(metrics["provider_successful_calls"])
        provider_failed_calls = int(metrics["provider_failed_calls"])
        latencies = sorted(int(value) for value in metrics["_latencies"])
        provider_latencies = sorted(
            int(value) for value in metrics["_provider_latencies"]
        )
        avg_latency_ms = (
            round(sum(latencies) / len(latencies), 2) if latencies else 0.0
        )
        p95_latency_ms = (
            latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)]
            if latencies
            else 0
        )
        return {
            "calls": calls,
            "successful_calls": successful_calls,
            "failed_calls": failed_calls,
            "success_rate": round(successful_calls * 100 / calls, 2) if calls else 0.0,
            "provider_calls": provider_calls,
            "provider_successful_calls": provider_successful_calls,
            "provider_failed_calls": provider_failed_calls,
            "provider_success_rate": (
                round(provider_successful_calls * 100 / provider_calls, 2)
                if provider_calls
                else 0.0
            ),
            "local_calls": int(metrics["local_calls"]),
            "prompt_tokens": int(metrics["prompt_tokens"]),
            "completion_tokens": int(metrics["completion_tokens"]),
            "total_tokens": int(metrics["total_tokens"]),
            "prompt_chars": int(metrics["prompt_chars"]),
            "completion_chars": int(metrics["completion_chars"]),
            "latency_samples": len(latencies),
            "avg_latency_ms": avg_latency_ms,
            "p95_latency_ms": p95_latency_ms,
            "provider_latency_samples": len(provider_latencies),
            "provider_avg_latency_ms": (
                round(sum(provider_latencies) / len(provider_latencies), 2)
                if provider_latencies
                else 0.0
            ),
            "provider_p95_latency_ms": (
                provider_latencies[
                    max(0, math.ceil(len(provider_latencies) * 0.95) - 1)
                ]
                if provider_latencies
                else 0
            ),
            "avg_prompt_tokens": (
                round(int(metrics["prompt_tokens"]) / provider_calls, 2)
                if provider_calls
                else 0.0
            ),
        }

    def _events_path(self) -> Path:
        return self.root / "usage" / "events.jsonl"

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _to_day_ordinal(iso_text: str) -> int | None:
        # ISO 8601: "YYYY-MM-DD..."
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", iso_text)
        if not match:
            return None
        try:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
        except ValueError:
            return None
        try:
            return datetime(year, month, day, tzinfo=UTC).date().toordinal()
        except ValueError:
            return None
