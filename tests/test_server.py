import json

from fastapi.testclient import TestClient

from voice_robot.agent.state import AgentResult
from voice_robot.config import get_settings
import voice_robot.server as server_mod
from voice_robot.server import app


def receive_tts_audio_and_end(ws) -> tuple[list[bytes], dict]:
    audio_chunks: list[bytes] = []
    while True:
        message = ws.receive()
        if message.get("bytes") is not None:
            audio_chunks.append(message["bytes"])
            continue
        event = json.loads(message["text"])
        assert event["type"] == "tts_end"
        return audio_chunks, event


def test_health_returns_ok() -> None:
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}


def test_websocket_text_audio_closed_loop(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes("讲一个恐龙故事".encode("utf-8"))
        ws.send_json({"type": "audio_end"})

        asr_result = ws.receive_json()
        assert asr_result["type"] == "asr_result"
        assert asr_result["text"] == "讲一个恐龙故事"
        assert asr_result["latency_ms"] >= 0
        prompt_event = ws.receive_json()
        assert prompt_event["type"] == "llm_prompt"
        assert prompt_event["payload"]["memory_block"]
        stream_start = ws.receive_json()
        assert stream_start["type"] == "llm_reply_start"
        assert stream_start["source"] in {"ark", "fallback-local", "local-rule"}
        deltas = []
        while True:
            event = ws.receive_json()
            if event["type"] == "llm_reply_end":
                stream_end = event
                break
            assert event["type"] == "llm_reply_delta"
            assert event["text"]
            deltas.append(event["text"])
        assert stream_end["text"] == "".join(deltas)
        reply = ws.receive_json()
        assert reply["type"] == "llm_reply"
        assert reply["text"]
        assert reply["source"] in {"ark", "fallback-local", "local-rule"}
        assert "detail" not in reply or isinstance(reply["detail"], str)
        assert ws.receive_json().get("type") == "tts_start"
        audio_chunks, end_event = receive_tts_audio_and_end(ws)
        assert b"".join(audio_chunks)
        assert end_event["type"] == "tts_end"
        assert end_event.get("reason") in {None, "", "natural_end"}


def test_websocket_text_input_sends_character_trace_and_records_vocabulary(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "text_input", "user_id": "xiaoming", "text": "十字怎么写"})

        assert ws.receive_json() == {"type": "text_input_ack", "text": "十字怎么写"}
        assert ws.receive_json()["type"] == "llm_prompt"
        assert ws.receive_json()["type"] == "llm_reply_start"
        while ws.receive_json()["type"] != "llm_reply_end":
            pass
        assert ws.receive_json()["type"] == "llm_reply"
        assert ws.receive_json()["type"] == "tts_start"
        trace = ws.receive_json()
        assert trace["type"] == "show_character_trace"
        assert trace["character"] == "十"
        assert len(trace["strokes"]) == 2
        assert trace["payload"]["character"] == "十"
        assert len(trace["payload"]["strokes"]) == 2
        assert len(trace["payload"]["strokes"][0]) >= 5
        assert len(trace["payload"]["strokes"][1]) >= 5

    vocab_path = tmp_path / "vocabulary" / "xiaoming" / "index.json"
    history_path = tmp_path / "vocabulary" / "xiaoming" / "history.jsonl"
    assert vocab_path.exists()
    assert history_path.exists()
    vocabulary = json.loads(vocab_path.read_text(encoding="utf-8"))
    history = [
        json.loads(line)
        for line in history_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert vocabulary[0]["character"] == "十"
    assert vocabulary[0]["ask_count"] == 1
    assert vocabulary[0]["trace_available"] is True
    assert history[0]["question"] == "十字怎么写"


def test_websocket_text_input_write_prefix_enters_character_trace_scene(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "text_input", "user_id": "xiaoming", "text": "写一下饺子的饺"})

        assert ws.receive_json() == {"type": "text_input_ack", "text": "写一下饺子的饺"}
        assert ws.receive_json()["type"] == "llm_prompt"
        assert ws.receive_json()["type"] == "llm_reply_start"
        while ws.receive_json()["type"] != "llm_reply_end":
            pass
        assert ws.receive_json()["type"] == "llm_reply"
        assert ws.receive_json()["type"] == "tts_start"
        trace = ws.receive_json()
        assert trace["type"] == "show_character_trace"
        assert trace["character"] == "饺"
        assert len(trace["strokes"]) >= 8
        assert trace["payload"]["character"] == "饺"
        assert len(trace["payload"]["strokes"]) >= 8

def test_websocket_text_input_character_trace_uses_writing_narration_for_reply_and_tts(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    synthesized_texts: list[str] = []

    class FakeAgent:
        async def ainvoke(self, text: str, *, user_id: str) -> AgentResult:
            return AgentResult(
                response_text="普通问答回复",
                safety_passed=True,
                reply_source="ark",
                turn_id="turn-trace-1",
                prompt_debug={"route": "ark", "user_text": text, "user_id": user_id},
            )

    class FakeTTS:
        async def synthesize(self, text: str):
            synthesized_texts.append(text)
            yield b"pcm"

    monkeypatch.setattr(server_mod, "_build_runtime_agent", lambda **_: FakeAgent())
    monkeypatch.setattr(server_mod, "_build_runtime_tts_service", lambda settings: FakeTTS())
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "text_input", "user_id": "xiaoming", "text": "十字怎么写"})

        assert ws.receive_json() == {"type": "text_input_ack", "text": "十字怎么写"}
        assert ws.receive_json()["type"] == "llm_prompt"
        assert ws.receive_json()["type"] == "llm_reply_start"
        reply_end_text = ""
        while True:
            event = ws.receive_json()
            if event["type"] == "llm_reply_end":
                reply_end_text = event["text"]
                break
            assert event["type"] == "llm_reply_delta"
        reply = ws.receive_json()
        assert reply["type"] == "llm_reply"
        assert reply["text"] == reply_end_text
        assert "正在写“十”字" in reply["text"]
        assert "先写一横" in reply["text"]
        assert "再写一竖" in reply["text"]
        assert "小路口" in reply["text"]
        assert ws.receive_json()["type"] == "tts_start"
        trace = ws.receive_json()
        assert trace["type"] == "show_character_trace"
        assert trace["character"] == "十"
        assert len(trace["strokes"]) == 2
        assert trace["payload"]["character"] == "十"
        audio_chunks, end_event = receive_tts_audio_and_end(ws)
        assert b"".join(audio_chunks) == b"pcm"
        assert end_event["type"] == "tts_end"

    assert synthesized_texts == [reply_end_text]


