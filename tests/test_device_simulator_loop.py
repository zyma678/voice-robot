"""End-to-end test for the browser device-simulator path.

The web "真机模拟" page connects to /ws with a hardware device_id and drives
the same uplink loop the firmware does. This verifies that loop server-side:
the device is recognised as hardware, an audio turn produces ASR -> LLM -> TTS,
and the console can list/target it.
"""

from fastapi.testclient import TestClient

from voice_robot.config import get_settings
from voice_robot.server import app


def test_simulated_hardware_device_runs_full_voice_loop(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()

    import voice_robot.server as server_mod

    class FakeASR:
        async def transcribe(self, audio: bytes) -> str:
            assert audio == b"sim-pcm-frame"
            return "你好小智"

    class FakeTTS:
        async def synthesize(self, text: str):
            assert text
            yield b"\x10\x20"

    monkeypatch.setattr(server_mod, "LocalASRService", FakeASR)
    monkeypatch.setattr(server_mod, "LocalTTSService", FakeTTS)

    client = TestClient(app)
    device_id = "esp32-sim-001"
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "auth",
                "device_id": device_id,
                "token": get_settings().auth_token,
                "transport": "wifi_direct",
            }
        )
        assert ws.receive_json()["type"] == "auth_ok"

        # The simulator now appears as a hardware device the console can target.
        devices = client.get("/api/devices").json()
        sim = next(d for d in devices["devices"] if d["device_id"] == device_id)
        assert sim["kind"] == "hardware"
        assert devices["hardware_online"] >= 1

        # Conversation-mode uplink turn: audio_start -> PCM frame -> audio_end.
        ws.send_json(
            {
                "type": "audio_start",
                "user_id": "xiaoming",
                "codec": "pcm_s16le",
                "sample_rate_hz": 16000,
            }
        )
        ws.send_bytes(b"sim-pcm-frame")
        ws.send_json({"type": "audio_end"})

        asr_result = ws.receive_json()
        assert asr_result["type"] == "asr_result"
        assert asr_result["text"] == "你好小智"
        assert asr_result["latency_ms"] >= 0
        # Drive through the LLM/TTS reply the device would play back.
        saw_tts_start = False
        saw_tts_bytes = False
        while True:
            message = ws.receive()
            if message.get("bytes") is not None:
                saw_tts_bytes = True
                continue
            event = __import__("json").loads(message["text"])
            if event["type"] == "tts_start":
                saw_tts_start = True
            if event["type"] == "tts_end":
                break
        assert saw_tts_start
        assert saw_tts_bytes


def test_simulator_rejects_console_identity_server_side_is_console(monkeypatch, tmp_path) -> None:
    # Sanity: device_id "web-console" is the console, never hardware. The sim UI
    # blocks this client-side; confirm the server classification it relies on.
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()

    from voice_robot.server import _device_kind, CONSOLE_DEVICE_ID

    assert _device_kind(CONSOLE_DEVICE_ID) == "console"
    assert _device_kind("esp32-sim-001") == "hardware"
