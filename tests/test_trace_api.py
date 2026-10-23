import json

import pytest
from fastapi.testclient import TestClient

from voice_robot import server
from voice_robot.config import get_settings
from voice_robot.server import app


@pytest.fixture(autouse=True)
def _isolate_speech_runtime(monkeypatch, tmp_path) -> None:
    """Keep API tests off the developer's configured cloud speech account."""
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()


def test_trace_preview_returns_payload_for_supported_hanzi(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.get("/api/trace/preview", params={"character": "十"})

    assert response.status_code == 200
    data = response.json()
    assert data["available"] is True
    assert data["payload"]["character"] == "十"
    assert len(data["payload"]["strokes"]) == 2


def test_trace_preview_returns_unavailable_for_missing_hanzi(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.get("/api/trace/preview", params={"character": "龘"})

    assert response.status_code == 200
    data = response.json()
    assert data["available"] is False
    assert data["payload"] is None


def test_trace_preview_rejects_empty_character(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.get("/api/trace/preview", params={"character": ""})

    assert response.status_code == 400


def test_device_trace_rejects_empty_character(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.post("/api/devices/ghost/trace", json={"character": ""})

    assert response.status_code == 400


def test_device_trace_requires_online_device(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.post("/api/devices/ghost/trace", json={"character": "十"})

    assert response.status_code == 409


def test_device_trace_websocket_message_includes_top_level_payload_for_firmware(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)
    token = get_settings().auth_token

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "esp32-trace", "token": token})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        response = client.post("/api/devices/esp32-trace/trace", json={"character": "十"})

        assert response.status_code == 200
        event = ws.receive_json()
        assert event["type"] == "show_character_trace"
        assert event["character"] == "十"
        assert event["strokes"]
        assert event["payload"]["character"] == "十"


def test_vocab_explanation_rejects_non_single_hanzi(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    assert client.post("/api/vocab/explanation", json={"character": ""}).status_code == 400
    assert client.post("/api/vocab/explanation", json={"character": "ab"}).status_code == 400
    assert client.post("/api/vocab/explanation", json={"character": "十一"}).status_code == 400


def test_vocab_explanation_uses_llm_output(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()

    class _FakeLLM:
        async def generate_freeform(
            self, *, system_prompt, user_prompt, max_tokens=320, **kwargs
        ):
            return '{"word":"十分","explanation":"先写一横再写一竖，就是十字。"}'

    monkeypatch.setattr(server, "_build_runtime_llm_service", lambda: _FakeLLM())
    client = TestClient(app)

    response = client.post("/api/vocab/explanation", json={"character": "十"})

    assert response.status_code == 200
    data = response.json()
    assert data["character"] == "十"
    assert data["word"] == "十分"
    assert "十字" in data["explanation"]
    assert data["source"] == "ark"


def test_vocab_explanation_falls_back_when_llm_unavailable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()

    def _boom():
        raise RuntimeError("no api key")

    monkeypatch.setattr(server, "_build_runtime_llm_service", _boom)
    client = TestClient(app)

    response = client.post("/api/vocab/explanation", json={"character": "十"})

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "fallback"
    assert "十" in data["explanation"]
    assert "detail" in data


def test_vocab_explanation_generates_and_caches_tts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()

    class _FakeLLM:
        async def generate_freeform(
            self, *, system_prompt, user_prompt, max_tokens=320, **kwargs
        ):
            return '{"word":"十分","explanation":"先写一横，再写一竖，就是十字。"}'

    monkeypatch.setattr(server, "_build_runtime_llm_service", lambda: _FakeLLM())
    client = TestClient(app)

    # Generate -> response advertises cached TTS (LocalTTSService placeholder tone).
    response = client.post("/api/vocab/explanation", json={"character": "十"})
    assert response.status_code == 200
    data = response.json()
    assert data["has_tts"] is True
    assert data["tts_url"]
    assert data["tts_sample_rate_hz"]

    # The cached audio is retrievable as raw PCM (audio/L16).
    audio_resp = client.get(data["tts_url"])
    assert audio_resp.status_code == 200
    assert audio_resp.headers["content-type"].startswith("audio/L16")
    assert audio_resp.headers["X-TTS-Audio-Format"] == "pcm_s16le"
    assert len(audio_resp.content) > 0

    # And it survives as a listed record for a fresh session.
    list_resp = client.get("/api/vocab/explanations")
    assert list_resp.status_code == 200
    items = list_resp.json()["explanations"]
    record = next(item for item in items if item["character"] == "十")
    assert record["word"] == "十分"
    assert record["has_tts"] is True
    assert record["tts_url"]


def test_vocab_tts_audio_404_when_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    assert client.get("/api/vocab/十/tts").status_code == 404
    assert client.get("/api/vocab/ab/tts").status_code == 400


def test_vocab_explanations_list_empty_initially(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.get("/api/vocab/explanations")
    assert response.status_code == 200
    assert response.json() == {"explanations": []}


def test_vocab_explanations_list_ordered_by_entry_order(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()

    # Seed the store directly with distinct created_at to assert ordering.
    from voice_robot.memory.vocab_explanation_store import VocabExplanationStore

    store = VocabExplanationStore(tmp_path)
    for ch in ("三", "一", "二"):
        store.upsert(
            server.VOCAB_EXPLANATION_USER_ID,
            character=ch,
            word=f"{ch}个",
            explanation=f"{ch}的解读。",
            source="ark",
        )
    # Force created_at to reflect entry order 三 -> 一 -> 二.
    index_path = tmp_path / "vocab_explanations" / "console-vocab" / "index.json"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    order = {"三": "2026-06-19T00:00:01", "一": "2026-06-19T00:00:02", "二": "2026-06-19T00:00:03"}
    for entry in data:
        entry["created_at"] = order[entry["character"]]
    index_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    client = TestClient(app)
    response = client.get("/api/vocab/explanations")
    assert response.status_code == 200
    chars = [item["character"] for item in response.json()["explanations"]]
    assert chars == ["三", "一", "二"]


def test_device_trace_say_validates_inputs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    assert (
        client.post("/api/devices/ghost/trace-say", json={"character": "", "text": "x"}).status_code
        == 400
    )
    assert (
        client.post("/api/devices/ghost/trace-say", json={"character": "十", "text": ""}).status_code
        == 400
    )
    # Valid inputs but device offline -> 409.
    assert (
        client.post(
            "/api/devices/ghost/trace-say", json={"character": "十", "text": "先一横"}
        ).status_code
        == 409
    )


def test_device_trace_say_speaks_text_and_shows_trace(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)
    token = get_settings().auth_token

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "esp32-say", "token": token})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        response = client.post(
            "/api/devices/esp32-say/trace-say",
            json={"character": "十", "text": "先写一横，再写一竖。"},
        )
        assert response.status_code == 200

        # The pre-generated text is spoken verbatim (no LLM) and the trace is shown.
        types = []
        reply_text = None
        for _ in range(40):
            event = ws.receive_json()
            types.append(event.get("type"))
            if event.get("type") == "llm_reply_end":
                reply_text = event.get("text")
            if event.get("type") == "show_character_trace":
                assert event["character"] == "十"
                break
        assert "show_character_trace" in types
        assert reply_text == "先写一横，再写一竖。"
