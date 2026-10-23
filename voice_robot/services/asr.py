from __future__ import annotations

import asyncio
import gzip
import json
import struct
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

import anyio
import websockets

# --- Volc 双向流式 ASR (sauc/bigmodel) 二进制协议常量 ---------------------------
# 协议参考：docs/knowledge/integrations/ASR.md
_PROTOCOL_VERSION = 0b0001
_HEADER_SIZE_WORDS = 0b0001  # header 大小 = 1 x 4 = 4 字节

# message type
_MSG_FULL_CLIENT_REQUEST = 0b0001
_MSG_AUDIO_ONLY_REQUEST = 0b0010
_MSG_FULL_SERVER_RESPONSE = 0b1001
_MSG_ERROR = 0b1111

# message type specific flags
_FLAG_NONE = 0b0000  # header 后无 sequence
_FLAG_POS_SEQUENCE = 0b0001  # header 后为正 sequence
_FLAG_LAST_NO_SEQUENCE = 0b0010  # 最后一包（负包），无 sequence
_FLAG_NEG_SEQUENCE_LAST = 0b0011  # 最后一包（负包），带负 sequence

# serialization
_SERIALIZATION_RAW = 0b0000
_SERIALIZATION_JSON = 0b0001

# compression
_COMPRESSION_NONE = 0b0000
_COMPRESSION_GZIP = 0b0001


class ASRStreamSession(Protocol):
    async def feed(self, pcm: bytes) -> None:
        """Send a chunk of PCM audio to the streaming recognizer."""

    async def finish(self) -> str:
        """Signal end of input and return the final transcript."""

    async def aclose(self) -> None:
        """Release the underlying connection. Safe to call multiple times."""


OnPartial = Callable[[str], Awaitable[None]]


class ASRService(Protocol):
    def open_stream(
        self,
        *,
        sample_rate_hz: int = 16000,
        on_partial: OnPartial | None = None,
    ) -> ASRStreamSession:
        """Open a streaming recognition session."""

    async def transcribe(self, audio: bytes) -> str:
        """Convert a full audio buffer to text (compatibility helper)."""


class _LocalASRStreamSession:
    def __init__(self) -> None:
        self._buffer = bytearray()

    async def feed(self, pcm: bytes) -> None:
        if pcm:
            self._buffer += pcm

    async def finish(self) -> str:
        try:
            text = bytes(self._buffer).decode("utf-8").strip()
        except UnicodeDecodeError:
            return "我听到一段语音"
        return text or "我听到一段语音"

    async def aclose(self) -> None:
        self._buffer = bytearray()


class LocalASRService:
    def open_stream(
        self,
        *,
        sample_rate_hz: int = 16000,
        on_partial: OnPartial | None = None,
    ) -> _LocalASRStreamSession:
        return _LocalASRStreamSession()

    async def transcribe(self, audio: bytes) -> str:
        session = _LocalASRStreamSession()
        await session.feed(audio)
        return await session.finish()


@dataclass(frozen=True)
class VolcASRConfig:
    # New console APP Key, sent as the X-Api-Key header.
    api_key: str = ""
    # 流式语音识别 2.0 资源 ID（小时版 duration / 并发版 concurrent）。
    resource_id: str = "volc.bigasr.sauc.duration"
    ws_url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    sample_rate_hz: int = 16000
    model_name: str = "bigmodel"
    enable_itn: bool = True
    enable_punc: bool = True
    enable_ddc: bool = False
    show_utterances: bool = True
    connect_timeout_seconds: float = 10.0
    recv_timeout_seconds: float = 30.0
    # The provider recommends 100-200 ms packets and documents 200 ms as the
    # best-performing size for bidirectional streaming. Device frames are 20 ms,
    # so coalesce them here without changing the device protocol.
    packet_duration_ms: int = 200
    # 场景热词：注入 request.corpus.context 的 hotwords，把识别软性偏置到这些词
    # 上（如英语场景的常见英文单词）。空 = 不注入。双向流式上限约 100 tokens。
    hotwords: tuple[str, ...] = ()


