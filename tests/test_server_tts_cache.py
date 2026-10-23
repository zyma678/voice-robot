import anyio
import json
from fastapi.testclient import TestClient

from voice_robot import server
from voice_robot.agent.state import AgentResult
from voice_robot.config import get_settings
from voice_robot.runtime_config import RuntimeConfigStore


def test_reply_tts_cache_avoids_duplicate_provider_call(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()
    runtime_store = RuntimeConfigStore(tmp_path / "console_settings.json")
    runtime_store.save_speech_settings(
        tts_resource_id="seed-tts-1.0",
        tts_sample_rate_hz=16000,
        tts_encoding="pcm",
        tts_voice_type="voice-a",
    )

    class CountingTTS:
        calls = 0

        async def synthesize(self, text: str):
            self.calls += 1
            yield f"audio:{text}".encode()

    service = CountingTTS()

    async def scenario() -> None:
        first, first_hit, first_complete = await server._collect_cached_reply_tts_audio(
            service,
            "重复回答",
            settings=get_settings(),
            runtime_store=runtime_store,
        )
        second, second_hit, second_complete = await server._collect_cached_reply_tts_audio(
            service,
            "重复回答",
            settings=get_settings(),
            runtime_store=runtime_store,
        )

        assert first == second == b"audio:\xe9\x87\x8d\xe5\xa4\x8d\xe5\x9b\x9e\xe7\xad\x94"
        assert first_hit is False
        assert second_hit is True
        assert first_complete is True
        assert second_complete is True
        assert service.calls == 1

    anyio.run(scenario)


def test_reply_tts_cache_is_invalidated_by_voice_change(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()
    runtime_store = RuntimeConfigStore(tmp_path / "console_settings.json")

    class CountingTTS:
        calls = 0

        async def synthesize(self, text: str):
            self.calls += 1
            yield b"audio"

    service = CountingTTS()

    async def scenario() -> None:
        runtime_store.save_speech_settings(
            tts_resource_id="seed-tts-1.0",
            tts_sample_rate_hz=16000,
            tts_encoding="pcm",
            tts_voice_type="voice-a",
        )
        _, first_hit, _ = await server._collect_cached_reply_tts_audio(
            service,
            "同一句话",
            settings=get_settings(),
            runtime_store=runtime_store,
        )
        runtime_store.save_speech_settings(
            tts_resource_id="seed-tts-1.0",
            tts_sample_rate_hz=16000,
            tts_encoding="pcm",
            tts_voice_type="voice-b",
        )
        _, second_hit, _ = await server._collect_cached_reply_tts_audio(
            service,
            "同一句话",
            settings=get_settings(),
            runtime_store=runtime_store,
        )

        assert first_hit is False
        assert second_hit is False
        assert service.calls == 2

    anyio.run(scenario)


def test_partial_reply_tts_is_playable_once_but_never_cached(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()

    class PartialTTS:
        calls = 0

        async def synthesize(self, text: str):
            type(self).calls += 1
            yield b"partial-pcm"
            raise RuntimeError("provider stream dropped")

    service = PartialTTS()
    runtime_store = RuntimeConfigStore(tmp_path / "console_settings.json")

    async def scenario() -> None:
        first, first_hit, first_complete = await server._collect_cached_reply_tts_audio(
            service,
            "这是一句未完成的回答",
            settings=get_settings(),
            runtime_store=runtime_store,
        )
        second, second_hit, second_complete = await server._collect_cached_reply_tts_audio(
            service,
            "这是一句未完成的回答",
            settings=get_settings(),
            runtime_store=runtime_store,
        )

        assert first == second == b"partial-pcm"
        assert first_hit is second_hit is False
        assert first_complete is second_complete is False
        assert service.calls == 2
        assert not list((tmp_path / "tts_response_cache").glob("*.pcm"))

    anyio.run(scenario)


def test_websocket_reports_tts_cache_hit_and_skips_second_synthesis(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()

    class FixedAgent:
        async def ainvoke(self, text: str, *, user_id: str) -> AgentResult:
            return AgentResult(
                response_text="固定回复",
                safety_passed=True,
                reply_source="local-rule",
                prompt_debug={"route": "test"},
            )

    class CountingLocalTTS:
        calls = 0

        def __init__(self, sample_rate_hz: int = 16000) -> None:
            self.sample_rate_hz = sample_rate_hz

        async def synthesize(self, text: str):
            type(self).calls += 1
            yield b"\x01\x00\x02\x00"

    monkeypatch.setattr(server, "_build_runtime_agent", lambda **_: FixedAgent())
    monkeypatch.setattr(server, "LocalTTSService", CountingLocalTTS)

    def run_turn(ws, text: str) -> dict:
        ws.send_json({"type": "text_input", "user_id": "u1", "text": text})
        assert ws.receive_json()["type"] == "text_input_ack"
        start = None
        while start is None:
            event = ws.receive_json()
            if event["type"] == "tts_start":
                start = event
        assert ws.receive_bytes() == b"\x01\x00\x02\x00"
        assert ws.receive_json()["type"] == "tts_end"
        return start

    with TestClient(server.app).websocket_connect("/ws") as ws:
        ws.send_json(
            {"type": "auth", "device_id": "web-console", "token": "local-token"}
        )
        assert ws.receive_json()["type"] == "auth_ok"
        first = run_turn(ws, "第一次")
        second = run_turn(ws, "第二次")

    assert first["source"] == "local"
    assert second["source"] == "cache"
    assert CountingLocalTTS.calls == 1
    audit_path = tmp_path / "audit" / "u1" / "events.jsonl"
    turn_ids = [
        event["turn_id"]
        for event in (
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        if event["event_type"] == "text_input"
    ]
    assert len(turn_ids) == 2
    assert len(set(turn_ids)) == 2
