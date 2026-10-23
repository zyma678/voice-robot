import gzip
import json
import struct

import pytest

from voice_robot.services import asr as asr_module
from voice_robot.services.asr import (
    VolcASRConfig,
    VolcASRService,
    VolcASRStreamSession,
    _build_audio_request,
    _build_full_client_request,
    _build_hotword_context,
    _format_sauc_error,
    _generate_header,
    _parse_server_message,
)
from voice_robot.services import tts as tts_module
from voice_robot.services.tts import (
    LocalTTSService,
    VolcTTSConfig,
    VolcTTSService,
    build_finish_connection,
    build_send_text,
    parse_server_frame,
)


def test_local_tts_service_returns_pcm_audio_not_text_bytes() -> None:
    service = LocalTTSService(sample_rate_hz=24000)
    chunks = []

    async def collect() -> None:
        async for chunk in service.synthesize("你好，小朋友"):
            chunks.append(chunk)

    import anyio

    anyio.run(collect)

    assert chunks
    payload = b"".join(chunks)
    assert len(payload) > 1000
    assert len(payload) % 2 == 0
    assert payload != "你好，小朋友".encode("utf-8")


def test_volc_tts_default_sample_rate_matches_hardware_16khz() -> None:
    assert VolcTTSConfig().sample_rate_hz == 16000


def test_build_send_text_roundtrip() -> None:
    payload = {
        "user": {"uid": "voice_robot"},
        "req_params": {
            "text": "你好",
            "speaker": "voice-a",
            "audio_params": {"format": "pcm", "sample_rate": 24000},
        },
    }
    frame = build_send_text(payload)

    assert frame[0] == (0b0001 << 4) | 0b0001
    assert (frame[1] >> 4) == 0b0001  # full client request
    assert (frame[1] & 0x0F) == 0b0000  # no event number
    assert (frame[2] >> 4) == 0b0001  # JSON
    assert (frame[2] & 0x0F) == 0b0000  # no compression

    size = struct.unpack_from(">I", frame, 4)[0]
    assert json.loads(frame[8 : 8 + size]) == payload


def test_build_finish_connection_carries_event_2() -> None:
    frame = build_finish_connection()
    assert (frame[1] & 0x0F) == 0b0100  # with event number
    assert struct.unpack_from(">I", frame, 4)[0] == 2  # FinishConnection


def _tts_server_frame(message_type, event, *, payload=None, audio=b"", session_id="sess") -> bytes:
    serialization = 0b0000 if audio else 0b0001
    header = tts_module._generate_header(message_type, 0b0100, serialization, 0b0000)
    body = audio if audio else (json.dumps(payload).encode("utf-8") if payload is not None else b"")
    sid = session_id.encode("utf-8")
    return (
        header
        + struct.pack(">I", event)
        + struct.pack(">I", len(sid))
        + sid
        + struct.pack(">I", len(body))
        + body
    )


def test_parse_server_frame_audio_and_control() -> None:
    audio_frame = _tts_server_frame(
        tts_module._MSG_AUDIO_ONLY_RESPONSE, tts_module._EVENT_TTS_RESPONSE, audio=b"PCMDATA"
    )
    msg = parse_server_frame(audio_frame)
    assert msg.event == 352
    assert msg.audio == b"PCMDATA"
    assert msg.session_id == "sess"

    start_frame = _tts_server_frame(
        tts_module._MSG_FULL_SERVER_RESPONSE,
        tts_module._EVENT_TTS_SENTENCE_START,
        payload={"text": "句子"},
    )
    start = parse_server_frame(start_frame)
    assert start.event == 350
    assert start.payload == {"text": "句子"}

    done_frame = _tts_server_frame(
        tts_module._MSG_FULL_SERVER_RESPONSE,
        tts_module._EVENT_SESSION_FINISHED,
        payload={"status_code": 20000000, "message": "ok"},
    )
    done = parse_server_frame(done_frame)
    assert done.event == 152
    assert done.payload["status_code"] == 20000000