class VolcASRStreamSession:
    def __init__(
        self,
        config: VolcASRConfig,
        *,
        sample_rate_hz: int,
        on_partial: OnPartial | None = None,
    ) -> None:
        self._config = config
        self._sample_rate_hz = sample_rate_hz
        self._on_partial = on_partial
        self._ws: Any = None
        self._reader_task: Any = None
        self._final_event = anyio.Event()
        self._latest_text = ""
        self._error: Exception | None = None
        self._fed_any = False
        self._closed = False
        self._pending_audio = bytearray()
        self._packet_bytes = max(
            2,
            self._sample_rate_hz * 2 * max(20, self._config.packet_duration_ms) // 1000,
        )

    async def _ensure_connected(self) -> None:
        if self._ws is not None:
            return
        headers = {
            "X-Api-Key": self._config.api_key.strip(),
            "X-Api-Resource-Id": self._config.resource_id.strip(),
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Connect-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        }
        try:
            with anyio.fail_after(self._config.connect_timeout_seconds):
                self._ws = await websockets.connect(
                    self._config.ws_url,
                    additional_headers=headers,
                    max_size=None,
                )
        except TimeoutError as exc:
            raise RuntimeError(
                f"volc sauc asr connect timed out (url={self._config.ws_url})"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - surface connection failures
            raise RuntimeError(f"volc sauc asr connect failed: {exc}") from exc

        await self._ws.send(_build_full_client_request(self._build_request_payload()))
        self._reader_task = asyncio.ensure_future(self._reader_loop())

    def _build_request_payload(self) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model_name": self._config.model_name,
            "enable_itn": self._config.enable_itn,
            "enable_punc": self._config.enable_punc,
            "enable_ddc": self._config.enable_ddc,
            "show_utterances": self._config.show_utterances,
            "result_type": "full",
        }
        context = _build_hotword_context(self._config.hotwords)
        if context:
            request["corpus"] = {"context": context}
        return {
            "user": {"uid": "voice_robot"},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": self._sample_rate_hz,
                "bits": 16,
                "channel": 1,
            },
            "request": request,
        }

    async def _reader_loop(self) -> None:
        try:
            while True:
                frame = await self._ws.recv()
                if isinstance(frame, str):
                    frame = frame.encode("utf-8")
                message = _parse_server_message(frame)
                if message.message_type == _MSG_ERROR:
                    self._error = RuntimeError(
                        _format_sauc_error(
                            "volc sauc asr server error",
                            code=message.error_code,
                            detail=message.error_message,
                            resource_id=self._config.resource_id,
                        )
                    )
                    break
                text = _extract_asr_text(message.payload or {})
                if text and text != self._latest_text:
                    self._latest_text = text
                    if self._on_partial is not None:
                        await self._on_partial(text)
                if message.flags in (_FLAG_LAST_NO_SEQUENCE, _FLAG_NEG_SEQUENCE_LAST):
                    break
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as exc:  # noqa: BLE001 - connection drop / decode error
            if self._error is None:
                self._error = RuntimeError(f"volc sauc asr stream failed: {exc}")
        finally:
            self._final_event.set()

    async def feed(self, pcm: bytes) -> None:
        if not pcm:
            return
        await self._ensure_connected()
        if self._error is not None:
            raise self._error
        self._fed_any = True
        self._pending_audio.extend(pcm)
        while len(self._pending_audio) >= self._packet_bytes:
            packet = bytes(self._pending_audio[: self._packet_bytes])
            del self._pending_audio[: self._packet_bytes]
            await self._ws.send(_build_audio_request(packet, last=False))

    async def finish(self) -> str:
        await self._ensure_connected()
        if self._error is None:
            if self._pending_audio:
                await self._ws.send(
                    _build_audio_request(bytes(self._pending_audio), last=False)
                )
                self._pending_audio.clear()
            await self._ws.send(_build_audio_request(b"", last=True))
            try:
                with anyio.fail_after(self._config.recv_timeout_seconds):
                    await self._final_event.wait()
            except TimeoutError:
                self._error = RuntimeError(
                    "volc sauc asr query timed out waiting for final result "
                    f"(resource_id={self._config.resource_id})"
                )
        await self.aclose()
        if self._error is not None:
            raise self._error
        return self._latest_text

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending_audio.clear()
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except BaseException:  # noqa: BLE001 - best-effort teardown
                pass
            self._reader_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
            self._ws = None


class VolcASRService:
    def __init__(self, *, config: VolcASRConfig) -> None:
        self.config = config

    def open_stream(
        self,
        *,
        sample_rate_hz: int | None = None,
        on_partial: OnPartial | None = None,
    ) -> VolcASRStreamSession:
        if not self.config.api_key.strip():
            raise RuntimeError(
                "volc sauc asr auth is not configured (need new-console X-Api-Key)"
            )
        if not self.config.resource_id.strip():
            raise RuntimeError("volc sauc asr resource_id is not configured")
        return VolcASRStreamSession(
            self.config,
            sample_rate_hz=sample_rate_hz or self.config.sample_rate_hz,
            on_partial=on_partial,
        )

    async def transcribe(self, audio: bytes) -> str:
        session = self.open_stream()
        try:
            await session.feed(audio)
            return await session.finish()
        finally:
            await session.aclose()


# --- 二进制协议编解码（纯函数，便于单测） -------------------------------------


@dataclass(frozen=True)
class ParsedServerMessage:
    message_type: int
    flags: int
    sequence: int | None
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


