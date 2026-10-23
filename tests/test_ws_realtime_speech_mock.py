from fastapi.testclient import TestClient

from voice_robot.config import get_settings
from voice_robot.runtime_config import RuntimeConfigStore
from voice_robot.server import app


def test_websocket_uses_split_asr_tts_even_when_speech_config_exists(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH",
        str(tmp_path / "console_settings.json"),
    )
    get_settings.cache_clear()

    import voice_robot.server as server_mod

    class FakeASR:
        async def transcribe(self, audio: bytes) -> str:
            assert audio == b"fake-audio"
            return "讲一个恐龙故事"

    class FakeTTS:
        async def synthesize(self, text: str):
            assert text
            yield b"\x01\x02"

    monkeypatch.setattr(server_mod, "LocalASRService", FakeASR)
    monkeypatch.setattr(server_mod, "LocalTTSService", FakeTTS)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes(b"fake-audio")
        ws.send_json({"type": "audio_end"})

        asr_result = ws.receive_json()
        assert asr_result["type"] == "asr_result"
        assert asr_result["text"] == "讲一个恐龙故事"
        assert asr_result["latency_ms"] >= 0
        # LLM prompt debug + stream events happen next.
        assert ws.receive_json()["type"] == "llm_prompt"
        assert ws.receive_json()["type"] == "llm_reply_start"
        while ws.receive_json()["type"] != "llm_reply_end":
            pass
        assert ws.receive_json()["type"] == "llm_reply"
        assert ws.receive_json().get("type") == "tts_start"
        assert ws.receive_bytes() == b"\x01\x02"
        end_event = ws.receive_json()
        assert end_event["type"] == "tts_end"


def test_websocket_back_to_back_audio_turns_do_not_reuse_cancel_scope(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH",
        str(tmp_path / "console_settings.json"),
    )
    get_settings.cache_clear()

    import voice_robot.server as server_mod

    class FakeASR:
        async def transcribe(self, audio: bytes) -> str:
            return "连续语音"

    class FakeTTS:
        async def synthesize(self, text: str):
            yield b"ok"

    monkeypatch.setattr(server_mod, "LocalASRService", FakeASR)
    monkeypatch.setattr(server_mod, "LocalTTSService", FakeTTS)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json()["type"] == "auth_ok"

        # Queue two turns without waiting for the first background task to enter
        # its cancel scope. This used to let both closures enter the same later
        # cancel_scope and raise: Each CancelScope may only be used once.
        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes(b"first")
        ws.send_json({"type": "audio_end"})
        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes(b"second")
        ws.send_json({"type": "audio_end"})

        saw_asr = False
        for _ in range(8):
            event = ws.receive_json()
            assert "Each CancelScope may only be used" not in event.get("message", "")
            if event.get("type") == "asr_result":
                saw_asr = True
                break
        assert saw_asr is True


def test_websocket_persists_tts_audio_for_assistant_turn(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH",
        str(tmp_path / "console_settings.json"),
    )
    get_settings.cache_clear()

    import voice_robot.server as server_mod

    class FakeASR:
        async def transcribe(self, audio: bytes) -> str:
            return "讲一个恐龙故事"

    class FakeTTS:
        async def synthesize(self, text: str):
            yield b"\x01\x00"
            yield b"\x02\x00"

    monkeypatch.setattr(server_mod, "LocalASRService", FakeASR)
    monkeypatch.setattr(server_mod, "LocalTTSService", FakeTTS)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes(b"fake-audio")
        ws.send_json({"type": "audio_end"})

        assert ws.receive_json()["type"] == "asr_result"
        assert ws.receive_json()["type"] == "llm_prompt"
        assert ws.receive_json()["type"] == "llm_reply_start"
        while ws.receive_json()["type"] != "llm_reply_end":
            pass
        reply = ws.receive_json()
        assert reply["type"] == "llm_reply"
        assert reply["turn_id"]
        assert reply["tts_url"].endswith(f"/turns/{reply['turn_id']}/tts")
        assert ws.receive_json()["type"] == "tts_start"
        # TTS is buffered and streamed in one paced pass, so the two synthesized
        # chunks arrive joined in a single (sub-frame-sized) binary frame.
        assert ws.receive_bytes() == b"\x01\x00\x02\x00"
        end_event = ws.receive_json()
        assert end_event["type"] == "tts_end"
        assert end_event["tts_url"] == reply["tts_url"]
        assert end_event["tts_audio_format"] == "pcm_s16le"

    turns = client.get("/api/memory/users/xiaoming/turns?limit=1").json()["turns"]
    assert turns[0]["turn_id"] == reply["turn_id"]
    assert turns[0]["tts_byte_length"] == 4
    assert "tts_audio_base64" not in turns[0]
    audio_response = client.get(reply["tts_url"])
    assert audio_response.status_code == 200
    assert audio_response.content == b"\x01\x00\x02\x00"