def test_parse_server_frame_error() -> None:
    header = tts_module._generate_header(tts_module._MSG_ERROR, 0b0000, 0b0001, 0b0000)
    frame = header + struct.pack(">I", 45000000) + b'{"message":"denied"}'
    msg = parse_server_frame(frame)
    assert msg.message_type == tts_module._MSG_ERROR
    assert msg.error_code == 45000000
    assert "denied" in msg.error_message


def test_volc_tts_raises_on_empty_text() -> None:
    service = VolcTTSService(config=VolcTTSConfig(api_key="k", voice_type="voice-a"))

    async def collect() -> None:
        async for _chunk in service.synthesize("   "):
            pass

    import anyio

    with pytest.raises(RuntimeError, match="text is empty"):
        anyio.run(collect)


def test_volc_tts_open_stream_requires_api_key() -> None:
    service = VolcTTSService(config=VolcTTSConfig(api_key="", voice_type="voice-a"))

    async def collect() -> None:
        async for _chunk in service.synthesize("你好"):
            pass

    import anyio

    with pytest.raises(RuntimeError, match="X-Api-Key"):
        anyio.run(collect)


class _FakeTTSWS:
    def __init__(self, frames: list[bytes]) -> None:
        self.sent: list[bytes] = []
        self._frames = list(frames)
        self.closed = False

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> bytes:
        if not self._frames:
            import anyio

            await anyio.sleep_forever()
        return self._frames.pop(0)

    async def close(self) -> None:
        self.closed = True


def test_volc_tts_streams_audio_frames(monkeypatch) -> None:
    import anyio

    fake_ws = _FakeTTSWS(
        [
            _tts_server_frame(
                tts_module._MSG_FULL_SERVER_RESPONSE,
                tts_module._EVENT_TTS_SENTENCE_START,
                payload={"text": "讲一个"},
            ),
            _tts_server_frame(
                tts_module._MSG_AUDIO_ONLY_RESPONSE,
                tts_module._EVENT_TTS_RESPONSE,
                audio=b"AAA",
            ),
            _tts_server_frame(
                tts_module._MSG_AUDIO_ONLY_RESPONSE,
                tts_module._EVENT_TTS_RESPONSE,
                audio=b"BBB",
            ),
            _tts_server_frame(
                tts_module._MSG_FULL_SERVER_RESPONSE,
                tts_module._EVENT_SESSION_FINISHED,
                payload={"status_code": 20000000},
            ),
        ]
    )
    captured_headers: dict[str, str] = {}

    async def fake_connect(url, *, additional_headers=None, **kwargs):
        captured_headers.update(additional_headers or {})
        return fake_ws

    monkeypatch.setattr(tts_module.websockets, "connect", fake_connect)

    service = VolcTTSService(
        config=VolcTTSConfig(api_key="api-key-123", voice_type="voice-a")
    )

    async def collect() -> list[bytes]:
        out = []
        async for chunk in service.synthesize("讲个故事"):
            out.append(chunk)
        return out

    out = anyio.run(collect)

    assert out == [b"AAA", b"BBB"]
    assert captured_headers["X-Api-Key"] == "api-key-123"
    assert captured_headers["X-Api-Resource-Id"] == "seed-tts-1.0"
    assert "X-Api-Request-Id" in captured_headers
    # SendText first; FinishConnection sent on teardown.
    assert (fake_ws.sent[0][1] >> 4) == 0b0001
    assert fake_ws.closed


def test_volc_tts_raises_on_error_frame(monkeypatch) -> None:
    import anyio

    header = tts_module._generate_header(tts_module._MSG_ERROR, 0b0000, 0b0001, 0b0000)
    error_frame = header + struct.pack(">I", 45000000) + b'{"message":"access denied"}'
    fake_ws = _FakeTTSWS([error_frame])

    async def fake_connect(url, *, additional_headers=None, **kwargs):
        return fake_ws

    monkeypatch.setattr(tts_module.websockets, "connect", fake_connect)
    service = VolcTTSService(config=VolcTTSConfig(api_key="k", voice_type="voice-a"))

    async def collect() -> None:
        async for _chunk in service.synthesize("你好"):
            pass

    with pytest.raises(RuntimeError, match="45000000"):
        anyio.run(collect)


