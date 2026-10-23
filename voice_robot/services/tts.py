from __future__ import annotations

import gzip
import json
import math
import struct
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

import anyio
import websockets


# --- Volc V3 单向流式 TTS (unidirectional/stream) 二进制协议常量 -----------------
# 协议参考：docs/knowledge/integrations/HTTP-Chunked-V3.md（实为 WebSocket 单向流式二进制协议）
_PROTOCOL_VERSION = 0b0001
_HEADER_SIZE_WORDS = 0b0001  # header 大小 = 1 x 4 = 4 字节

# message type
_MSG_FULL_CLIENT_REQUEST = 0b0001  # 上行 SendText / FinishConnection
_MSG_AUDIO_ONLY_RESPONSE = 0b1011  # 下行音频帧（payload 为 raw PCM）
_MSG_FULL_SERVER_RESPONSE = 0b1001  # 下行 JSON 控制帧
_MSG_ERROR = 0b1111  # 下行错误帧

# message type specific flags
_FLAG_NONE = 0b0000  # 上行 SendText：无 event number
_FLAG_WITH_EVENT = 0b0100  # 带 event number（上行 FinishConnection / 所有下行帧）

# serialization
_SERIALIZATION_RAW = 0b0000
_SERIALIZATION_JSON = 0b0001

# compression
_COMPRESSION_NONE = 0b0000
_COMPRESSION_GZIP = 0b0001

# event codes
_EVENT_FINISH_CONNECTION = 2
_EVENT_CONNECTION_FINISHED = 52
_EVENT_SESSION_FINISHED = 152
_EVENT_TTS_SENTENCE_START = 350
_EVENT_TTS_SENTENCE_END = 351
_EVENT_TTS_RESPONSE = 352

_TTS_SUCCESS_STATUS = 20000000


class TTSService(Protocol):
    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """Convert text to one or more audio chunks."""
        yield b""


@dataclass(frozen=True)
class LocalTTSService:
    sample_rate_hz: int = 16000

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        yield self._render_debug_tone(cleaned)

    def _render_debug_tone(self, text: str) -> bytes:
        # Local fallback uses an audible PCM tone instead of returning UTF-8 text bytes.
        # This keeps the Web playback path debuggable even when upstream TTS is not configured.
        sample_rate = max(8000, int(self.sample_rate_hz or 16000))
        duration_seconds = max(0.35, min(2.4, len(text) * 0.055))
        total_samples = max(1, int(sample_rate * duration_seconds))
        fade_samples = max(1, int(sample_rate * 0.01))
        amplitude = 0.22
        freq_a = 440.0
        freq_b = 660.0
        pcm = bytearray(total_samples * 2)

        for index in range(total_samples):
            t = index / sample_rate
            mix = (
                math.sin(2 * math.pi * freq_a * t) * 0.65
                + math.sin(2 * math.pi * freq_b * t) * 0.35
            )
            envelope = 1.0
            if index < fade_samples:
                envelope = index / fade_samples
            elif index >= total_samples - fade_samples:
                envelope = max(0.0, (total_samples - index - 1) / fade_samples)
            value = int(max(-1.0, min(1.0, mix * amplitude * envelope)) * 32767)
            pcm[index * 2 : index * 2 + 2] = int(value).to_bytes(
                2, byteorder="little", signed=True
            )
        return bytes(pcm)


@dataclass(frozen=True)
class VolcTTSConfig:
    # New console APP Key, sent as the X-Api-Key header.
    api_key: str = ""
    # 语音合成大模型资源 ID（字符版 seed-tts-1.0 / 并发版 seed-tts-1.0-concurr / 2.0）。
    resource_id: str = "seed-tts-1.0"
    voice_type: str = ""
    ws_url: str = "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream"
    sample_rate_hz: int = 16000
    encoding: str = "pcm"  # 流式必须 pcm（wav 会多次返回 header）
    connect_timeout_seconds: float = 10.0
    recv_timeout_seconds: float = 30.0