def test_websocket_uses_runtime_volc_asr_tts_services(monkeypatch, tmp_path) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    RuntimeConfigStore(runtime_config_path).save_speech_settings(
        mode="split",
        asr_api_key="asr-api-key",
        asr_resource_id="volc.bigasr.sauc.duration",
        asr_ws_url="wss://example.com/api/v3/sauc/bigmodel",
        asr_sample_rate_hz=16000,
        tts_api_key="tts-api-key",
        tts_resource_id="seed-tts-1.0",
        tts_ws_url="wss://example.com/api/v3/tts/unidirectional/stream",
        tts_sample_rate_hz=24000,
        tts_encoding="pcm",
        tts_voice_type="voice-a",
    )

    import voice_robot.server as server_mod

    class FakeStreamSession:
        def __init__(self) -> None:
            self._buffer = bytearray()

        async def feed(self, pcm: bytes) -> None:
            self._buffer += pcm

        async def finish(self) -> str:
            assert bytes(self._buffer) == b"pcm"
            return "运行态火山识别"

        async def aclose(self) -> None:
            pass

    class FakeVolcASR:
        def __init__(self, *, config):
            assert config.api_key == "asr-api-key"
            assert config.resource_id == "volc.bigasr.sauc.duration"
            assert config.ws_url == "wss://example.com/api/v3/sauc/bigmodel"

        def open_stream(self, *, sample_rate_hz=None, on_partial=None):
            return FakeStreamSession()

        async def transcribe(self, audio: bytes) -> str:
            return "运行态火山识别"

    class FakeVolcTTS:
        def __init__(self, *, config):
            assert config.api_key == "tts-api-key"
            assert config.voice_type == "voice-a"
            assert config.resource_id == "seed-tts-1.0"
            assert config.ws_url == "wss://example.com/api/v3/tts/unidirectional/stream"

        async def synthesize(self, text: str):
            yield b"volc-tts"

    monkeypatch.setattr(server_mod, "VolcASRService", FakeVolcASR)
    monkeypatch.setattr(server_mod, "VolcTTSService", FakeVolcTTS)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes(b"pcm")
        ws.send_json({"type": "audio_end"})

        asr_result = ws.receive_json()
        assert asr_result["type"] == "asr_result"
        assert asr_result["text"] == "运行态火山识别"
        assert asr_result["latency_ms"] >= 0
        assert ws.receive_json()["type"] == "llm_prompt"
        assert ws.receive_json()["type"] == "llm_reply_start"
        while ws.receive_json()["type"] != "llm_reply_end":
            pass
        assert ws.receive_json()["type"] == "llm_reply"
        assert ws.receive_json().get("type") == "tts_start"
        assert ws.receive_bytes() == b"volc-tts"