def test_build_hotword_context_dedupes_and_serializes() -> None:
    context = _build_hotword_context(("apple", " apple ", "", "banana"))
    assert json.loads(context) == {"hotwords": [{"word": "apple"}, {"word": "banana"}]}


def test_build_hotword_context_empty_returns_empty_string() -> None:
    assert _build_hotword_context(()) == ""
    assert _build_hotword_context(("", "   ")) == ""


def test_asr_request_payload_injects_scene_hotwords() -> None:
    config = VolcASRConfig(api_key="k", resource_id="r", hotwords=("hello", "apple"))
    session = VolcASRStreamSession(config, sample_rate_hz=16000)
    payload = session._build_request_payload()
    context = payload["request"]["corpus"]["context"]
    assert json.loads(context) == {"hotwords": [{"word": "hello"}, {"word": "apple"}]}


def test_asr_request_payload_omits_corpus_without_hotwords() -> None:
    session = VolcASRStreamSession(
        VolcASRConfig(api_key="k", resource_id="r"), sample_rate_hz=16000
    )
    assert "corpus" not in session._build_request_payload()["request"]


def test_build_full_client_request_roundtrip() -> None:
    payload = {"audio": {"format": "pcm"}, "request": {"model_name": "bigmodel"}}
    frame = _build_full_client_request(payload)

    # Protocol version 1, header size 1 word (4 bytes).
    assert frame[0] == (0b0001 << 4) | 0b0001
    # Full client request, no sequence flag.
    assert (frame[1] >> 4) == 0b0001
    assert (frame[1] & 0x0F) == 0b0000
    # JSON serialization, gzip compression.
    assert (frame[2] >> 4) == 0b0001
    assert (frame[2] & 0x0F) == 0b0001

    size = struct.unpack_from(">I", frame, 4)[0]
    body = frame[8 : 8 + size]
    assert json.loads(gzip.decompress(body)) == payload


def test_build_audio_request_marks_last_packet() -> None:
    mid = _build_audio_request(b"\x01\x02", last=False)
    last = _build_audio_request(b"", last=True)

    assert (mid[1] >> 4) == 0b0010  # audio only request
    assert (mid[1] & 0x0F) == 0b0000  # not last
    assert (last[1] & 0x0F) == 0b0010  # last packet (negative)

    size = struct.unpack_from(">I", mid, 4)[0]
    assert gzip.decompress(mid[8 : 8 + size]) == b"\x01\x02"


def _fake_server_frame(payload: dict, *, last: bool = False) -> bytes:
    flags = 0b0011 if last else 0b0001
    header = _generate_header(0b1001, flags, 0b0001, 0b0001)
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return header + struct.pack(">i", 1) + struct.pack(">I", len(body)) + body


def test_parse_server_message_decodes_full_response() -> None:
    frame = _fake_server_frame({"result": {"text": "你好世界"}}, last=True)
    message = _parse_server_message(frame)

    assert message.message_type == 0b1001
    assert message.flags == 0b0011
    assert message.sequence == 1
    assert message.payload == {"result": {"text": "你好世界"}}


def test_parse_server_message_decodes_error_frame() -> None:
    header = _generate_header(0b1111, 0b0000, 0b0001, 0b0000)
    detail = b"bad request"
    frame = header + struct.pack(">I", 45000001) + struct.pack(">I", len(detail)) + detail
    message = _parse_server_message(frame)

    assert message.message_type == 0b1111
    assert message.error_code == 45000001
    assert message.error_message == "bad request"