def test_websocket_rejects_unauthenticated_audio_start(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})

        assert ws.receive_json() == {"type": "error", "message": "auth required"}


def test_websocket_rejects_audio_end_without_active_audio_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "audio_end"})

        assert ws.receive_json() == {
            "type": "error",
            "message": "audio_start required",
        }


def test_websocket_rejects_repeated_audio_end_after_completed_turn(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes("讲一个恐龙故事".encode("utf-8"))
        ws.send_json({"type": "audio_end"})

        assert ws.receive_json()["type"] == "asr_result"
        assert ws.receive_json()["type"] == "llm_prompt"
        assert ws.receive_json()["type"] == "llm_reply_start"
        while ws.receive_json()["type"] != "llm_reply_end":
            pass
        assert ws.receive_json()["type"] == "llm_reply"
        assert ws.receive_json().get("type") == "tts_start"
        audio_chunks, end_event = receive_tts_audio_and_end(ws)
        assert b"".join(audio_chunks)
        assert end_event["type"] == "tts_end"

        ws.send_json({"type": "audio_end"})

        assert ws.receive_json() == {
            "type": "error",
            "message": "audio_start required",
        }


def test_websocket_rejects_binary_before_audio_start(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_bytes("讲一个恐龙故事".encode("utf-8"))

        assert ws.receive_json() == {
            "type": "error",
            "message": "audio_start required",
        }


def test_websocket_rejects_audio_frame_over_resource_limit(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()

    import voice_robot.server as server_mod

    monkeypatch.setattr(server_mod, "MAX_AUDIO_FRAME_BYTES", 3)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {"type": "auth", "device_id": "web-dev", "token": "local-token"}
        )
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes(b"1234")
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["message"] == "这段语音太长，请说短一点再试"
        assert error["reason"] == "audio_limit"
        assert "audio segment limit exceeded" in error["detail"]
        ws.send_json({"type": "audio_end"})


def test_websocket_audio_start_restarts_active_audio_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes("旧音频".encode("utf-8"))
        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes("新音频".encode("utf-8"))
        ws.send_json({"type": "audio_end"})

        asr_result = ws.receive_json()
        assert asr_result["type"] == "asr_result"
        assert asr_result["text"] == "新音频"
        assert asr_result["latency_ms"] >= 0


def test_websocket_records_audit_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes("讲一个恐龙故事".encode("utf-8"))
        ws.send_json({"type": "audio_end"})

        assert ws.receive_json()["type"] == "asr_result"
        assert ws.receive_json()["type"] == "llm_prompt"
        assert ws.receive_json()["type"] == "llm_reply_start"
        while ws.receive_json()["type"] != "llm_reply_end":
            pass
        assert ws.receive_json()["type"] == "llm_reply"
        assert ws.receive_json().get("type") == "tts_start"
        audio_chunks, end_event = receive_tts_audio_and_end(ws)
        assert b"".join(audio_chunks)
        assert end_event["type"] == "tts_end"

    events_path = tmp_path / "audit" / "xiaoming" / "events.jsonl"
    assert events_path.exists()
    types = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        types.append(__import__("json").loads(line).get("event_type"))
    assert "asr_result" in types
    assert "llm_prompt" in types
    assert "llm_reply_delta" not in types
    assert "llm_reply_end" in types
    assert "tts_end" in types


def test_websocket_records_tts_prepare_error_without_false_start(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()

    import voice_robot.server as server_mod

    class BrokenTTS:
        async def synthesize(self, text: str):
            raise RuntimeError("tts denied")
            yield b""

    monkeypatch.setattr(server_mod, "LocalTTSService", BrokenTTS)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes("讲一个恐龙故事".encode("utf-8"))
        ws.send_json({"type": "audio_end"})

        assert ws.receive_json()["type"] == "asr_result"
        assert ws.receive_json()["type"] == "llm_prompt"
        assert ws.receive_json()["type"] == "llm_reply_start"
        while ws.receive_json()["type"] != "llm_reply_end":
            pass
        assert ws.receive_json()["type"] == "llm_reply"
        error_event = ws.receive_json()
        assert error_event["type"] == "error"
        assert error_event["message"] == "语音处理失败，请再说一次"
        assert error_event["reason"] == "speech_turn_failed"
        assert "tts denied" in error_event["detail"]

    events_path = tmp_path / "audit" / "xiaoming" / "events.jsonl"
    assert events_path.exists()
    events = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(__import__("json").loads(line))

    assert any(
        event.get("event_type") == "tts_prepare_error"
        and event.get("payload", {}).get("reason") == "error"
        for event in events
    )
    assert not any(event.get("event_type") == "tts_start" for event in events)
    assert any(
        event.get("event_type") == "speech_turn_error"
        and "tts denied" in event.get("payload", {}).get("error", "")
        for event in events
    )