def test_websocket_picks_up_updated_runtime_speech_settings_without_reconnect(
    monkeypatch, tmp_path
) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    RuntimeConfigStore(runtime_config_path).save_speech_settings(
        mode="split",
        asr_api_key="asr-api-key",
        asr_resource_id="volc.bigasr.sauc.duration",
        asr_ws_url="wss://example.com/api/v3/sauc/bigmodel",
        asr_sample_rate_hz=16000,
        tts_api_key="tts-api-key",
        tts_resource_id="seed-tts-1.0",
        tts_ws_url="wss://example.com/api/v3/tts/unidirectional/stream",
        tts_sample_rate_hz=24000,
        tts_encoding="pcm",
        tts_voice_type="voice-a",
    )

    import voice_robot.server as server_mod

    class FakeStreamSession:
        def __init__(self, text: str) -> None:
            self._text = text

        async def feed(self, pcm: bytes) -> None:
            pass

        async def finish(self) -> str:
            return self._text

        async def aclose(self) -> None:
            pass

    class FakeVolcASR:
        def __init__(self, *, config):
            self._text = f"识别:{config.resource_id}"

        def open_stream(self, *, sample_rate_hz=None, on_partial=None):
            return FakeStreamSession(self._text)

        async def transcribe(self, audio: bytes) -> str:
            return self._text

    class FakeVolcTTS:
        def __init__(self, *, config):
            self._voice = config.voice_type

        async def synthesize(self, text: str):
            yield self._voice.encode("utf-8")

    monkeypatch.setattr(server_mod, "VolcASRService", FakeVolcASR)
    monkeypatch.setattr(server_mod, "VolcTTSService", FakeVolcTTS)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json()["type"] == "auth_ok"

        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes(b"pcm")
        ws.send_json({"type": "audio_end"})

        asr_result = ws.receive_json()
        assert asr_result["type"] == "asr_result"
        assert asr_result["text"] == "识别:volc.bigasr.sauc.duration"
        assert asr_result["latency_ms"] >= 0
        assert ws.receive_json()["type"] == "llm_prompt"
        assert ws.receive_json()["type"] == "llm_reply_start"
        while ws.receive_json()["type"] != "llm_reply_end":
            pass
        assert ws.receive_json()["type"] == "llm_reply"
        assert ws.receive_json().get("type") == "tts_start"
        assert ws.receive_bytes() == b"voice-a"
        assert ws.receive_json()["type"] == "tts_end"

        RuntimeConfigStore(runtime_config_path).save_speech_settings(
            mode="split",
            asr_api_key="asr-api-key",
            asr_resource_id="volc.bigasr.sauc.duration",
            asr_ws_url="wss://example.com/api/v3/sauc/bigmodel-v2",
            asr_sample_rate_hz=16000,
            tts_api_key="tts-api-key",
            tts_resource_id="seed-tts-1.0",
            tts_ws_url="wss://example.com/api/v3/tts/unidirectional/stream",
            tts_sample_rate_hz=24000,
            tts_encoding="pcm",
            tts_voice_type="voice-b",
        )

        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes(b"pcm")
        ws.send_json({"type": "audio_end"})

        asr_result = ws.receive_json()
        assert asr_result["type"] == "asr_result"
        assert asr_result["text"] == "识别:volc.bigasr.sauc.duration"
        assert asr_result["latency_ms"] >= 0
        assert ws.receive_json()["type"] == "llm_prompt"
        assert ws.receive_json()["type"] == "llm_reply_start"
        while ws.receive_json()["type"] != "llm_reply_end":
            pass
        assert ws.receive_json()["type"] == "llm_reply"
        assert ws.receive_json().get("type") == "tts_start"
        assert ws.receive_bytes() == b"voice-b"


def test_websocket_streams_partial_then_final_asr(monkeypatch, tmp_path) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    RuntimeConfigStore(runtime_config_path).save_speech_settings(
        mode="split",
        asr_api_key="asr-api-key",
        asr_resource_id="volc.bigasr.sauc.duration",
        asr_ws_url="wss://example.com/api/v3/sauc/bigmodel",
        asr_sample_rate_hz=16000,
        tts_api_key="tts-api-key",
        tts_resource_id="seed-tts-1.0",
        tts_ws_url="wss://example.com/api/v3/tts/unidirectional/stream",
        tts_sample_rate_hz=24000,
        tts_encoding="pcm",
        tts_voice_type="voice-a",
    )

    import voice_robot.server as server_mod

    class FakeStreamSession:
        def __init__(self, on_partial) -> None:
            self._on_partial = on_partial

        async def feed(self, pcm: bytes) -> None:
            # Emit a live partial transcript while the user is still speaking.
            if self._on_partial is not None:
                await self._on_partial("讲一个")

        async def finish(self) -> str:
            return "讲一个恐龙故事"

        async def aclose(self) -> None:
            pass

    class FakeVolcASR:
        def __init__(self, *, config):
            pass

        def open_stream(self, *, sample_rate_hz=None, on_partial=None):
            return FakeStreamSession(on_partial)

        async def transcribe(self, audio: bytes) -> str:
            return "讲一个恐龙故事"

    class FakeVolcTTS:
        def __init__(self, *, config):
            pass

        async def synthesize(self, text: str):
            yield b"tts"

    monkeypatch.setattr(server_mod, "VolcASRService", FakeVolcASR)
    monkeypatch.setattr(server_mod, "VolcTTSService", FakeVolcTTS)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json(
            {"type": "audio_start", "user_id": "xiaoming", "codec": "pcm_s16le"}
        )
        ws.send_bytes(b"pcm")
        ws.send_json({"type": "audio_end"})

        assert ws.receive_json() == {"type": "asr_partial", "text": "讲一个"}
        asr_result = ws.receive_json()
        assert asr_result["type"] == "asr_result"
        assert asr_result["text"] == "讲一个恐龙故事"
        assert asr_result["latency_ms"] >= 0
        assert ws.receive_json()["type"] == "llm_prompt"