def test_format_sauc_error_includes_code_and_advice() -> None:
    message = _format_sauc_error(
        "volc sauc asr server error",
        code=45000010,
        detail="invalid api key",
        resource_id="volc.bigasr.sauc.duration",
    )
    assert "45000010" in message
    assert "resource_id=volc.bigasr.sauc.duration" in message
    assert "X-Api-Key" in message


class _FakeWS:
    """Minimal stand-in for a websockets client connection."""

    def __init__(self, server_frames: list[bytes]) -> None:
        self.sent: list[bytes] = []
        self._frames = list(server_frames)
        self.closed = False

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> bytes:
        if not self._frames:
            # Block until cancelled (mirrors a quiet server connection).
            import anyio

            await anyio.sleep_forever()
        return self._frames.pop(0)

    async def close(self) -> None:
        self.closed = True


def test_volc_asr_streaming_session_feeds_and_finishes(monkeypatch) -> None:
    import anyio

    fake_ws = _FakeWS(
        [
            _fake_server_frame({"result": {"text": "讲一个"}}),
            _fake_server_frame({"result": {"text": "讲一个恐龙故事"}}, last=True),
        ]
    )
    captured_headers: dict[str, str] = {}

    async def fake_connect(url, *, additional_headers=None, **kwargs):
        captured_headers.update(additional_headers or {})
        return fake_ws

    monkeypatch.setattr(asr_module.websockets, "connect", fake_connect)

    service = VolcASRService(
        config=VolcASRConfig(api_key="api-key-123", resource_id="volc.bigasr.sauc.duration")
    )
    partials: list[str] = []

    async def collect_partial(text: str) -> None:
        partials.append(text)

    async def run() -> str:
        session = service.open_stream(sample_rate_hz=16000, on_partial=collect_partial)
        await session.feed(b"\x01\x00\x02\x00")
        return await session.finish()

    text = anyio.run(run)

    assert text == "讲一个恐龙故事"
    assert partials == ["讲一个", "讲一个恐龙故事"]
    assert captured_headers["X-Api-Key"] == "api-key-123"
    assert captured_headers["X-Api-Resource-Id"] == "volc.bigasr.sauc.duration"
    assert captured_headers["X-Api-Sequence"] == "-1"
    assert "X-Api-Request-Id" in captured_headers
    assert "X-Api-Connect-Id" in captured_headers
    # full client request + one audio packet + the final (empty) packet.
    assert len(fake_ws.sent) == 3
    assert (fake_ws.sent[0][1] >> 4) == 0b0001
    assert (fake_ws.sent[-1][1] & 0x0F) == 0b0010
    assert fake_ws.closed


def test_volc_asr_coalesces_device_frames_into_200ms_packets(monkeypatch) -> None:
    import anyio

    fake_ws = _FakeWS([_fake_server_frame({"result": {"text": "你好"}}, last=True)])

    async def fake_connect(url, *, additional_headers=None, **kwargs):
        return fake_ws

    monkeypatch.setattr(asr_module.websockets, "connect", fake_connect)
    session = VolcASRStreamSession(
        VolcASRConfig(api_key="k", resource_id="r"), sample_rate_hz=16000
    )

    async def run() -> str:
        # Ten 20ms mono s16le frames become one 200ms upstream audio packet.
        for _ in range(10):
            await session.feed(b"\x01\x00" * 320)
        return await session.finish()

    assert anyio.run(run) == "你好"
    assert len(fake_ws.sent) == 3  # config + 200ms audio + final marker
    packet_size = struct.unpack_from(">I", fake_ws.sent[1], 4)[0]
    packet = gzip.decompress(fake_ws.sent[1][8 : 8 + packet_size])
    assert len(packet) == 16000 * 2 // 5


def test_volc_asr_open_stream_requires_api_key() -> None:
    service = VolcASRService(config=VolcASRConfig(api_key="", resource_id="volc.bigasr.sauc.duration"))
    with pytest.raises(RuntimeError, match="X-Api-Key"):
        service.open_stream()