class VolcTTSService:
    def __init__(self, *, config: VolcTTSConfig) -> None:
        self.config = config

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        if not self.config.api_key.strip():
            raise RuntimeError("volc tts auth is not configured (need new-console X-Api-Key)")
        if not self.config.voice_type.strip():
            raise RuntimeError("volc tts voice_type is not configured")
        if not text.strip():
            raise RuntimeError("volc tts text is empty")

        headers = {
            "X-Api-Key": self.config.api_key.strip(),
            "X-Api-Resource-Id": self.config.resource_id.strip(),
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        ws: Any = None
        try:
            try:
                with anyio.fail_after(self.config.connect_timeout_seconds):
                    ws = await websockets.connect(
                        self.config.ws_url,
                        additional_headers=headers,
                        max_size=None,
                    )
            except TimeoutError as exc:
                raise RuntimeError(
                    f"volc tts connect timed out (url={self.config.ws_url})"
                ) from exc
            except Exception as exc:  # noqa: BLE001 - surface connection failures
                raise RuntimeError(f"volc tts connect failed: {exc}") from exc

            await ws.send(build_send_text(self._build_request_payload(text)))

            while True:
                try:
                    with anyio.fail_after(self.config.recv_timeout_seconds):
                        frame = await ws.recv()
                except TimeoutError as exc:
                    raise RuntimeError(
                        f"volc tts recv timed out (resource_id={self.config.resource_id})"
                    ) from exc
                if isinstance(frame, str):
                    frame = frame.encode("utf-8")
                message = parse_server_frame(frame)
                if message.message_type == _MSG_ERROR:
                    raise RuntimeError(
                        _format_tts_error(
                            code=message.error_code,
                            detail=message.error_message,
                            resource_id=self.config.resource_id,
                        )
                    )
                if message.event == _EVENT_TTS_RESPONSE and message.audio:
                    yield message.audio
                elif message.event == _EVENT_SESSION_FINISHED:
                    status = (message.payload or {}).get("status_code")
                    if status not in (None, _TTS_SUCCESS_STATUS):
                        raise RuntimeError(
                            f"volc tts session failed: status_code={status} "
                            f"(resource_id={self.config.resource_id})"
                        )
                    break
                # 350/351 SentenceStart/End、52 ConnectionFinished 等控制帧忽略。
        finally:
            if ws is not None:
                try:
                    await ws.send(build_finish_connection())
                except Exception:  # noqa: BLE001 - best-effort graceful close
                    pass
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001 - best-effort teardown
                    pass

    def _build_request_payload(self, text: str) -> dict[str, Any]:
        return {
            "user": {"uid": "voice_robot"},
            "req_params": {
                "text": text,
                "speaker": self.config.voice_type,
                "audio_params": {
                    "format": self.config.encoding,
                    "sample_rate": self.config.sample_rate_hz,
                },
            },
        }


# --- 二进制协议编解码（纯函数，便于单测） -------------------------------------


@dataclass(frozen=True)
class ParsedTTSFrame:
    message_type: int
    event: int | None
    session_id: str
    audio: bytes
    payload: dict[str, Any] | None
    error_code: int | None
    error_message: str


def _generate_header(
    message_type: int,
    flags: int,
    serialization: int,
    compression: int,
) -> bytes:
    return bytes(
        [
            (_PROTOCOL_VERSION << 4) | _HEADER_SIZE_WORDS,
            (message_type << 4) | flags,
            (serialization << 4) | compression,
            0x00,
        ]
    )


def build_send_text(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = _generate_header(
        _MSG_FULL_CLIENT_REQUEST,
        _FLAG_NONE,
        _SERIALIZATION_JSON,
        _COMPRESSION_NONE,
    )
    return header + struct.pack(">I", len(body)) + body


def build_finish_connection() -> bytes:
    body = b"{}"
    header = _generate_header(
        _MSG_FULL_CLIENT_REQUEST,
        _FLAG_WITH_EVENT,
        _SERIALIZATION_JSON,
        _COMPRESSION_NONE,
    )
    return (
        header
        + struct.pack(">I", _EVENT_FINISH_CONNECTION)
        + struct.pack(">I", len(body))
        + body
    )


def parse_server_frame(frame: bytes) -> ParsedTTSFrame:
    if len(frame) < 4:
        raise ValueError("volc tts frame too short")
    header_len = (frame[0] & 0x0F) * 4
    message_type = (frame[1] >> 4) & 0x0F
    flags = frame[1] & 0x0F
    compression = frame[2] & 0x0F
    offset = header_len

    if message_type == _MSG_ERROR:
        error_code = struct.unpack_from(">I", frame, offset)[0]
        offset += 4
        raw = frame[offset:]
        if compression == _COMPRESSION_GZIP and raw:
            raw = gzip.decompress(raw)
        return ParsedTTSFrame(
            message_type=message_type,
            event=None,
            session_id="",
            audio=b"",
            payload=None,
            error_code=error_code,
            error_message=raw.decode("utf-8", errors="replace"),
        )

    event: int | None = None
    if flags & _FLAG_WITH_EVENT:
        event = struct.unpack_from(">I", frame, offset)[0]
        offset += 4

    # 下行数据/会话帧（350/351/352/152/52）在 event 之后、payload 之前夹一个
    # session_id（或 connection_id）段：len(4B) + bytes。
    session_id = ""
    if event is not None:
        sid_len = struct.unpack_from(">I", frame, offset)[0]
        offset += 4
        session_id = frame[offset : offset + sid_len].decode("utf-8", errors="replace")
        offset += sid_len

    payload_size = struct.unpack_from(">I", frame, offset)[0]
    offset += 4
    raw = frame[offset : offset + payload_size]
    if compression == _COMPRESSION_GZIP and raw:
        raw = gzip.decompress(raw)

    if message_type == _MSG_AUDIO_ONLY_RESPONSE:
        return ParsedTTSFrame(
            message_type=message_type,
            event=event,
            session_id=session_id,
            audio=raw,
            payload=None,
            error_code=None,
            error_message="",
        )

    payload: dict[str, Any] | None = None
    if raw.strip():
        decoded = json.loads(raw.decode("utf-8"))
        if isinstance(decoded, dict):
            payload = decoded
    return ParsedTTSFrame(
        message_type=message_type,
        event=event,
        session_id=session_id,
        audio=b"",
        payload=payload,
        error_code=None,
        error_message="",
    )


def _format_tts_error(
    *,
    code: int | None,
    detail: str,
    resource_id: str,
) -> str:
    parts = ["volc tts server error"]
    if code is not None:
        parts.append(f"code={code}")
    parts.append(f"resource_id={resource_id}")
    if detail:
        parts.append(f"detail={detail[:500]}")
    advice = _tts_error_advice(code, resource_id=resource_id)
    if advice:
        parts.append(f"advice={advice}")
    return "volc tts server error (" + ", ".join(parts[1:]) + ")"


def _tts_error_advice(code: int | None, *, resource_id: str) -> str:
    if code == 45000000:
        return (
            f"音色鉴权失败或并发限流；请确认 X-Api-Key 有效、voice_type 已授权，"
            f"且 resource_id={resource_id} 与音色版本匹配"
        )
    if code == 55000000:
        return "服务端错误；请稍后重试"
    return ""