def test_websocket_reports_split_asr_failure_without_closing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH",
        str(tmp_path / "console_settings.json"),
    )
    get_settings.cache_clear()

    import voice_robot.server as server_mod

    class FailingASR:
        async def transcribe(self, audio: bytes) -> str:
            raise RuntimeError("asr denied")

    monkeypatch.setattr(server_mod, "LocalASRService", FailingASR)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes(b"pcm")
        ws.send_json({"type": "audio_end"})

        error_event = ws.receive_json()
        assert error_event["type"] == "error"
        assert error_event["message"] == "语音处理失败，请再说一次"
        assert error_event["reason"] == "speech_turn_failed"
        assert "asr denied" in error_event["detail"]
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["type"] == "heartbeat"


def test_websocket_empty_asr_result_prompts_retry_without_llm(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH",
        str(tmp_path / "console_settings.json"),
    )
    get_settings.cache_clear()

    import voice_robot.server as server_mod

    class EmptyASR:
        async def transcribe(self, audio: bytes) -> str:
            return "   "

    monkeypatch.setattr(server_mod, "LocalASRService", EmptyASR)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes(b"silence")
        ws.send_json({"type": "audio_end"})

        error_event = ws.receive_json()
        assert error_event == {
            "type": "error",
            "message": "没有听清，请再说一次",
            "reason": "no_speech",
        }
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["type"] == "heartbeat"


def test_websocket_streaming_asr_feed_failure_does_not_close(monkeypatch, tmp_path) -> None:
    """A streaming recognizer that fails mid-feed must surface an error, not drop the ws.

    Regression: ``feed`` runs directly in recv_loop; an unguarded exception used
    to unwind the task group and close the socket (client saw a bare close 1000).
    """
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH",
        str(tmp_path / "console_settings.json"),
    )
    get_settings.cache_clear()

    import voice_robot.server as server_mod

    class FeedFailsSession:
        async def feed(self, pcm: bytes) -> None:
            raise RuntimeError("volc sauc asr connect failed")

        async def finish(self) -> str:
            return ""

        async def aclose(self) -> None:
            pass

    class StreamingASR:
        def open_stream(self, *, sample_rate_hz=None, on_partial=None):
            return FeedFailsSession()

        async def transcribe(self, audio: bytes) -> str:
            return ""

    monkeypatch.setattr(server_mod, "LocalASRService", StreamingASR)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json(
            {"type": "audio_start", "user_id": "xiaoming", "codec": "pcm_s16le"}
        )
        ws.send_bytes(b"pcm-1")

        error_event = ws.receive_json()
        assert error_event["type"] == "error"
        assert error_event["message"] == "语音识别暂时不可用，请再说一次"
        assert error_event["reason"] == "asr_failed"
        assert "connect failed" in error_event["detail"]

        # Trailing audio + audio_end after a failed segment are tolerated quietly,
        # and the connection stays usable for the next turn.
        ws.send_bytes(b"pcm-2")
        ws.send_json({"type": "audio_end"})
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["type"] == "heartbeat"


def test_websocket_reports_split_tts_failure_without_closing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH",
        str(tmp_path / "console_settings.json"),
    )
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
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes(b"pcm")
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
        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json()["type"] == "heartbeat"


def test_build_runtime_asr_falls_back_to_local_only_when_no_auth(
    monkeypatch, tmp_path
) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()

    import voice_robot.server as server_mod
    from voice_robot.services.asr import LocalASRService

    service = server_mod._build_runtime_asr_service(get_settings())
    assert isinstance(service, LocalASRService)


def test_build_runtime_asr_uses_default_resource_id_when_only_api_key_set(
    monkeypatch, tmp_path
) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    # Only the new-console X-Api-Key is provided. The resource id and ws url must
    # default to the streaming 1.0 values instead of downgrading to LocalASRService.
    RuntimeConfigStore(runtime_config_path).save_speech_settings(
        asr_api_key="asr-api-key",
    )

    import voice_robot.server as server_mod
    from voice_robot.services.asr import VolcASRService

    service = server_mod._build_runtime_asr_service(get_settings())
    assert isinstance(service, VolcASRService)
    assert service.config.api_key == "asr-api-key"
    assert service.config.resource_id == "volc.bigasr.sauc.duration"
    assert service.config.ws_url == "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"


def test_build_runtime_asr_does_not_reuse_tts_credentials(monkeypatch, tmp_path) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    # Streaming 1.0 only supports the new-console X-Api-Key. Without it, the
    # service must fall back to the offline echo stub even if TTS is configured.
    RuntimeConfigStore(runtime_config_path).save_speech_settings(
        tts_api_key="shared-tts-key",
        tts_voice_type="voice-a",
    )

    import voice_robot.server as server_mod
    from voice_robot.services.asr import LocalASRService

    service = server_mod._build_runtime_asr_service(get_settings())
    assert isinstance(service, LocalASRService)