def _build_full_client_request(payload: dict[str, Any], *, compress: bool = True) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    compression = _COMPRESSION_NONE
    if compress:
        body = gzip.compress(body)
        compression = _COMPRESSION_GZIP
    header = _generate_header(
        _MSG_FULL_CLIENT_REQUEST,
        _FLAG_NONE,
        _SERIALIZATION_JSON,
        compression,
    )
    return header + struct.pack(">I", len(body)) + body


def _build_audio_request(audio: bytes, *, last: bool = False, compress: bool = True) -> bytes:
    body = audio
    compression = _COMPRESSION_NONE
    if compress:
        body = gzip.compress(audio)
        compression = _COMPRESSION_GZIP
    flags = _FLAG_LAST_NO_SEQUENCE if last else _FLAG_NONE
    header = _generate_header(
        _MSG_AUDIO_ONLY_REQUEST,
        flags,
        _SERIALIZATION_RAW,
        compression,
    )
    return header + struct.pack(">I", len(body)) + body


def _parse_server_message(frame: bytes) -> ParsedServerMessage:
    if len(frame) < 4:
        raise ValueError("volc sauc asr frame too short")
    header_size_words = frame[0] & 0x0F
    header_len = header_size_words * 4
    message_type = (frame[1] >> 4) & 0x0F
    flags = frame[1] & 0x0F
    compression = frame[2] & 0x0F
    offset = header_len

    if message_type == _MSG_ERROR:
        error_code = struct.unpack_from(">I", frame, offset)[0]
        offset += 4
        msg_size = struct.unpack_from(">I", frame, offset)[0]
        offset += 4
        raw = frame[offset : offset + msg_size]
        if compression == _COMPRESSION_GZIP and raw:
            raw = gzip.decompress(raw)
        return ParsedServerMessage(
            message_type=message_type,
            flags=flags,
            sequence=None,
            payload=None,
            error_code=error_code,
            error_message=raw.decode("utf-8", errors="replace"),
        )

    sequence: int | None = None
    if flags in (_FLAG_POS_SEQUENCE, _FLAG_NEG_SEQUENCE_LAST):
        sequence = struct.unpack_from(">i", frame, offset)[0]
        offset += 4

    payload_size = struct.unpack_from(">I", frame, offset)[0]
    offset += 4
    raw = frame[offset : offset + payload_size]
    if compression == _COMPRESSION_GZIP and raw:
        raw = gzip.decompress(raw)
    payload: dict[str, Any] | None = None
    if raw.strip():
        decoded = json.loads(raw.decode("utf-8"))
        if isinstance(decoded, dict):
            payload = decoded
    return ParsedServerMessage(
        message_type=message_type,
        flags=flags,
        sequence=sequence,
        payload=payload,
        error_code=None,
        error_message="",
    )


def _format_sauc_error(
    prefix: str,
    *,
    code: int | None,
    detail: str,
    resource_id: str,
) -> str:
    parts = [prefix]
    if code is not None:
        parts.append(f"code={code}")
    parts.append(f"resource_id={resource_id}")
    if detail:
        parts.append(f"detail={detail[:500]}")
    advice = _sauc_error_advice(code, resource_id=resource_id)
    if advice:
        parts.append(f"advice={advice}")
    if len(parts) == 1:
        return prefix
    return f"{prefix} (" + ", ".join(parts[1:]) + ")"


def _sauc_error_advice(code: int | None, *, resource_id: str) -> str:
    if code in (45000001,):
        return "请求参数无效；请检查 resource_id 与请求字段是否匹配流式语音识别 2.0"
    if code in (45000002,):
        return "空音频；请确认麦克风有声音且发送了音频分片"
    if code in (45000081,):
        return "等包超时；请检查发包节奏（建议 100~200ms/包）与网络连通性"
    if code in (45000010, 55000031):
        return (
            f"鉴权或服务异常；请确认火山语音新版控制台 X-Api-Key 有效且已授权 {resource_id}"
        )
    return ""


def _build_hotword_context(hotwords: tuple[str, ...]) -> str:
    """火山 `request.corpus.context` 的热词直传形式：序列化为 JSON 字符串
    `{"hotwords":[{"word":"..."}]}`。双向流式上限约 100 tokens，这里去重去空。
    返回空串表示不注入。"""
    seen: set[str] = set()
    words: list[dict[str, str]] = []
    for raw in hotwords:
        word = str(raw or "").strip()
        if word and word not in seen:
            seen.add(word)
            words.append({"word": word})
    if not words:
        return ""
    return json.dumps({"hotwords": words}, ensure_ascii=False)


def _extract_asr_text(data: dict) -> str:
    result = data.get("result")
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        utterances = result.get("utterances")
        if isinstance(utterances, list):
            parts = []
            for item in utterances:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"].strip())
            joined = "".join(parts).strip()
            if joined:
                return joined
    utterances = data.get("utterances")
    if isinstance(utterances, list):
        parts = []
        for item in utterances:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"].strip())
        return "".join(parts).strip()
    text = data.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""
