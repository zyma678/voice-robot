import time
import anyio

from fastapi.testclient import TestClient

from voice_robot.config import get_settings
from voice_robot.server import app


def test_websocket_barge_in_interrupts_tts(monkeypatch, tmp_path) -> None:
    class SlowTTS:
        async def synthesize(self, text: str):
            # Make the turn long enough so barge-in can happen before normal tts_end.
            for index in range(50):
                await anyio.sleep(0.02)
                yield f"chunk-{index}".encode("utf-8")

    import voice_robot.server as server_mod

    monkeypatch.setattr(server_mod, "LocalTTSService", SlowTTS)
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH",
        str(tmp_path / "console_settings.json"),
    )
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes("讲一个恐龙故事".encode("utf-8"))
        ws.send_json({"type": "audio_end"})

        # Wait for llm to start + tts to begin, then barge-in.
        saw_tts_start = False
        deadline = time.time() + 3
        while time.time() < deadline:
            event = ws.receive_json()
            if event.get("type") == "tts_start":
                saw_tts_start = True
                break
        assert saw_tts_start is True

        # Wait for at least one audio frame to be in-flight before interrupting.
        saw_audio = False
        deadline = time.time() + 3
        while time.time() < deadline:
            message = ws.receive()
            if message.get("bytes") is not None:
                saw_audio = True
                break
        assert saw_audio is True

        ws.send_json({"type": "barge_in"})
        # After tts_start, the server may have already pushed at least one audio frame.
        saw_end = False
        deadline = time.time() + 3
        while time.time() < deadline:
            message = ws.receive()
            if "text" in message and message["text"]:
                end_event = __import__("json").loads(message["text"])
                if end_event.get("type") == "tts_end" and end_event.get("reason"):
                    assert end_event.get("reason") == "barge_in"
                    assert "latency_ms" in end_event
                    saw_end = True
                    break
        assert saw_end is True, "expected tts_end(reason=barge_in) after barge_in"

    # Verify audit contains barge-in received event.
    events_path = tmp_path / "audit" / "xiaoming" / "events.jsonl"
    assert events_path.exists()
    types = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        types.append(__import__("json").loads(line).get("event_type"))
    assert "barge_in_received" in types


def test_websocket_text_input_interrupts_tts(monkeypatch, tmp_path) -> None:
    class SlowTTS:
        async def synthesize(self, text: str):
            for index in range(50):
                await anyio.sleep(0.02)
                yield f"chunk-{index}".encode("utf-8")

    import voice_robot.server as server_mod

    monkeypatch.setattr(server_mod, "LocalTTSService", SlowTTS)
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH",
        str(tmp_path / "console_settings.json"),
    )
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes("讲一个恐龙故事".encode("utf-8"))
        ws.send_json({"type": "audio_end"})

        # Wait for tts_start + one audio chunk, then text_input barge-in.
        deadline = time.time() + 3
        while time.time() < deadline:
            event = ws.receive_json()
            if event.get("type") == "tts_start":
                break
        deadline = time.time() + 3
        while time.time() < deadline:
            message = ws.receive()
            if message.get("bytes") is not None:
                break

        ws.send_json({"type": "text_input", "user_id": "xiaoming", "text": "换个话题，讲太空故事"})

        # Expect an interrupt tts_end with reason, plus a new llm turn starting.
        saw_interrupt_end = False
        saw_new_reply_start = False
        deadline = time.time() + 3
        while time.time() < deadline:
            message = ws.receive()
            if "text" not in message or not message["text"]:
                continue
            event = __import__("json").loads(message["text"])
            if event.get("type") == "tts_end" and event.get("reason") == "barge_in":
                saw_interrupt_end = True
            if event.get("type") == "llm_reply_start":
                saw_new_reply_start = True
            if saw_interrupt_end and saw_new_reply_start:
                return
        raise AssertionError("expected text_input to interrupt and start a new turn")


def test_websocket_text_input_failure_records_turn_error(monkeypatch, tmp_path) -> None:
    class SlowTTS:
        async def synthesize(self, text: str):
            for index in range(10):
                await anyio.sleep(0.01)
                yield f"chunk-{index}".encode("utf-8")

    class BrokenAgent:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, text: str, user_id: str):
            self.calls += 1
            if self.calls >= 2:
                raise RuntimeError("llm denied")

            class Result:
                response_text = "第一轮回复"
                reply_source = "fallback-local"
                fallback_reason = ""
                safety_passed = True

            return Result()

    import voice_robot.server as server_mod

    monkeypatch.setattr(server_mod, "LocalTTSService", SlowTTS)
    agent = BrokenAgent()
    monkeypatch.setattr(server_mod, "_build_runtime_agent", lambda **_: agent)
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH",
        str(tmp_path / "console_settings.json"),
    )
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes("讲一个恐龙故事".encode("utf-8"))
        ws.send_json({"type": "audio_end"})

        deadline = time.time() + 3
        while time.time() < deadline:
            event = ws.receive_json()
            if event.get("type") == "tts_start":
                break
        deadline = time.time() + 3
        while time.time() < deadline:
            message = ws.receive()
            if message.get("bytes") is not None:
                break

        ws.send_json({"type": "text_input", "user_id": "xiaoming", "text": "换个话题"})

        saw_interrupt_end = False
        saw_error = False
        deadline = time.time() + 3
        while time.time() < deadline:
            message = ws.receive()
            if "text" not in message or not message["text"]:
                continue
            event = __import__("json").loads(message["text"])
            if event.get("type") == "tts_end" and event.get("reason") == "barge_in":
                saw_interrupt_end = True
            if event.get("type") == "error" and "speech turn failed: llm denied" in event.get("message", ""):
                saw_error = True
                break

        assert saw_interrupt_end is True
        assert saw_error is True

    events_path = tmp_path / "audit" / "xiaoming" / "events.jsonl"
    assert events_path.exists()
    events = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(__import__("json").loads(line))

    assert any(
        event.get("event_type") == "speech_turn_error"
        and "llm denied" in event.get("payload", {}).get("error", "")
        for event in events
    )
