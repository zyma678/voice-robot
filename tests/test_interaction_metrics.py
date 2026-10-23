import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from voice_robot.config import get_settings
from voice_robot.metrics.interaction_metrics import InteractionMetrics
from voice_robot.server import app


def _event(event_type: str, payload: dict) -> dict:
    return {
        "event_id": event_type,
        "created_at": datetime.now(UTC).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }


def test_interaction_metrics_aggregates_asr_tts_cost_units(tmp_path) -> None:
    path = tmp_path / "audit" / "u1" / "events.jsonl"
    path.parent.mkdir(parents=True)
    events = [
        _event(
            "asr_result",
            {
                "metrics_version": 1,
                "provider": "volc",
                "audio_duration_ms": 3200,
                "latency_ms": 180,
            },
        ),
        _event(
            "tts_start",
            {
                "metrics_version": 1,
                "provider": "volc",
                "characters": 20,
                "cache_hit": False,
                "prepare_latency_ms": 900,
            },
        ),
        _event(
            "tts_start",
            {
                "metrics_version": 1,
                "provider": "volc",
                "characters": 20,
                "cache_hit": True,
                "prepare_latency_ms": 2,
            },
        ),
        _event(
            "tts_start",
            {
                "metrics_version": 1,
                "provider": "local",
                "characters": 10,
                "cache_hit": False,
                "prepare_latency_ms": 1,
            },
        ),
    ]
    path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )

    result = InteractionMetrics(tmp_path).aggregate(days=7)

    assert result["asr"]["calls"] == 1
    assert result["asr"]["billable_audio_seconds"] == 3.2
    assert result["asr"]["p95_latency_ms"] == 180
    assert result["tts"]["calls"] == 3
    assert result["tts"]["playback_calls"] == 3
    assert result["tts"]["generation_calls"] == 2
    assert result["tts"]["cache_hits"] == 1
    assert result["tts"]["cache_hit_rate"] == 33.33
    assert result["tts"]["synthesized_chars"] == 30
    assert result["tts"]["billable_chars"] == 20
    assert result["tts"]["cache_saved_chars"] == 20
    assert result["tts"]["p95_latency_ms"] == 900


def test_interaction_metrics_endpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    path = tmp_path / "audit" / "u1" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            _event(
                "tts_start",
                {
                    "metrics_version": 1,
                    "provider": "volc",
                    "characters": 12,
                    "cache_hit": False,
                    "prepare_latency_ms": 40,
                },
            ),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    response = TestClient(app).get("/api/metrics/interactions?days=7")

    assert response.status_code == 200
    assert response.json()["tts"]["billable_chars"] == 12


def test_interaction_metrics_skips_legacy_zero_filled_events(tmp_path) -> None:
    path = tmp_path / "audit" / "u1" / "events.jsonl"
    path.parent.mkdir(parents=True)
    events = [
        _event("asr_result", {"text": "旧版事件"}),
        _event("tts_start", {"source": "real"}),
        _event(
            "tts_generation",
            {
                "metrics_version": 1,
                "provider": "volc",
                "characters": 16,
                "success": True,
                "latency_ms": 80,
            },
        ),
        _event(
            "tts_prepare_error",
            {
                "metrics_version": 1,
                "provider": "volc",
                "characters": 9,
                "prepare_latency_ms": 120,
            },
        ),
    ]
    path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )

    result = InteractionMetrics(tmp_path).aggregate(days=7)

    assert result["asr"]["calls"] == 0
    assert result["asr"]["legacy_events_skipped"] == 1
    assert result["tts"]["calls"] == 2
    assert result["tts"]["generation_calls"] == 1
    assert result["tts"]["failures"] == 1
    assert result["tts"]["billable_chars"] == 16
    assert result["tts"]["unpriced_failed_chars"] == 9
    assert result["tts"]["legacy_events_skipped"] == 1
