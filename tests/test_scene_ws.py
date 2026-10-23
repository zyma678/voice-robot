import json

from fastapi.testclient import TestClient

from voice_robot import server
from voice_robot.agent.state import AgentResult
from voice_robot.config import get_settings
from voice_robot.metrics.context import get_current_scenario
from voice_robot.server import app


def _recv_json(ws):
    """Receive next text frame, skipping binary (TTS audio) frames."""
    while True:
        message = ws.receive()
        text = message.get("text")
        if text is not None:
            return json.loads(text)
        if message.get("bytes") is not None:
            continue
        raise AssertionError(f"unexpected ws frame: {message}")


def _drain_turn(ws):
    """Collect a turn's control messages up to and including tts_end."""
    events = []
    while True:
        msg = _recv_json(ws)
        events.append(msg)
        if msg.get("type") == "tts_end":
            return events


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()
    return TestClient(app)


def test_enter_and_exit_scene_via_text(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        # 1) Fuzzy enter intent (keyword hit) -> play enter prompt verbatim.
        ws.send_json({"type": "text_input", "user_id": "xiaoming", "text": "我想学英语"})
        assert ws.receive_json() == {"type": "text_input_ack", "text": "我想学英语"}
        events = _drain_turn(ws)
        scene_changed = [e for e in events if e.get("type") == "scene_changed"]
        assert len(scene_changed) == 1
        assert scene_changed[0]["scene_id"] == "english_tutor"
        assert scene_changed[0]["scene_action"] == "enter"
        reply = [e for e in events if e.get("type") == "llm_reply"][0]
        assert "练习" in reply["text"] or "English" in reply["text"]
        assert reply["source"] == "scene-prompt"

        # 2) Exit via voice command -> play exit prompt verbatim.
        ws.send_json({"type": "text_input", "user_id": "xiaoming", "text": "退出英语"})
        assert ws.receive_json() == {"type": "text_input_ack", "text": "退出英语"}
        events = _drain_turn(ws)
        scene_changed = [e for e in events if e.get("type") == "scene_changed"]
        assert len(scene_changed) == 1
        assert scene_changed[0]["scene_action"] == "exit"


def test_enter_prompt_audio_cached_second_time(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)

    calls = {"n": 0}
    real_collect = server._collect_tts_audio

    async def counting_collect(text, **kwargs):
        calls["n"] += 1
        return await real_collect(text, **kwargs)

    monkeypatch.setattr(server, "_collect_tts_audio", counting_collect)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        ws.send_json({"type": "text_input", "user_id": "xiaoming", "text": "学英语"})
        ws.receive_json()  # text_input_ack
        _drain_turn(ws)
        after_first_enter = calls["n"]
        assert after_first_enter >= 1  # cache miss -> synthesized once

        ws.send_json({"type": "text_input", "user_id": "xiaoming", "text": "退出英语"})
        ws.receive_json()
        _drain_turn(ws)

        ws.send_json({"type": "text_input", "user_id": "xiaoming", "text": "学英语"})
        ws.receive_json()
        _drain_turn(ws)
        # Second enter must hit the persisted cache: no new enter-prompt synthesis.
        assert calls["n"] == after_first_enter + 1  # only the exit prompt synthesized between


def test_in_scene_conversation_is_attributed_to_scene_scenario(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)

    seen_scenarios: list[str] = []

    class FakeAgent:
        async def ainvoke(self, text, *, user_id):
            # Captured inside _run_turn's set_current_scenario(scene_id) context.
            seen_scenarios.append(get_current_scenario())
            return AgentResult(
                response_text="Nice to meet you!",
                safety_passed=True,
                reply_source="ark",
                turn_id="turn-scene-1",
                prompt_debug={"route": "ark", "user_text": text, "user_id": user_id},
            )

    monkeypatch.setattr(server, "_build_runtime_agent", lambda **_: FakeAgent())

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        # Enter the scene (verbatim prompt turn — agent not invoked).
        ws.send_json({"type": "text_input", "user_id": "xiaoming", "text": "学英语"})
        ws.receive_json()
        _drain_turn(ws)
        assert seen_scenarios == []

        # In-scene conversation -> agent invoked under scenario "english_tutor".
        ws.send_json({"type": "text_input", "user_id": "xiaoming", "text": "How are you"})
        ws.receive_json()
        _drain_turn(ws)
        assert seen_scenarios == ["english_tutor"]
