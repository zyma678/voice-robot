import json

from voice_robot.metrics.token_usage_store import TokenUsageStore


def test_token_usage_store_aggregates_by_group(tmp_path) -> None:
    store = TokenUsageStore(tmp_path)
    store.append_event(
        user_id="u1",
        scenario="chat",
        vendor="ark",
        model="m1",
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        success=True,
        request_kind="generate_reply",
        latency_ms=100,
        prompt_chars=40,
        completion_chars=20,
    )
    store.append_event(
        user_id="u1",
        scenario="ws",
        vendor="ark",
        model="m1",
        prompt_tokens=5,
        completion_tokens=5,
        total_tokens=10,
        success=False,
        request_kind="generate_reply",
        latency_ms=300,
        prompt_chars=30,
        completion_chars=0,
        error_code="llm_rate_limited",
    )
    store.append_event(
        user_id="u2",
        scenario="chat",
        vendor="local",
        model="fallback-local",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
    )

    aggregated = store.aggregate(group_by=["user_id", "scenario", "vendor"], days=90)

    assert aggregated["total"]["calls"] == 3
    assert aggregated["total"]["successful_calls"] == 2
    assert aggregated["total"]["failed_calls"] == 1
    assert aggregated["total"]["success_rate"] == 66.67
    assert aggregated["total"]["provider_calls"] == 2
    assert aggregated["total"]["provider_successful_calls"] == 1
    assert aggregated["total"]["provider_failed_calls"] == 1
    assert aggregated["total"]["provider_success_rate"] == 50.0
    assert aggregated["total"]["local_calls"] == 1
    assert aggregated["total"]["latency_samples"] == 2
    assert aggregated["total"]["avg_latency_ms"] == 200.0
    assert aggregated["total"]["p95_latency_ms"] == 300
    assert aggregated["total"]["avg_prompt_tokens"] == 7.5
    assert aggregated["total"]["prompt_chars"] == 70
    assert aggregated["total"]["completion_chars"] == 20
    assert aggregated["total"]["total_tokens"] == 40
    assert aggregated["group_by"] == ["user_id", "scenario", "vendor"]
    assert aggregated["models"] == ["m1"]
    rows = aggregated["rows"]
    assert any(
        row["user_id"] == "u1"
        and row["scenario"] == "chat"
        and row["vendor"] == "ark"
        and row["total_tokens"] == 30
        for row in rows
    )


def test_token_usage_store_keeps_legacy_events_compatible(tmp_path) -> None:
    path = tmp_path / "usage" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "event_id": "tok_legacy",
                "created_at": TokenUsageStore._now(),
                "user_id": "u1",
                "scenario": "chat",
                "vendor": "ark",
                "model": "m1",
                "prompt_tokens": 8,
                "completion_tokens": 2,
                "total_tokens": 10,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    aggregated = TokenUsageStore(tmp_path).aggregate(
        group_by=["request_kind"], days=90
    )

    assert aggregated["total"]["calls"] == 1
    assert aggregated["total"]["successful_calls"] == 1
    assert aggregated["total"]["failed_calls"] == 0
    assert aggregated["total"]["avg_prompt_tokens"] == 8.0
    assert aggregated["total"]["latency_samples"] == 0
    assert aggregated["rows"][0]["request_kind"] == ""


def test_token_usage_read_cache_invalidates_after_append(tmp_path) -> None:
    store = TokenUsageStore(tmp_path)
    assert store.aggregate(days=90)["total"]["calls"] == 0

    store.append_event(
        user_id="u1",
        scenario="chat",
        vendor="ark",
        model="m1",
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
    )

    assert store.aggregate(days=90)["total"]["calls"] == 1
    assert store.daily_breakdown(days=90)["daily"][0]["total_tokens"] == 3
