from pathlib import Path

import anyio
import hashlib
import json
import random
import time
import uuid
from dataclasses import dataclass
from urllib.parse import quote
from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from voice_robot.admin_api import (
    _build_runtime_agent,
    _build_runtime_llm_service,
    classify_scene_intent,
)
from voice_robot.admin_api import router as admin_router
from voice_robot.agent.graph import VoiceAgent
from voice_robot.agent.graph import stream_text_chunks
from voice_robot.agent.state import AgentResult
from voice_robot.character_trace import (
    character_trace_payload,
    extract_trace_character,
)
from voice_robot.character_trace_narration import build_trace_narration
from voice_robot.config import get_settings
from voice_robot.memory.audit_log_store import AuditLogStore
from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.idiom_explanation_store import IdiomExplanationStore
from voice_robot.memory.prompt_voice_store import PromptVoiceCacheStore, prompt_text_hash
from voice_robot.memory.tts_response_cache import TTSResponseCacheStore
from voice_robot.memory.vocab_explanation_store import VocabExplanationStore
from voice_robot.memory.vocabulary_store import VocabularyStore
from voice_robot.scenes.controller import SceneDecision, decide_scene_action
from voice_robot.scenes.intent import match_enter_keyword
from voice_robot.scenes.registry import effective_scene, effective_scenes
from voice_robot.idiom_intent import extract_idiom_query
from voice_robot.metrics.context import reset_current_scenario, set_current_scenario
from voice_robot.protocol import ClientControlMessage, ServerControlMessage
from voice_robot.runtime_config import (
    RuntimeConfigStore,
    resolve_speech_asr_api_key,
    resolve_speech_asr_resource_id,
    resolve_speech_asr_sample_rate_hz,
    resolve_speech_asr_ws_url,
    resolve_speech_tts_api_key,
    resolve_speech_tts_encoding,
    resolve_speech_tts_resource_id,
    resolve_speech_tts_sample_rate_hz,
    resolve_speech_tts_voice_type,
    resolve_speech_tts_ws_url,
)
from voice_robot.services.asr import (
    ASRService,
    ASRStreamSession,
    LocalASRService,
    VolcASRConfig,
    VolcASRService,
)
from voice_robot.services.opus_codec import (
    OpusCodecUnavailable,
    OpusDecoder16k,
    RawOpusDecoder16k,
)
from voice_robot.services.tts import LocalTTSService, TTSService, VolcTTSConfig, VolcTTSService
app = FastAPI()
app.include_router(admin_router)

class VersionedStaticFiles(StaticFiles):
    """Cache versioned assets aggressively while revalidating bare URLs."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        query = scope.get("query_string", b"")
        if b"v=" in query:
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["cache-control"] = "no-cache"
        return response


WEB_CONSOLE_DIR = Path(__file__).resolve().parent.parent / "web_console"
if WEB_CONSOLE_DIR.exists():
    app.mount(
        "/console/assets",
        VersionedStaticFiles(directory=WEB_CONSOLE_DIR),
        name="console-assets",
    )


CONSOLE_DEVICE_ID = "web-console"
MAX_AUDIO_FRAME_BYTES = 64 * 1024
MAX_AUDIO_SEGMENT_BYTES = 48_000 * 2 * 30  # mono s16le worst case, 30 seconds
MAX_AUDIO_SEGMENT_SECONDS = 30.0


@dataclass(frozen=True)
class CharacterTracePlan:
    payload: dict
    narration: str


@dataclass
class IdiomDisplayPlan:
    """Console 「成语学习」 dispatch: show an idiom as big characters (static fill,
    no stroke reveal) while the device speaks `narration` verbatim (no LLM)."""

    idiom: str
    pinyin: str
    glyphs: list
    narration: str


@dataclass(frozen=True)
class ScenePromptPlan:
    """场景进入/退出的固定语音提示：念 `narration` verbatim（不调对话 LLM），
    音频取自服务端持久化缓存（命中即零 TTS Token）。`action ∈ {enter, exit}`。"""

    scene_id: str
    action: str
    name: str
    narration: str


# In-process registry of online /ws connections, keyed by a per-connection id.
# Only JSON-safe fields live here so GET /api/devices can return it directly.
_online_connections: dict[int, dict[str, object]] = {}

# Runtime-only objects (not JSON-safe): the command stream used to push
# "say" requests into a connection's own task, keyed by the same conn id.
_connection_runtime: dict[int, dict[str, object]] = {}
_connection_order = 0

# device_id -> set of SSE subscriber send streams (live session monitoring).
_device_subscribers: dict[str, set] = {}


def _device_kind(device_id: str) -> str:
    return "console" if device_id == CONSOLE_DEVICE_ID else "hardware"


def _register_connection(
    conn_key: int,
    device_id: str,
    *,
    transport: str = "wifi_direct",
    gateway_id: str | None = None,
    command_send=None,
) -> None:
    global _connection_order
    now = time.time()
    _connection_order += 1
    _online_connections[conn_key] = {
        "device_id": device_id,
        "kind": _device_kind(device_id),
        "transport": transport,
        "gateway_id": gateway_id,
        "connected_at": now,
        "last_seen": now,
        "connected_order": _connection_order,
        "last_seen_order": _connection_order,
    }
    _connection_runtime[conn_key] = {
        "device_id": device_id,
        "command_send": command_send,
    }


def _touch_connection(conn_key: int) -> None:
    global _connection_order
    entry = _online_connections.get(conn_key)
    if entry is not None:
        _connection_order += 1
        entry["last_seen"] = time.time()
        entry["last_seen_order"] = _connection_order


def _unregister_connection(conn_key: int) -> None:
    _online_connections.pop(conn_key, None)
    _connection_runtime.pop(conn_key, None)


def _command_send_for_device(device_id: str):
    """Return the best online connection for device_id.

    Preference order:
    1. `wifi_direct`
    2. `ble_mobile_gateway`
    3. most recent `last_seen` / `connected_at`
    """
    candidates: list[tuple[tuple[int, int, int, float, float], object]] = []
    for conn_key, entry in _online_connections.items():
        if entry.get("device_id") != device_id:
            continue
        runtime = _connection_runtime.get(conn_key)
        if runtime is None:
            continue
        command_send = runtime.get("command_send")
        if command_send is None:
            continue
        transport = str(entry.get("transport") or "")
        transport_rank = 0 if transport == "wifi_direct" else 1
        last_seen = float(entry.get("last_seen") or 0.0)
        connected_at = float(entry.get("connected_at") or 0.0)
        last_seen_order = int(entry.get("last_seen_order") or 0)
        connected_order = int(entry.get("connected_order") or 0)
        candidates.append(
            (
                (
                    transport_rank,
                    -last_seen_order,
                    -connected_order,
                    -last_seen,
                    -connected_at,
                ),
                command_send,
            )
        )
    if candidates:
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]
    return None


def _subscribe_device(device_id: str, stream) -> None:
    _device_subscribers.setdefault(device_id, set()).add(stream)


def _unsubscribe_device(device_id: str, stream) -> None:
    subscribers = _device_subscribers.get(device_id)
    if subscribers is not None:
        subscribers.discard(stream)
        if not subscribers:
            _device_subscribers.pop(device_id, None)


def _publish_event(device_id: str, event: dict) -> None:
    """Fan-out a session event to all SSE subscribers of device_id (non-blocking)."""
    subscribers = _device_subscribers.get(device_id)
    if not subscribers:
        return
    for stream in list(subscribers):
        try:
            stream.send_nowait(event)
        except Exception:
            # Drop on full/closed subscriber buffers; never block the session.
            continue


@app.get("/api/devices")
async def list_devices() -> dict[str, object]:
    devices = sorted(
        _online_connections.values(),
        key=lambda item: (item["kind"], item["device_id"], item["connected_at"]),
    )
    hardware_online = sum(1 for item in devices if item["kind"] == "hardware")
    console_online = sum(1 for item in devices if item["kind"] == "console")
    return {
        "hardware_online": hardware_online,
        "console_online": console_online,
        "devices": devices,
    }


@app.post("/api/devices/{device_id}/say")
async def device_say(device_id: str, body: dict = Body(default={})) -> dict[str, object]:
    """Push a text turn to a connected device so it synthesizes and plays TTS."""
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="text must be at most 2000 characters")
    command_send = _command_send_for_device(device_id)
    if command_send is None:
        raise HTTPException(status_code=409, detail="device not connected")
    command = {"type": "say", "text": text}
    user_id = body.get("user_id")
    if isinstance(user_id, str) and user_id:
        command["user_id"] = user_id
    try:
        command_send.send_nowait(command)
    except Exception as exc:  # full/closed buffer
        raise HTTPException(status_code=409, detail="device busy or unavailable") from exc
    return {"dispatched": True, "device_id": device_id}


@app.post("/api/devices/{device_id}/volume")
async def device_volume(device_id: str, body: dict = Body(default={})) -> dict[str, object]:
    """Set playback volume on a connected hardware device."""
    try:
        volume = int(body.get("volume", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="volume must be a number") from None
    volume = max(0, min(100, volume))
    command_send = _command_send_for_device(device_id)
    if command_send is None:
        raise HTTPException(status_code=409, detail="device not connected")
    try:
        command_send.send_nowait({"type": "set_volume", "volume": volume})
    except Exception as exc:
        raise HTTPException(status_code=409, detail="device busy or unavailable") from exc
    return {"dispatched": True, "device_id": device_id, "volume": volume}


@app.post("/api/devices/{device_id}/conversation")
async def device_conversation(device_id: str, body: dict = Body(default={})) -> dict[str, object]:
    """Remotely enter/exit hardware conversation mode (PRD 11.13.5)."""
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be a boolean")
    command_send = _command_send_for_device(device_id)
    if command_send is None:
        raise HTTPException(status_code=409, detail="device not connected")
    try:
        command_send.send_nowait({"type": "set_conversation", "enabled": enabled})
    except Exception as exc:
        raise HTTPException(status_code=409, detail="device busy or unavailable") from exc
    return {"dispatched": True, "device_id": device_id, "enabled": enabled}


@app.post("/api/devices/{device_id}/color-test")
async def device_color_test(device_id: str, body: dict = Body(default={})) -> dict[str, object]:
    """Show a hardware LCD color diagnostic screen."""
    color = str(body.get("color", "bars")).strip().lower()
    allowed = {"bars", "red", "green", "blue", "black", "white"}
    if color not in allowed:
        raise HTTPException(status_code=400, detail="unsupported color test")
    command_send = _command_send_for_device(device_id)
    if command_send is None:
        raise HTTPException(status_code=409, detail="device not connected")
    try:
        command_send.send_nowait({"type": "show_color_test", "color": color})
    except Exception as exc:
        raise HTTPException(status_code=409, detail="device busy or unavailable") from exc
    return {"dispatched": True, "device_id": device_id, "color": color}


@app.get("/api/trace/preview")
async def trace_preview(character: str = "") -> dict[str, object]:
    """Generate a show_character_trace payload for a single Hanzi (web preview/debug)."""
    ch = (character or "").strip()
    if not ch:
        raise HTTPException(status_code=400, detail="character must not be empty")
    payload = character_trace_payload(ch)
    if payload is None:
        return {"available": False, "payload": None}
    return {"available": True, "payload": payload}


@app.post("/api/devices/{device_id}/trace")
async def device_trace(device_id: str, body: dict = Body(default={})) -> dict[str, object]:
    """Push a character trace command to a connected device (hardware or simulator)."""
    character = str(body.get("character", "")).strip()
    if not character:
        raise HTTPException(status_code=400, detail="character must not be empty")
    payload = character_trace_payload(character)
    if payload is None:
        raise HTTPException(status_code=404, detail="trace payload unavailable for character")

    command_send = _command_send_for_device(device_id)
    if command_send is None:
        raise HTTPException(status_code=409, detail="device not connected")
    try:
        command_send.send_nowait({"type": "show_character_trace", "payload": payload})
    except Exception as exc:  # full/closed buffer
        raise HTTPException(status_code=409, detail="device busy or unavailable") from exc
    return {"dispatched": True, "device_id": device_id, "character": payload.get("character")}


def _parse_explanation_json(raw: str, character: str) -> tuple[str, str]:
    """Extract {word, explanation} from the LLM output, tolerating ```json fences
    or a plain paragraph. Falls back to using the whole text as the explanation."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
        text = text.strip()
    word = ""
    explanation = ""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                word = str(data.get("word", "")).strip()
                explanation = str(data.get("explanation", "")).strip()
        except (json.JSONDecodeError, ValueError):
            pass
    if not explanation:
        explanation = text or ""
    return word, explanation


def _fallback_explanation(character: str, stroke_count: int) -> dict[str, object]:
    """Offline template used when the LLM is unavailable, so pre-generation still
    returns something usable (the user can edit it on the console before sending)."""
    strokes = f"一共有{stroke_count}笔，" if stroke_count else ""
    explanation = (
        f"我们来认识「{character}」字。{strokes}写的时候按照笔顺一笔一笔来，"
        f"先看清每一笔的位置，再慢慢描红。多写几遍，就能记住「{character}」啦。"
    )
    return {"character": character, "word": "", "explanation": explanation, "source": "fallback"}


_DEFAULT_EXPLANATION_TEMPLATE = (
    "先说这个字的常用组词，再说偏旁部首和拆解写法，最后给一段简单易于记忆的文案。"
)


async def _generate_character_explanation(
    character: str, custom_prompt: str | None = None
) -> dict[str, object]:
    """Use the runtime LLM to generate a child-friendly word + writing breakdown
    for one character. Falls back to a template if the LLM is unavailable."""
    payload = character_trace_payload(character)
    stroke_count = len(payload.get("strokes", [])) if payload else 0
    requirement = custom_prompt or _DEFAULT_EXPLANATION_TEMPLATE
    system_prompt = (
        "你是耐心的小学语文老师，给一年级小朋友讲解生字，帮助他们记住怎么写。"
        "请只输出一个 JSON 对象，格式为 "
        '{"word":"一个含该字的常用词语","explanation":"一段解读文案"}。'
        f"解读要求：口语化、适合朗读给小朋友听；{requirement}"
        "大约 60 到 140 个字；不要使用拼音字母、表情符号或列表符号。"
    )
    user_prompt = (
        f"生字：{character}。它一共有{stroke_count}笔。"
        if stroke_count
        else f"生字：{character}。"
    )
    llm = _build_runtime_llm_service()
    raw = await llm.generate_freeform(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=320,
        usage_user_id="console-vocab",
        request_kind="vocab_generation",
    )
    word, explanation = _parse_explanation_json(raw, character)
    if not explanation:
        return _fallback_explanation(character, stroke_count)
    return {
        "character": character,
        "word": word,
        "explanation": explanation,
        "source": "ark",
    }


# Vocab explanations are a console-wide library (not tied to a specific device
# user), so they live under a single fixed namespace in the explanation store.
VOCAB_EXPLANATION_USER_ID = "console-vocab"


def _vocab_explanation_store() -> VocabExplanationStore:
    return VocabExplanationStore(get_settings().memory_root)


async def _collect_tts_audio(
    text: str,
    *,
    record_usage: bool = True,
    usage_scenario: str = "admin_precompute",
) -> tuple[bytes, int]:
    """Synthesize `text` with the runtime TTS service (Volc when keys are set,
    else the local placeholder tone) and return the full PCM bytes + sample rate.
    Used by 「生字管理」 to pre-generate and cache explanation audio."""
    settings = get_settings()
    runtime_store = RuntimeConfigStore(settings.runtime_config_path)
    sample_rate_hz, _ = resolve_speech_tts_sample_rate_hz(runtime_store)
    service = _build_runtime_tts_service(settings)
    provider = "local" if isinstance(service, LocalTTSService) else "volc"
    started_at = time.perf_counter()
    chunks: list[bytes] = []
    success = False
    try:
        async for chunk in service.synthesize(text):
            if chunk:
                chunks.append(chunk)
        success = bool(chunks)
        return b"".join(chunks), int(sample_rate_hz or 16000)
    finally:
        if record_usage:
            try:
                AuditLogStore(settings.memory_root).append_event(
                    "console-tts",
                    event_type="tts_generation",
                    payload={
                        "metrics_version": 1,
                        "scenario": usage_scenario,
                        "provider": provider,
                        "characters": len(text),
                        "success": success,
                        "latency_ms": max(
                            0, int((time.perf_counter() - started_at) * 1000)
                        ),
                    },
                )
            except Exception:
                # Cost observability must not change pre-generation behavior.
                pass


async def _synthesize_buffered(
    tts_service: TTSService, text: str, *, attempts: int = 2
) -> tuple[list[bytes], int, bool]:
    """Buffer a full synthesis with jitter tolerance. Volc 的连接/接收在网络抖动时
    易超时（见 services/tts.py），原先「一抖即整轮丢音」。这里：首包前出错重试一次；
    已产出部分则保留部分（半句也好过整轮静默）；连重试都没产出才抛出，让调用方走
    tts_end error。"""
    cancelled_exc = anyio.get_cancelled_exc_class()
    last_exc: Exception | None = None
    for _ in range(max(1, attempts)):
        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in tts_service.synthesize(text):
                if chunk:
                    chunks.append(chunk)
                    total += len(chunk)
            if total > 0:
                return chunks, total, True
            last_exc = RuntimeError("tts produced no audio")
        except cancelled_exc:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced after retries
            last_exc = exc
            if chunks:
                # 已经播到一半才断：保留已合成的，不重试（重试会从头重复一遍）。
                return chunks, total, False
            continue
    raise last_exc if last_exc is not None else RuntimeError("tts produced no audio")


async def _collect_cached_reply_tts_audio(
    tts_service: TTSService,
    text: str,
    *,
    settings,
    runtime_store: RuntimeConfigStore,
) -> tuple[bytes, bool, bool]:
    """Return PCM, cache-hit, and whether synthesis completed normally."""
    sample_rate_hz, _ = resolve_speech_tts_sample_rate_hz(runtime_store)
    encoding, _ = resolve_speech_tts_encoding(runtime_store)
    if isinstance(tts_service, LocalTTSService):
        provider = "local"
        resource_id = "debug-tone"
        voice_type = "local"
        configuration_fingerprint = "local-debug-tone-v1"
    else:
        provider = "volc"
        resource_id, _ = resolve_speech_tts_resource_id(runtime_store)
        voice_type, _ = resolve_speech_tts_voice_type(runtime_store)
        ws_url, _ = resolve_speech_tts_ws_url(runtime_store)
        api_key, _ = resolve_speech_tts_api_key(runtime_store)
        # Never persist the secret itself. A short digest prevents audio from a
        # different account/endpoint or cache schema being reused accidentally.
        configuration_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "endpoint": ws_url,
                    "account": hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
                    if api_key
                    else "",
                    "implementation": "volc-v3-unidirectional-v1",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    cache = TTSResponseCacheStore(settings.memory_root)
    cached = cache.get(
        text,
        provider=provider,
        resource_id=resource_id,
        voice_type=voice_type,
        sample_rate_hz=sample_rate_hz,
        encoding=encoding,
        configuration_fingerprint=configuration_fingerprint,
    )
    if cached is not None:
        return cached, True, True

    chunks, _, complete = await _synthesize_buffered(tts_service, text)
    audio = b"".join(chunks)
    if not audio:
        raise RuntimeError("tts produced no audio")
    # A dropped provider stream may still contain playable leading audio. Play
    # it once as a degraded response, but never turn a truncated sentence into
    # a permanent cache hit.
    if complete:
        cache.put(
            text,
            provider=provider,
            resource_id=resource_id,
            voice_type=voice_type,
            sample_rate_hz=sample_rate_hz,
            encoding=encoding,
            audio=audio,
            configuration_fingerprint=configuration_fingerprint,
        )
    return audio, False, complete


def _prompt_voice_store() -> PromptVoiceCacheStore:
    return PromptVoiceCacheStore(get_settings().memory_root)


async def _collect_scene_prompt_audio(
    scene_id: str,
    kind: str,
    text: str,
    *,
    record_generation_usage: bool = True,
) -> tuple[bytes, int, bool]:
    """场景固定提示音：命中持久化缓存即零 TTS Token；缺失（首次/文案或音色/采样率
    变更）才现合成并落盘。缓存键含 文案hash + voice + sample_rate + encoding。"""
    settings = get_settings()
    runtime_store = RuntimeConfigStore(settings.runtime_config_path)
    sample_rate_hz, _ = resolve_speech_tts_sample_rate_hz(runtime_store)
    voice_type, _ = resolve_speech_tts_voice_type(runtime_store)
    encoding, _ = resolve_speech_tts_encoding(runtime_store)
    text_hash = prompt_text_hash(text)
    store = _prompt_voice_store()
    cached = store.get_cached(
        scene_id,
        kind,
        text_hash=text_hash,
        voice_type=voice_type,
        sample_rate_hz=int(sample_rate_hz or 16000),
        encoding=encoding,
    )
    if cached is not None:
        return cached[0], cached[1], True
    audio, sample_rate = await _collect_tts_audio(
        text,
        record_usage=record_generation_usage,
        usage_scenario="scene_prompt_precompute",
    )
    if audio:
        store.store(
            scene_id,
            kind,
            text_hash=text_hash,
            voice_type=voice_type,
            sample_rate_hz=int(sample_rate),
            encoding=encoding,
            audio=audio,
        )
    return audio, int(sample_rate), False


@app.post("/api/vocab/explanation")
async def vocab_explanation(body: dict = Body(default={})) -> dict[str, object]:
    """Pre-generate a child-friendly word + writing-breakdown explanation for a
    character (console 「生字管理」 calls this ahead of time; the result is shown,
    optionally edited, then dispatched to the device as text + TTS).

    Also synthesizes the explanation to TTS audio and persists both text + audio
    so the console can list prior records and offer playback across sessions."""
    character = str(body.get("character", "")).strip()
    if len(character) != 1 or not ("一" <= character <= "鿿"):
        raise HTTPException(status_code=400, detail="character must be a single hanzi")
    regenerate = bool(body.get("regenerate", False))
    custom_prompt = str(body.get("prompt_template", "")).strip() or None

    if not regenerate:
        cached = _vocab_explanation_store().get(VOCAB_EXPLANATION_USER_ID, character)
        if cached and cached.get("explanation"):
            has_tts = bool(cached.get("tts_path"))
            result: dict[str, object] = {
                "character": character,
                "word": cached.get("word", ""),
                "explanation": cached.get("explanation", ""),
                "source": cached.get("source", ""),
                "has_tts": has_tts,
                "cached": True,
            }
            if has_tts:
                result["tts_url"] = f"/api/vocab/{quote(character)}/tts"
                result["tts_sample_rate_hz"] = cached.get("tts_sample_rate_hz")
            return result

    try:
        result = await _generate_character_explanation(character, custom_prompt)
    except Exception as exc:  # LLM unavailable / network / key — degrade gracefully.
        payload = character_trace_payload(character)
        stroke_count = len(payload.get("strokes", [])) if payload else 0
        result = _fallback_explanation(character, stroke_count)
        result["detail"] = f"LLM 不可用，已用离线模板：{exc}"

    explanation_text = str(result.get("explanation", "")).strip()
    tts_audio = b""
    tts_sample_rate_hz = 0
    if explanation_text:
        try:
            tts_audio, tts_sample_rate_hz = await _collect_tts_audio(explanation_text)
        except Exception as exc:  # TTS unavailable — keep text, skip audio cache.
            result["tts_detail"] = f"TTS 合成失败，仅保存文字：{exc}"

    try:
        _vocab_explanation_store().upsert(
            VOCAB_EXPLANATION_USER_ID,
            character=character,
            word=str(result.get("word", "")),
            explanation=explanation_text,
            source=str(result.get("source", "")),
            tts_audio=tts_audio or None,
            tts_sample_rate_hz=tts_sample_rate_hz or None,
        )
    except Exception as exc:  # persistence failure must not break generation.
        result["store_detail"] = f"记录保存失败：{exc}"

    has_tts = bool(tts_audio)
    result["has_tts"] = has_tts
    if has_tts:
        result["tts_url"] = f"/api/vocab/{quote(character)}/tts"
        result["tts_sample_rate_hz"] = tts_sample_rate_hz
    return result


@app.get("/api/vocab/explanations")
async def list_vocab_explanations() -> dict[str, object]:
    """Return all previously generated 「生字管理」 explanations so the console can
    restore the grid (with playback availability) when it reopens. Ordered by
    created_at ascending so the console restores them in original entry order."""
    entries = _vocab_explanation_store().list(VOCAB_EXPLANATION_USER_ID)
    entries = sorted(entries, key=lambda e: str(e.get("created_at", "")))
    items = []
    for entry in entries:
        character = str(entry.get("character", ""))
        has_tts = bool(entry.get("tts_path"))
        item = {
            "character": character,
            "word": str(entry.get("word", "")),
            "explanation": str(entry.get("explanation", "")),
            "source": str(entry.get("source", "")),
            "created_at": str(entry.get("created_at", "")),
            "updated_at": str(entry.get("updated_at", "")),
            "has_tts": has_tts,
        }
        if has_tts:
            item["tts_url"] = f"/api/vocab/{quote(character)}/tts"
            item["tts_sample_rate_hz"] = entry.get("tts_sample_rate_hz")
        items.append(item)
    return {"explanations": items}


@app.get("/api/vocab/{character}/tts")
async def get_vocab_tts_audio(character: str) -> Response:
    """Return the cached TTS audio (raw PCM as audio/L16) for a generated
    explanation, for console playback (试听)."""
    character = (character or "").strip()
    if len(character) != 1 or not ("一" <= character <= "鿿"):
        raise HTTPException(status_code=400, detail="character must be a single hanzi")
    result = _vocab_explanation_store().read_tts_audio(VOCAB_EXPLANATION_USER_ID, character)
    if result is None:
        raise HTTPException(status_code=404, detail="vocab tts audio not found")
    audio_bytes, sample_rate_hz = result
    return Response(
        content=audio_bytes,
        media_type=f"audio/L16; rate={sample_rate_hz}; channels=1",
        headers={
            "X-TTS-Audio-Format": "pcm_s16le",
            "X-TTS-Sample-Rate-Hz": str(sample_rate_hz),
        },
    )


# Idiom content is a console-wide library (not tied to a specific device user),
# so it lives under a single fixed namespace in the explanation store.
IDIOM_CONTENT_USER_ID = "console-idiom"


def _idiom_explanation_store() -> IdiomExplanationStore:
    return IdiomExplanationStore(get_settings().memory_root)


def _is_idiom(value: str) -> bool:
    return 2 <= len(value) <= 8 and all("一" <= c <= "鿿" for c in value)


def _idiom_glyphs(idiom: str) -> list[dict]:
    """Per-character楷体 outline payloads for big-character display; characters
    without a glyph still appear (carry just the character) so the device can
    fall back to its font for that cell."""
    glyphs: list[dict] = []
    for ch in idiom:
        payload = character_trace_payload(ch)
        glyphs.append(payload if payload is not None else {"character": ch})
    return glyphs


def _parse_idiom_json(raw: str) -> tuple[str, str, str]:
    """Extract {pinyin, explanation, story} from the LLM output, tolerating
    ```json fences or a plain paragraph. Falls back to using the whole text as
    the explanation when no JSON object is present."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
        text = text.strip()
    pinyin = ""
    explanation = ""
    story = ""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                pinyin = str(data.get("pinyin", "")).strip()
                explanation = str(data.get("explanation", "")).strip()
                story = str(data.get("story", "")).strip()
        except (json.JSONDecodeError, ValueError):
            pass
    if not explanation:
        explanation = text or ""
    return pinyin, explanation, story


def _fallback_idiom_content(idiom: str) -> dict[str, object]:
    """Offline template used when the LLM is unavailable, so pre-generation still
    returns something usable (the user can edit it on the console before sending)."""
    explanation = (
        f"我们来认识成语「{idiom}」。它是一个常用的四字成语，"
        f"小朋友可以先把每个字读一读，再连起来读一遍，记住它的样子。"
    )
    story = (
        f"从前有一个小故事，和「{idiom}」有关。等老师把网络连好，"
        f"就能给你讲一个更精彩的成语小故事啦。"
    )
    return {"idiom": idiom, "pinyin": "", "explanation": explanation, "story": story, "source": "fallback"}


_DEFAULT_IDIOM_EXPLANATION_TEMPLATE = (
    "先说成语的意思，再说它的出处或用法，最后给一句简单好记的小结。"
)
_DEFAULT_IDIOM_STORY_TEMPLATE = (
    "用一个完整、有趣、适合朗读给小朋友听的小故事，讲清楚这个成语的由来。"
)


async def _generate_idiom_content(
    idiom: str,
    explanation_prompt: str | None = None,
    story_prompt: str | None = None,
) -> dict[str, object]:
    """Use the runtime LLM to generate a child-friendly interpretation + short
    story for an idiom in a single call. Falls back to a template if unavailable."""
    explanation_req = explanation_prompt or _DEFAULT_IDIOM_EXPLANATION_TEMPLATE
    story_req = story_prompt or _DEFAULT_IDIOM_STORY_TEMPLATE
    system_prompt = (
        "你是耐心的小学语文老师，给小朋友讲解成语。"
        "请只输出一个 JSON 对象，格式为 "
        '{"pinyin":"成语的拼音","explanation":"成语解读","story":"成语小故事"}。'
        f"解读要求：口语化、适合朗读；{explanation_req}大约 60 到 140 个字。"
        f"小故事要求：{story_req}大约 120 到 220 个字。"
        "拼音用带声调的全拼、字与字之间用空格分隔；"
        "explanation 与 story 都不要使用拼音字母、表情符号或列表符号。"
    )
    user_prompt = f"成语：{idiom}。"
    llm = _build_runtime_llm_service()
    raw = await llm.generate_freeform(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=640,
        # 成语解读+故事约 360 字，生成耗时长；prod 上默认 20s 读超时不够（会退兜底
        # 模板），给更长超时。首次生成后即缓存，后续命中零等待。
        timeout_seconds=60.0,
        usage_user_id="console-idiom",
        request_kind="idiom_generation",
    )
    pinyin, explanation, story = _parse_idiom_json(raw)
    if not explanation:
        return _fallback_idiom_content(idiom)
    return {
        "idiom": idiom,
        "pinyin": pinyin,
        "explanation": explanation,
        "story": story,
        "source": "ark",
    }


def _idiom_tts_urls(idiom: str, *, has_explanation: bool, has_story: bool) -> dict[str, object]:
    urls: dict[str, object] = {
        "has_explanation_tts": has_explanation,
        "has_story_tts": has_story,
    }
    if has_explanation:
        urls["explanation_tts_url"] = f"/api/idiom/{quote(idiom)}/tts?kind=explanation"
    if has_story:
        urls["story_tts_url"] = f"/api/idiom/{quote(idiom)}/tts?kind=story"
    return urls


# —— 成语学习「场景」取材与计划（动作型场景 idiom_learning 用）——————————————

def _library_idioms(store: RuntimeConfigStore) -> list[str]:
    """控制台「成语学习」所有分组里录入的成语（去重保序）。"""
    try:
        data = store.load_idiom_groups_settings()
    except Exception:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for group in (data.get("groups") or {}).values():
        for idiom in (group.get("idioms") or []):
            idiom = str(idiom or "").strip()
            if idiom and idiom not in seen:
                seen.add(idiom)
                result.append(idiom)
    return result


def _idiom_library_match(text: str, library: list[str]) -> str | None:
    """text 里是否直接含某个库成语（最稳的"确实是成语"判定）；返回最长命中。"""
    hits = [idiom for idiom in library if idiom and idiom in text]
    return max(hits, key=len) if hits else None


async def _idiom_content_for(idiom: str, *, allow_generate: bool) -> dict | None:
    """取该成语的解读+故事：优先缓存；缓存没有且 allow_generate 时现场生成并写
    入缓存（含 TTS）。返回含 explanation/story/pinyin 的内容 dict，或 None。"""
    store = _idiom_explanation_store()
    cached = store.get(IDIOM_CONTENT_USER_ID, idiom)
    if cached and cached.get("explanation"):
        return {
            "idiom": idiom,
            "pinyin": str(cached.get("pinyin", "")),
            "explanation": str(cached.get("explanation", "")),
            "story": str(cached.get("story", "")),
        }
    if not allow_generate:
        return None
    try:
        result = await _generate_idiom_content(idiom)
    except Exception:
        result = _fallback_idiom_content(idiom)
    explanation_text = str(result.get("explanation", "")).strip()
    story_text = str(result.get("story", "")).strip()
    if not explanation_text:
        return None
    expl_audio, expl_sr, story_audio, story_sr = b"", 0, b"", 0
    try:
        expl_audio, expl_sr = await _collect_tts_audio(explanation_text)
        if story_text:
            story_audio, story_sr = await _collect_tts_audio(story_text)
    except Exception:
        pass
    try:
        store.upsert(
            IDIOM_CONTENT_USER_ID,
            idiom=idiom,
            pinyin=str(result.get("pinyin", "")),
            explanation=explanation_text,
            story=story_text,
            source=str(result.get("source", "")),
            explanation_tts=expl_audio or None,
            explanation_sample_rate_hz=expl_sr or None,
            story_tts=story_audio or None,
            story_sample_rate_hz=story_sr or None,
        )
    except Exception:
        pass
    return {
        "idiom": idiom,
        "pinyin": str(result.get("pinyin", "")),
        "explanation": explanation_text,
        "story": story_text,
    }


def _collect_idiom_cached_audio(idiom: str, target_sample_rate_hz: int) -> bytes:
    """复用成语库里已合成的 explanation_tts + story_tts PCM（与 narration 同序拼接）。
    采样率与下发声明一致才用，缺失或不匹配返回 b"" 让调用方回退现合成。"""
    store = _idiom_explanation_store()
    entry = store.get(IDIOM_CONTENT_USER_ID, idiom)
    if not entry:
        return b""
    expl = store.read_tts_audio(IDIOM_CONTENT_USER_ID, idiom, "explanation")
    if expl is None or not expl[0] or expl[1] != target_sample_rate_hz:
        return b""
    parts = [expl[0]]
    # narration = explanation + " " + story；有故事文本就必须有对应音频，否则口播会缺半段。
    if str(entry.get("story", "")).strip():
        story = store.read_tts_audio(IDIOM_CONTENT_USER_ID, idiom, "story")
        if story is None or not story[0] or story[1] != target_sample_rate_hz:
            return b""
        parts.append(story[0])
    return b"".join(parts)


def _add_idiom_to_library(store: RuntimeConfigStore, idiom: str) -> None:
    """新成语追加进控制台默认分组（不覆盖，已存在则跳过）。"""
    default_name = RuntimeConfigStore._IDIOM_DEFAULT_GROUP_NAME
    try:
        data = store.load_idiom_groups_settings()
        groups = dict(data.get("groups") or {})
        group = dict(groups.get(default_name) or {"idioms": [], "statuses": {}, "sections": []})
        idioms = list(group.get("idioms") or [])
        if idiom in idioms:
            return
        idioms.append(idiom)
        group["idioms"] = idioms
        groups[default_name] = group
        store.save_idiom_groups_settings(
            active_group=data.get("active_group") or default_name,
            active_status_filter=data.get("active_status_filter") or "all",
            groups=groups,
            explanation_prompt_template=data.get("explanation_prompt_template") or "",
            story_prompt_template=data.get("story_prompt_template") or "",
        )
    except Exception:
        pass


def _idiom_plan_from_content(content: dict) -> IdiomDisplayPlan:
    idiom = str(content.get("idiom", ""))
    explanation = str(content.get("explanation", "")).strip()
    story = str(content.get("story", "")).strip()
    narration = f"{explanation} {story}".strip() if story else explanation
    return IdiomDisplayPlan(
        idiom=idiom,
        pinyin=str(content.get("pinyin", "")),
        glyphs=_idiom_glyphs(idiom),
        narration=narration,
    )


# 口语里贴在成语前的填充/称呼词。ASR 把它们一并识别进来时，extract_idiom_query 会
# 贪婪地把「请问画蛇添足」当成候选（6字），库外四字门槛随即把它挡掉 → 漏切页。
_IDIOM_LEADING_FILLER = (
    "我想问问",
    "我想问",
    "想问问",
    "请问问",
    "麻烦问",
    "帮我查",
    "帮我",
    "想问",
    "问问",
    "请问",
    "那个",
    "这个",
    "老师",
    "麻烦",
    "我想",
    "知道",
    "请",
    "那",
    "嗯",
)


def _strip_idiom_leading_filler(candidate: str) -> str:
    """剥掉候选开头的口语填充词，剥到 4 字即停（绝大多数成语四字）。
    例：「请问画蛇添足」→「画蛇添足」；纯五字成语「五十步笑百步」无前缀，保持不变。"""
    changed = True
    while changed and len(candidate) > 4:
        changed = False
        for filler in _IDIOM_LEADING_FILLER:
            if candidate.startswith(filler) and len(candidate) - len(filler) >= 4:
                candidate = candidate[len(filler):]
                changed = True
                break
    return candidate


async def _plan_idiom_query(user_text: str, store: RuntimeConfigStore) -> IdiomDisplayPlan | None:
    """路径 A：问具体成语。需先有查询意图（extract_idiom_query 命中）；优先库内
    匹配用缓存；库外仅四字候选才生成 + 写入成语库。"""
    candidate = extract_idiom_query(user_text)
    if not candidate:
        return None
    library = _library_idioms(store)
    matched = _idiom_library_match(user_text, library)
    if matched:
        candidate = matched
    else:
        # 库外候选先剥前缀填充词，让「请问/这个+四字成语」也能命中四字门槛。
        candidate = _strip_idiom_leading_filler(candidate)
    in_library = candidate in library
    if not in_library and len(candidate) != 4:
        # 库外新增门槛：仅四字候选，避免把非成语写进生产成语库。
        return None
    content = await _idiom_content_for(candidate, allow_generate=True)
    if content is None:
        return None
    if not in_library:
        _add_idiom_to_library(store, candidate)
    return _idiom_plan_from_content(content)


async def _plan_idiom_random(store: RuntimeConfigStore) -> IdiomDisplayPlan | None:
    """路径 B：泛意图 → 从库随机一条（优先已缓存）；库空则 None。"""
    library = _library_idioms(store)
    if not library:
        return None
    cached_ids = {
        str(e.get("idiom", ""))
        for e in _idiom_explanation_store().list(IDIOM_CONTENT_USER_ID)
        if e.get("explanation")
    }
    pool = [i for i in library if i in cached_ids] or library
    content = await _idiom_content_for(random.choice(pool), allow_generate=True)
    return _idiom_plan_from_content(content) if content else None


@app.post("/api/idiom/content")
async def idiom_content(body: dict = Body(default={})) -> dict[str, object]:
    """Pre-generate (or read cached) a child-friendly interpretation + short story
    for an idiom, synthesize TTS for each, and persist both text + audio so the
    console can list prior records and offer playback across sessions."""
    idiom = str(body.get("idiom", "")).strip()
    if not _is_idiom(idiom):
        raise HTTPException(status_code=400, detail="idiom must be 2-8 hanzi")
    regenerate = bool(body.get("regenerate", False))
    explanation_prompt = str(body.get("explanation_prompt", "")).strip() or None
    story_prompt = str(body.get("story_prompt", "")).strip() or None

    if not regenerate:
        cached = _idiom_explanation_store().get(IDIOM_CONTENT_USER_ID, idiom)
        if cached and cached.get("explanation"):
            has_expl = bool(cached.get("explanation_tts_path"))
            has_story = bool(cached.get("story_tts_path"))
            result: dict[str, object] = {
                "idiom": idiom,
                "pinyin": cached.get("pinyin", ""),
                "explanation": cached.get("explanation", ""),
                "story": cached.get("story", ""),
                "source": cached.get("source", ""),
                "cached": True,
                **_idiom_tts_urls(idiom, has_explanation=has_expl, has_story=has_story),
            }
            return result

    try:
        result = await _generate_idiom_content(idiom, explanation_prompt, story_prompt)
    except Exception as exc:  # LLM unavailable / network / key — degrade gracefully.
        result = _fallback_idiom_content(idiom)
        result["detail"] = f"LLM 不可用，已用离线模板：{exc}"

    explanation_text = str(result.get("explanation", "")).strip()
    story_text = str(result.get("story", "")).strip()
    expl_audio = b""
    expl_sr = 0
    story_audio = b""
    story_sr = 0
    if explanation_text:
        try:
            expl_audio, expl_sr = await _collect_tts_audio(explanation_text)
        except Exception as exc:  # TTS unavailable — keep text, skip audio cache.
            result["tts_detail"] = f"TTS 合成失败，仅保存文字：{exc}"
    if story_text:
        try:
            story_audio, story_sr = await _collect_tts_audio(story_text)
        except Exception as exc:
            result["tts_detail"] = f"TTS 合成失败，仅保存文字：{exc}"

    try:
        _idiom_explanation_store().upsert(
            IDIOM_CONTENT_USER_ID,
            idiom=idiom,
            pinyin=str(result.get("pinyin", "")),
            explanation=explanation_text,
            story=story_text,
            source=str(result.get("source", "")),
            explanation_tts=expl_audio or None,
            explanation_sample_rate_hz=expl_sr or None,
            story_tts=story_audio or None,
            story_sample_rate_hz=story_sr or None,
        )
    except Exception as exc:  # persistence failure must not break generation.
        result["store_detail"] = f"记录保存失败：{exc}"

    result.update(
        _idiom_tts_urls(idiom, has_explanation=bool(expl_audio), has_story=bool(story_audio))
    )
    return result


@app.get("/api/idiom/contents")
async def list_idiom_contents() -> dict[str, object]:
    """Return all previously generated 「成语学习」 content so the console can
    restore the grid (with playback availability) when it reopens. Ordered by
    created_at ascending so the console restores them in original entry order."""
    entries = _idiom_explanation_store().list(IDIOM_CONTENT_USER_ID)
    entries = sorted(entries, key=lambda e: str(e.get("created_at", "")))
    items = []
    for entry in entries:
        idiom = str(entry.get("idiom", ""))
        has_expl = bool(entry.get("explanation_tts_path"))
        has_story = bool(entry.get("story_tts_path"))
        items.append(
            {
                "idiom": idiom,
                "pinyin": str(entry.get("pinyin", "")),
                "explanation": str(entry.get("explanation", "")),
                "story": str(entry.get("story", "")),
                "source": str(entry.get("source", "")),
                "created_at": str(entry.get("created_at", "")),
                "updated_at": str(entry.get("updated_at", "")),
                **_idiom_tts_urls(idiom, has_explanation=has_expl, has_story=has_story),
            }
        )
    return {"idioms": items}


@app.get("/api/idiom/{idiom}/tts")
async def get_idiom_tts_audio(idiom: str, kind: str = "explanation") -> Response:
    """Return the cached TTS audio (raw PCM as audio/L16) for an idiom's
    interpretation or story, for console playback (试听)."""
    idiom = (idiom or "").strip()
    if not _is_idiom(idiom):
        raise HTTPException(status_code=400, detail="idiom must be 2-8 hanzi")
    if kind not in ("explanation", "story"):
        raise HTTPException(status_code=400, detail="kind must be explanation or story")
    result = _idiom_explanation_store().read_tts_audio(IDIOM_CONTENT_USER_ID, idiom, kind)
    if result is None:
        raise HTTPException(status_code=404, detail="idiom tts audio not found")
    audio_bytes, sample_rate_hz = result
    return Response(
        content=audio_bytes,
        media_type=f"audio/L16; rate={sample_rate_hz}; channels=1",
        headers={
            "X-TTS-Audio-Format": "pcm_s16le",
            "X-TTS-Sample-Rate-Hz": str(sample_rate_hz),
        },
    )


def _scene_prompt_text(scene_id: str, kind: str) -> str:
    scene = effective_scene(RuntimeConfigStore(get_settings().runtime_config_path), scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    if kind == "enter":
        return scene.enter_prompt_text
    if kind == "exit":
        return scene.exit_prompt_text
    raise HTTPException(status_code=400, detail="kind must be enter or exit")


@app.get("/api/scenes/{scene_id}/prompt-tts")
async def get_scene_prompt_tts(scene_id: str, kind: str = "enter") -> Response:
    """场景进入/退出固定提示音的试听（命中缓存或现合成并缓存）。"""
    text = _scene_prompt_text(scene_id, kind).strip()
    if not text:
        raise HTTPException(status_code=404, detail="scene prompt text is empty")
    audio_bytes, sample_rate_hz, _ = await _collect_scene_prompt_audio(
        scene_id, kind, text
    )
    if not audio_bytes:
        raise HTTPException(status_code=502, detail="scene prompt tts synthesis failed")
    return Response(
        content=audio_bytes,
        media_type=f"audio/L16; rate={sample_rate_hz}; channels=1",
        headers={
            "X-TTS-Audio-Format": "pcm_s16le",
            "X-TTS-Sample-Rate-Hz": str(sample_rate_hz),
        },
    )


@app.post("/api/scenes/{scene_id}/prompt-tts/rebuild")
async def rebuild_scene_prompt_tts(scene_id: str) -> dict[str, object]:
    """保存提示音文案后主动预热缓存：合成 enter/exit 两段并落盘（避免首个用户
    进出场景时才现合成）。文案为空的段跳过。"""
    results: dict[str, object] = {"scene_id": scene_id}
    for kind in ("enter", "exit"):
        text = _scene_prompt_text(scene_id, kind).strip()
        if not text:
            results[kind] = {"status": "empty"}
            continue
        try:
            audio_bytes, sample_rate_hz, _ = await _collect_scene_prompt_audio(
                scene_id, kind, text
            )
            results[kind] = {
                "status": "ok" if audio_bytes else "empty",
                "bytes": len(audio_bytes),
                "sample_rate_hz": sample_rate_hz,
            }
        except Exception as exc:  # noqa: BLE001 - surface synth failure per kind
            results[kind] = {"status": "error", "detail": str(exc)}
    return results


@app.post("/api/devices/{device_id}/idiom-say")
async def device_idiom_say(device_id: str, body: dict = Body(default={})) -> dict[str, object]:
    """Dispatch an idiom together with a pre-generated narration that the device
    speaks (verbatim TTS, no LLM) while showing the idiom as big characters
    (2×2, static fill — no stroke reveal). Used by the console 「成语学习」 send."""
    idiom = str(body.get("idiom", "")).strip()
    text = str(body.get("text", "")).strip()
    if not _is_idiom(idiom):
        raise HTTPException(status_code=400, detail="idiom must be 2-8 hanzi")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="text must be at most 4000 characters")
    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty")
    pinyin = str(body.get("pinyin", "")).strip()
    command_send = _command_send_for_device(device_id)
    if command_send is None:
        raise HTTPException(status_code=409, detail="device not connected")
    command = {
        "type": "idiom_say",
        "idiom": idiom,
        "pinyin": pinyin,
        "text": text,
        "glyphs": _idiom_glyphs(idiom),
    }
    user_id = body.get("user_id")
    if isinstance(user_id, str) and user_id:
        command["user_id"] = user_id
    try:
        command_send.send_nowait(command)
    except Exception as exc:  # full/closed buffer
        raise HTTPException(status_code=409, detail="device busy or unavailable") from exc
    return {"dispatched": True, "device_id": device_id, "idiom": idiom}


@app.post("/api/devices/{device_id}/trace-say")
async def device_trace_say(device_id: str, body: dict = Body(default={})) -> dict[str, object]:
    """Dispatch a character trace together with a pre-generated explanation that
    the device speaks (verbatim TTS, no LLM) while showing the text. Used by the
    console 「生字管理」 batch/per-character send."""
    character = str(body.get("character", "")).strip()
    text = str(body.get("text", "")).strip()
    if not character:
        raise HTTPException(status_code=400, detail="character must not be empty")
    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty")
    payload = character_trace_payload(character)
    if payload is None:
        raise HTTPException(status_code=404, detail="trace payload unavailable for character")
    command_send = _command_send_for_device(device_id)
    if command_send is None:
        raise HTTPException(status_code=409, detail="device not connected")
    command = {"type": "trace_say", "character": character, "text": text}
    user_id = body.get("user_id")
    if isinstance(user_id, str) and user_id:
        command["user_id"] = user_id
    try:
        command_send.send_nowait(command)
    except Exception as exc:  # full/closed buffer
        raise HTTPException(status_code=409, detail="device busy or unavailable") from exc
    return {"dispatched": True, "device_id": device_id, "character": character}


@app.get("/api/devices/{device_id}/events")
async def device_events(device_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events stream of a device's live session (text-class events)."""
    send_stream, recv_stream = anyio.create_memory_object_stream(128)
    _subscribe_device(device_id, send_stream)

    async def event_generator():
        try:
            # Initial comment so the client knows the stream is open.
            yield ": subscribed\n\n"
            async with recv_stream:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        with anyio.fail_after(15):
                            event = await recv_stream.receive()
                    except TimeoutError:
                        # Heartbeat to keep the connection alive.
                        yield ": ping\n\n"
                        continue
                    except anyio.EndOfStream:
                        break
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            _unsubscribe_device(device_id, send_stream)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/console")
async def console_page() -> FileResponse:
    # no-cache so the HTML always revalidates and the browser always sees the
    # latest asset version strings (?v=...) — avoids serving a stale console.
    return FileResponse(
        WEB_CONSOLE_DIR / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/speech/uploads/{filename}")
async def get_speech_upload(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith(".wav"):
        raise HTTPException(status_code=404, detail="speech upload not found")
    path = get_settings().memory_root / "speech_uploads" / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="speech upload not found")
    return FileResponse(path, media_type="audio/wav")


async def _send_control(websocket: WebSocket, message: ServerControlMessage) -> None:
    await websocket.send_json(message.model_dump(exclude_none=True))


async def _send_tts_audio_bytes(
    websocket,
    audio: bytes,
    *,
    device_id: str,
    sample_rate_hz: int,
    frame_bytes: int = 4096,
    sleep_fn=anyio.sleep,
    clock=anyio.current_time,
) -> None:
    if not audio:
        return
    if _device_kind(device_id) == "console":
        await websocket.send_bytes(audio)
        return

    # The full reply is synthesized before this is called, so there are no
    # upstream gaps; pace to real time so the device's bounded queue neither
    # overflows (faster than real time → dropped tail) nor sits empty.
    #
    # Pace against a wall-clock deadline rather than sleeping a fixed slice per
    # frame: the per-frame send + scheduling overhead used to accumulate (307
    # tiny 1280B frames × overhead stretched a 12s reply to ~27s of trickled
    # playback). Deadlines self-correct — when we're already behind, the sleep
    # is skipped so total send time tracks the audio's real duration.
    bytes_per_second = max(1, int(sample_rate_hz or 16000) * 2)
    frame_size = max(1, int(frame_bytes))
    start = clock()
    sent = 0
    for offset in range(0, len(audio), frame_size):
        chunk = audio[offset : offset + frame_size]
        await websocket.send_bytes(chunk)
        sent += len(chunk)
        delay = (start + sent / bytes_per_second) - clock()
        if delay > 0:
            await sleep_fn(delay)


class _BufferingASRSession:
    """Buffer fed audio and transcribe it as one shot on finish.

    Used for the opus capture path and for ASR services (e.g. test mocks) that
    only implement ``transcribe`` rather than the streaming ``open_stream`` API.
    """

    def __init__(self, asr: ASRService) -> None:
        self._asr = asr
        self._buffer = bytearray()

    async def feed(self, pcm: bytes) -> None:
        if pcm:
            self._buffer += pcm

    async def finish(self) -> str:
        return await self._asr.transcribe(bytes(self._buffer))

    async def aclose(self) -> None:
        self._buffer = bytearray()


def _scene_asr_hotwords(runtime_store: RuntimeConfigStore, scene_id: str | None) -> tuple[str, ...]:
    """Return intent hotwords for every enabled scene plus active-scene vocabulary.

    Entry phrases must be available before a scene is active; otherwise the ASR
    has no bias when the user says the very command that should enter it.
    """
    try:
        scenes = tuple(scene for scene in effective_scenes(runtime_store) if scene.enabled)
    except Exception:
        return ()
    words: list[str] = []
    for scene in scenes:
        words.extend(scene.enter_keywords)
    active = next((scene for scene in scenes if scene.scene_id == scene_id), None)
    if active is not None:
        words.extend(active.exit_keywords)
        words.extend(active.asr_hotwords)
    # The upstream bidirectional API documents an approximately 100-token
    # context limit. Keep a deterministic cap after de-duplication.
    return tuple(dict.fromkeys(word.strip() for word in words if word.strip()))[:100]


def _build_runtime_asr_service(settings, *, scene_id: str | None = None) -> ASRService:
    runtime_store = RuntimeConfigStore(settings.runtime_config_path)
    api_key, _ = resolve_speech_asr_api_key(runtime_store)
    resource_id, _ = resolve_speech_asr_resource_id(runtime_store)
    ws_url, _ = resolve_speech_asr_ws_url(runtime_store)
    sample_rate_hz, _ = resolve_speech_asr_sample_rate_hz(runtime_store)
    if not api_key:
        # No new-console X-Api-Key -> local echo service (offline/dev mode).
        return LocalASRService()
    if not resource_id:
        raise RuntimeError(
            "volc sauc asr resource_id is not configured "
            "(default volc.bigasr.sauc.duration in the console speech settings)"
        )
    return VolcASRService(
        config=VolcASRConfig(
            api_key=api_key,
            resource_id=resource_id,
            ws_url=ws_url,
            sample_rate_hz=sample_rate_hz,
            hotwords=_scene_asr_hotwords(runtime_store, scene_id),
        ),
    )


def _build_runtime_tts_service(settings) -> TTSService:
    runtime_store = RuntimeConfigStore(settings.runtime_config_path)
    api_key, _ = resolve_speech_tts_api_key(runtime_store)
    voice_type, _ = resolve_speech_tts_voice_type(runtime_store)
    sample_rate_hz, _ = resolve_speech_tts_sample_rate_hz(runtime_store)
    if not api_key or not voice_type:
        try:
            return LocalTTSService(sample_rate_hz=sample_rate_hz)
        except TypeError:
            return LocalTTSService()
    resource_id, _ = resolve_speech_tts_resource_id(runtime_store)
    ws_url, _ = resolve_speech_tts_ws_url(runtime_store)
    encoding, _ = resolve_speech_tts_encoding(runtime_store)
    return VolcTTSService(
        config=VolcTTSConfig(
            api_key=api_key,
            resource_id=resource_id,
            voice_type=voice_type,
            ws_url=ws_url,
            sample_rate_hz=sample_rate_hz,
            encoding=encoding,
        )
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    settings = get_settings()
    audit_store = AuditLogStore(settings.memory_root)
    conversation_store = ConversationStore(settings.memory_root)
    vocabulary_store = VocabularyStore(settings.memory_root)

    authenticated = False
    device_id = CONSOLE_DEVICE_ID
    user_id = settings.default_user_id
    audio_chunks: list[bytes] = []
    collecting_audio = False
    audio_segment_started_at: float | None = None
    audio_segment_bytes = 0
    active_turn_cancel_scope: anyio.CancelScope | None = None
    active_audio_codec: str = "pcm_s16le"
    active_audio_sample_rate_hz: int = 16000
    active_asr_provider: str = "local"
    opus_decoder: OpusDecoder16k | None = None
    raw_opus_decoder: RawOpusDecoder16k | None = None
    asr_session: ASRStreamSession | None = None
    asr_session_failed = False
    # Current intent scene for this connection (None = 通用对话). In-memory only:
    # reconnect/restart returns to general chat (ADR 0008).
    current_scene: str | None = None
    # Command stream lets HTTP /say push a turn into this connection's own task.
    command_send, command_recv = anyio.create_memory_object_stream(32)

    # Session events broadcast to SSE monitors of this device (text-class only).
    _MONITOR_EVENT_TYPES = {
        "asr_result",
        "llm_prompt",
        "llm_reply_start",
        "llm_reply_delta",
        "llm_reply_end",
        "tts_start",
        "tts_end",
        "text_input",
        "show_character_trace",
        "show_idiom",
        "scene_changed",
    }

    def _audit(user: str, *, event_type: str, payload: dict, turn_id: str = "") -> None:
        enriched = {**payload, "device_id": device_id}
        if event_type in _MONITOR_EVENT_TYPES:
            _publish_event(
                device_id,
                {
                    "event_type": event_type,
                    "device_id": device_id,
                    "user_id": user,
                    "turn_id": turn_id,
                    "payload": payload,
                    "ts": time.time(),
                },
            )
        # Deltas remain live on the SSE monitor, but persisting every artificial
        # 8-character chunk turns one reply into many synchronous JSONL writes.
        # llm_reply_end already stores the complete text and is the durable event.
        if event_type == "llm_reply_delta":
            return
        try:
            audit_store.append_event(
                user,
                event_type=event_type,
                payload=enriched,
                turn_id=turn_id,
            )
        except Exception:
            # Audit logging must not break the real-time websocket flow.
            return

    barge_in_started_at_ms: int | None = None

    async def _cancel_active_turn(reason: str) -> None:
        nonlocal active_turn_cancel_scope, collecting_audio, audio_chunks, audio_segment_started_at, audio_segment_bytes
        if active_turn_cancel_scope is None:
            return
        try:
            active_turn_cancel_scope.cancel()
        finally:
            active_turn_cancel_scope = None
            collecting_audio = False
            audio_chunks = []
            audio_segment_started_at = None
            audio_segment_bytes = 0
            latency_ms = None
            if barge_in_started_at_ms is not None:
                latency_ms = int(anyio.current_time() * 1000) - barge_in_started_at_ms
            await _send_control(
                websocket,
                ServerControlMessage(type="tts_end", reason=reason, latency_ms=latency_ms),
            )

    async def _fail_audio_segment(
        exc: Exception,
        *,
        message: str = "语音识别暂时不可用，请再说一次",
        reason: str = "asr_failed",
    ) -> None:
        """Abort the in-flight audio segment after an upstream ASR failure.

        Surfaces the failure as an ``error`` control message and keeps the
        websocket open. Without this, an ASR exception raised in ``recv_loop``
        (e.g. the streaming recognizer failing to connect/authenticate) would
        unwind the task group and drop the whole connection — the browser then
        only sees a bare close 1000 with no reason.
        """
        nonlocal asr_session, asr_session_failed
        asr_session_failed = True
        if asr_session is not None:
            try:
                await asr_session.aclose()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
            asr_session = None
        await _send_control(
            websocket,
            ServerControlMessage(
                type="error",
                message=message,
                detail=str(exc),
                reason=reason,
            ),
        )
        _audit(
            user_id,
            event_type="speech_turn_error",
            payload={"error": str(exc)},
        )

    def _plan_character_trace(user_text: str, turn_id: str) -> CharacterTracePlan | None:
        trace_character = extract_trace_character(user_text)
        if not trace_character:
            return None
        trace_payload = character_trace_payload(trace_character)
        try:
            vocabulary_store.record_question(
                user_id,
                character=trace_character,
                question=user_text,
                turn_id=turn_id,
                trace_available=trace_payload is not None,
            )
        except Exception as exc:  # noqa: BLE001
            _audit(
                user_id,
                event_type="vocabulary_store_error",
                payload={"error": str(exc), "character": trace_character},
                turn_id=turn_id,
            )
        if trace_payload is None:
            return None
        return CharacterTracePlan(
            payload=trace_payload,
            narration=build_trace_narration(trace_character, trace_payload),
        )

    async def _send_character_trace(plan: CharacterTracePlan, turn_id: str) -> None:
        trace_payload = dict(plan.payload)
        await _send_control(
            websocket,
            ServerControlMessage(
                type="show_character_trace",
                payload=trace_payload,
                character=trace_payload.get("character"),
                duration_ms=trace_payload.get("duration_ms"),
                strokes=trace_payload.get("strokes"),
                outlines=trace_payload.get("outlines"),
                turn_id=turn_id,
            ),
        )
        _audit(
            user_id,
            event_type="show_character_trace",
            payload=plan.payload,
            turn_id=turn_id,
        )

    async def _send_idiom_display(plan: IdiomDisplayPlan, turn_id: str) -> None:
        await _send_control(
            websocket,
            ServerControlMessage(
                type="show_idiom",
                idiom=plan.idiom,
                pinyin=plan.pinyin or None,
                glyphs=plan.glyphs,
                turn_id=turn_id,
            ),
        )
        _audit(
            user_id,
            event_type="show_idiom",
            payload={"idiom": plan.idiom, "pinyin": plan.pinyin},
            turn_id=turn_id,
        )

    async def _run_turn(
        *,
        user_text: str,
        turn_id: str,
        tts_service: TTSService,
        agent: VoiceAgent,
        trace_plan: CharacterTracePlan | None = None,
        idiom_plan: IdiomDisplayPlan | None = None,
        scene_id: str | None = None,
        scene_prompt_plan: ScenePromptPlan | None = None,
    ) -> None:
        cancelled_exc = anyio.get_cancelled_exc_class()
        turn_started_at = time.perf_counter()
        runtime_store = RuntimeConfigStore(settings.runtime_config_path)
        tts_sample_rate_hz, _ = resolve_speech_tts_sample_rate_hz(
            runtime_store
        )
        # trace-say / idiom-say / scene-prompt all speak a pre-generated narration
        # verbatim (no LLM); only the synchronized side-effect differs (display vs
        # scene_changed) and the audio source (synthesize vs prompt-voice cache).
        if trace_plan is not None:
            verbatim_text = trace_plan.narration
            verbatim_route = "local-rule:character-trace-narration"
            verbatim_source = "local-rule"
        elif idiom_plan is not None:
            verbatim_text = idiom_plan.narration
            verbatim_route = "local-rule:idiom-narration"
            verbatim_source = "local-rule"
        elif scene_prompt_plan is not None:
            verbatim_text = scene_prompt_plan.narration
            verbatim_route = f"scene:{scene_prompt_plan.action}"
            verbatim_source = "scene-prompt"
        else:
            verbatim_text = None
            verbatim_route = ""
            verbatim_source = ""

        llm_started_at = time.perf_counter()
        if verbatim_text is None:
            token = set_current_scenario(scene_id or "ws")
            try:
                agent_result = await agent.ainvoke(user_text, user_id=user_id)
            finally:
                reset_current_scenario(token)
        else:
            turn = conversation_store.append_turn(
                user_id,
                user_text=user_text,
                assistant_text=verbatim_text,
                reply_source=verbatim_source,
                fallback_reason=None,
                safety_passed=True,
            )
            agent_result = AgentResult(
                response_text=verbatim_text,
                safety_passed=True,
                reply_source=verbatim_source,
                turn_id=str(turn.get("turn_id", "")),
                prompt_debug={
                    "route": verbatim_route,
                    "reply_source": verbatim_source,
                    "fallback_reason": "",
                    "user_id": user_id,
                    "user_text": user_text,
                    "memory_block": "",
                    "system_prompt": "",
                    "user_prompt_template": "",
                    "rendered_user_prompt": "",
                },
            )
        llm_latency_ms = max(
            0, int((time.perf_counter() - llm_started_at) * 1000)
        )

        prompt_payload = getattr(agent_result, "prompt_debug", None) or {}
        await _send_control(
            websocket,
            ServerControlMessage(type="llm_prompt", payload=prompt_payload),
        )
        _audit(
            user_id,
            event_type="llm_prompt",
            payload=prompt_payload,
            turn_id=turn_id,
        )
        await _send_control(
            websocket,
            ServerControlMessage(
                type="llm_reply_start",
                source=agent_result.reply_source,
                detail=agent_result.fallback_reason,
            ),
        )
        _audit(
            user_id,
            event_type="llm_reply_start",
            payload={
                "source": agent_result.reply_source,
                "detail": agent_result.fallback_reason or "",
            },
            turn_id=turn_id,
        )
        for chunk in stream_text_chunks(agent_result.response_text):
            await _send_control(
                websocket,
                ServerControlMessage(type="llm_reply_delta", text=chunk),
            )
            _audit(
                user_id,
                event_type="llm_reply_delta",
                payload={"text": chunk},
                turn_id=turn_id,
            )
            # Pace chunks lightly for a typewriter effect without delaying tts_start.
            await anyio.sleep(0.005)
        await _send_control(
            websocket,
            ServerControlMessage(
                type="llm_reply_end",
                text=agent_result.response_text,
                source=agent_result.reply_source,
                detail=agent_result.fallback_reason,
                latency_ms=llm_latency_ms,
            ),
        )
        _audit(
            user_id,
            event_type="llm_reply_end",
            payload={
                "text": agent_result.response_text,
                "source": agent_result.reply_source,
                "detail": agent_result.fallback_reason or "",
                "latency_ms": llm_latency_ms,
            },
            turn_id=turn_id,
        )
        persisted_turn_id = getattr(agent_result, "turn_id", "") or turn_id
        tts_url = (
            f"/api/memory/users/{user_id}/turns/{persisted_turn_id}/tts"
            if persisted_turn_id
            else None
        )
        await _send_control(
            websocket,
            ServerControlMessage(
                type="llm_reply",
                text=agent_result.response_text,
                source=agent_result.reply_source,
                detail=agent_result.fallback_reason,
                turn_id=persisted_turn_id,
                tts_url=tts_url,
                latency_ms=llm_latency_ms,
            ),
        )
        tts_audio = b""
        tts_bytes = 0
        tts_cache_hit = False
        tts_synthesis_complete = True
        tts_started = False
        tts_end_reason = "natural_end"
        tts_prepare_started_at = time.perf_counter()
        tts_prepare_latency_ms = 0
        tts_send_latency_ms = 0
        tts_store_latency_ms = 0
        stored_tts_url = None
        tts_provider = (
            "local" if isinstance(tts_service, LocalTTSService) else "volc"
        )
        try:
            # Buffer the whole synthesis first, then stream it in one paced pass.
            # Streaming each Volc chunk as it arrived surfaced upstream
            # generation gaps as choppy playback on the device; collecting first
            # lets the device receive a single, evenly-paced stream.
            if scene_prompt_plan is not None:
                # Scene prompts play cached PCM (zero TTS Token on cache hit).
                prompt_audio, _, tts_cache_hit = await _collect_scene_prompt_audio(
                    scene_prompt_plan.scene_id,
                    scene_prompt_plan.action,
                    scene_prompt_plan.narration,
                    # This live turn is accounted by its tts_start event below.
                    record_generation_usage=False,
                )
                tts_audio = prompt_audio
            else:
                cached_idiom_audio = (
                    _collect_idiom_cached_audio(idiom_plan.idiom, tts_sample_rate_hz)
                    if idiom_plan is not None
                    else b""
                )
                if cached_idiom_audio:
                    # 成语库命中：复用缓存 PCM，零合成、秒下发（修复「走缓存仍久等」）。
                    tts_audio = cached_idiom_audio
                    tts_cache_hit = True
                else:
                    (
                        tts_audio,
                        tts_cache_hit,
                        tts_synthesis_complete,
                    ) = await _collect_cached_reply_tts_audio(
                        tts_service,
                        agent_result.response_text,
                        settings=settings,
                        runtime_store=runtime_store,
                    )
            if not tts_audio:
                raise RuntimeError("tts produced no audio")
            if not tts_synthesis_complete:
                tts_end_reason = "partial"
            tts_bytes = len(tts_audio)
            tts_prepare_latency_ms = max(
                0, int((time.perf_counter() - tts_prepare_started_at) * 1000)
            )
            tts_source = (
                "cache"
                if tts_cache_hit
                else "local"
                if tts_provider == "local"
                else "real"
            )
            # tts_start now means audio is actually ready to send. The device stays
            # in the reply/thinking state while synthesis is still in progress.
            await _send_control(
                websocket,
                ServerControlMessage(
                    type="tts_start",
                    source=tts_source,
                    tts_sample_rate_hz=tts_sample_rate_hz,
                    latency_ms=tts_prepare_latency_ms,
                ),
            )
            tts_started = True
            _audit(
                user_id,
                event_type="tts_start",
                payload={
                    "metrics_version": 1,
                    "source": tts_source,
                    "provider": tts_provider,
                    "cache_hit": tts_cache_hit,
                    "synthesis_complete": tts_synthesis_complete,
                    "characters": len(agent_result.response_text),
                    "prepare_latency_ms": tts_prepare_latency_ms,
                },
                turn_id=turn_id,
            )
            if trace_plan is not None:
                await _send_character_trace(trace_plan, turn_id)
            elif idiom_plan is not None:
                await _send_idiom_display(idiom_plan, turn_id)
            elif scene_prompt_plan is not None:
                await _send_control(
                    websocket,
                    ServerControlMessage(
                        type="scene_changed",
                        scene_id=scene_prompt_plan.scene_id,
                        scene_action=scene_prompt_plan.action,
                        scene_name=scene_prompt_plan.name,
                        turn_id=turn_id,
                    ),
                )
                _audit(
                    user_id,
                    event_type="scene_changed",
                    payload={
                        "scene_id": scene_prompt_plan.scene_id,
                        "scene_action": scene_prompt_plan.action,
                        "scene_name": scene_prompt_plan.name,
                    },
                    turn_id=turn_id,
                )
            async def persist_turn_audio() -> None:
                nonlocal stored_tts_url, tts_store_latency_ms
                store_started_at = time.perf_counter()
                try:
                    updated = await anyio.to_thread.run_sync(
                        lambda: conversation_store.attach_tts_audio(
                            user_id,
                            persisted_turn_id,
                            audio_bytes=tts_audio,
                            audio_format="pcm_s16le",
                            sample_rate_hz=tts_sample_rate_hz,
                            byte_length=tts_bytes,
                        ),
                        abandon_on_cancel=True,
                    )
                    if updated:
                        stored_tts_url = (
                            f"/api/memory/users/{user_id}/turns/"
                            f"{persisted_turn_id}/tts"
                        )
                except Exception as exc:
                    _audit(
                        user_id,
                        event_type="tts_audio_store_error",
                        payload={"error": str(exc), "bytes": tts_bytes},
                        turn_id=persisted_turn_id,
                    )
                finally:
                    tts_store_latency_ms = max(
                        0, int((time.perf_counter() - store_started_at) * 1000)
                    )

            async def deliver_audio() -> None:
                nonlocal tts_send_latency_ms
                send_started_at = time.perf_counter()
                await _send_tts_audio_bytes(
                    websocket,
                    tts_audio,
                    device_id=device_id,
                    sample_rate_hz=tts_sample_rate_hz,
                )
                tts_send_latency_ms = max(
                    0, int((time.perf_counter() - send_started_at) * 1000)
                )

            # Disk migration/write runs off the event loop and overlaps paced
            # playback. On the first legacy turn this avoids serially adding a
            # full JSONL migration to the user's end-of-playback wait.
            async with anyio.create_task_group() as delivery_group:
                delivery_group.start_soon(deliver_audio)
                if (
                    persisted_turn_id
                    and tts_audio
                    and tts_end_reason == "natural_end"
                ):
                    delivery_group.start_soon(persist_turn_audio)
        except cancelled_exc:
            # Do not emit tts_end here; the interrupt path emits it with reason=barge_in.
            raise
        except Exception:
            if tts_started:
                await _send_control(
                    websocket,
                    ServerControlMessage(
                        type="tts_end",
                        reason="error",
                        latency_ms=max(
                            0, int((time.perf_counter() - turn_started_at) * 1000)
                        ),
                    ),
                )
                _audit(
                    user_id,
                    event_type="tts_end",
                    payload={
                        "metrics_version": 1,
                        "bytes": tts_bytes,
                        "reason": "error",
                        "cache_hit": tts_cache_hit,
                        "provider": tts_provider,
                        "characters": len(agent_result.response_text),
                        "prepare_latency_ms": tts_prepare_latency_ms,
                    },
                    turn_id=turn_id,
                )
            else:
                _audit(
                    user_id,
                    event_type="tts_prepare_error",
                    payload={
                        "metrics_version": 1,
                        "reason": "error",
                        "cache_hit": tts_cache_hit,
                        "provider": tts_provider,
                        "characters": len(agent_result.response_text),
                        "prepare_latency_ms": max(
                            0,
                            int(
                                (time.perf_counter() - tts_prepare_started_at)
                                * 1000
                            ),
                        ),
                    },
                    turn_id=turn_id,
                )
            raise
        await _send_control(
            websocket,
            ServerControlMessage(
                type="tts_end",
                reason=tts_end_reason,
                turn_id=persisted_turn_id,
                tts_url=stored_tts_url,
                tts_audio_format="pcm_s16le" if stored_tts_url else None,
                tts_sample_rate_hz=tts_sample_rate_hz if stored_tts_url else None,
                latency_ms=max(
                    0, int((time.perf_counter() - turn_started_at) * 1000)
                ),
            ),
        )
        _audit(
            user_id,
            event_type="tts_end",
            payload={
                "metrics_version": 1,
                "bytes": tts_bytes,
                "reason": tts_end_reason,
                "tts_url": stored_tts_url or "",
                "cache_hit": tts_cache_hit,
                "provider": (
                    "local" if isinstance(tts_service, LocalTTSService) else "volc"
                ),
                "characters": len(agent_result.response_text),
                "llm_latency_ms": llm_latency_ms,
                "prepare_latency_ms": tts_prepare_latency_ms,
                "send_latency_ms": tts_send_latency_ms,
                "store_latency_ms": tts_store_latency_ms,
                "total_latency_ms": max(
                    0, int((time.perf_counter() - turn_started_at) * 1000)
                ),
            },
            turn_id=persisted_turn_id or turn_id,
        )

    async def _scene_for_turn(
        user_text: str,
    ) -> tuple[str | None, ScenePromptPlan | None]:
        """Decide the scene action for this turn and apply the in-memory state
        transition. Returns (scene_id_for_conversation, scene_prompt_plan):
        - enter/exit → a ScenePromptPlan (play the fixed prompt verbatim);
        - converse  → scene_id of the active scene (for persona injection) or None.
        """
        nonlocal current_scene
        store = RuntimeConfigStore(settings.runtime_config_path)
        # persona 场景才有进入/退出/人设状态机；action 场景（汉字/成语）由
        # _plan_action_scene 在当轮单独检测，不参与 current_scene 状态机。
        persona_scenes = tuple(
            scene for scene in effective_scenes(store) if scene.kind == "persona"
        )
        decision: SceneDecision = await decide_scene_action(
            text=user_text,
            current_scene=current_scene,
            scenes=persona_scenes,
            llm_classify=classify_scene_intent,
        )
        if decision.action == "enter" and decision.scene_id:
            scene = effective_scene(store, decision.scene_id)
            current_scene = decision.scene_id
            if scene is not None:
                return decision.scene_id, ScenePromptPlan(
                    scene_id=scene.scene_id,
                    action="enter",
                    name=scene.name,
                    narration=scene.enter_prompt_text,
                )
            return decision.scene_id, None
        if decision.action == "exit" and decision.scene_id:
            scene = effective_scene(store, decision.scene_id)
            current_scene = None
            if scene is not None:
                return None, ScenePromptPlan(
                    scene_id=scene.scene_id,
                    action="exit",
                    name=scene.name,
                    narration=scene.exit_prompt_text,
                )
            return None, None
        return current_scene, None

    def _plan_hanzi_keyword_trace(
        user_text: str, scene, turn_id: str
    ) -> CharacterTracePlan | None:
        """汉字「可配触发词」扩展路径（标准抽取 extract_trace_character 未命中时的
        兜底）：取首个命中触发词**紧邻的那个汉字**作为描红目标。对重复说、带标点
        （如「大，怎么写？大，怎么写？」）也能稳抽——优先触发词前一个字，没有再取后
        一个字。"""
        compact = "".join((user_text or "").split())
        _STRIP = "的了吗呢啊呀这个字，。、?？!！~ "
        for keyword in scene.enter_keywords:
            keyword = (keyword or "").strip()
            if not keyword:
                continue
            idx = compact.find(keyword)
            if idx < 0:
                continue
            before = compact[:idx].strip(_STRIP)
            after = compact[idx + len(keyword):].strip(_STRIP)
            candidate = None
            if before and "一" <= before[-1] <= "鿿":
                candidate = before[-1]  # 触发词前一个字：大[怎么写]
            elif after and "一" <= after[0] <= "鿿":
                candidate = after[0]  # 触发词在前：[写一下]大
            if candidate:
                payload = character_trace_payload(candidate)
                if payload is not None:
                    return CharacterTracePlan(
                        payload=payload,
                        narration=build_trace_narration(candidate, payload),
                    )
        return None

    async def _plan_action_scene(
        user_text: str, turn_id: str
    ) -> tuple[CharacterTracePlan | None, IdiomDisplayPlan | None]:
        """动作型场景（汉字/成语）当轮一次性命中检测。优先级：成语具体查询 A >
        汉字描红 > 成语泛意图随机 B。命中返回 (trace_plan, idiom_plan)，至多一个非空。"""
        store = RuntimeConfigStore(settings.runtime_config_path)
        actions = {
            scene.scene_id: scene
            for scene in effective_scenes(store)
            if scene.kind == "action" and scene.enabled
        }
        idiom_scene = actions.get("idiom_learning")
        # A) 成语：问具体成语（库内缓存 / 库外四字新增入库）。
        if idiom_scene is not None:
            plan = await _plan_idiom_query(user_text, store)
            if plan is not None:
                return None, plan
        # B) 汉字描红（标准抽取 + 可配触发词扩展）。
        hanzi_scene = actions.get("hanzi_writing")
        if hanzi_scene is not None:
            trace_plan = _plan_character_trace(user_text, turn_id)
            if trace_plan is None:
                trace_plan = _plan_hanzi_keyword_trace(user_text, hanzi_scene, turn_id)
            if trace_plan is not None:
                return trace_plan, None
        # C) 成语：泛意图触发词 → 随机一条。
        if idiom_scene is not None and match_enter_keyword(user_text, (idiom_scene,)):
            plan = await _plan_idiom_random(store)
            if plan is not None:
                return None, plan
        return None, None

    async def _plan_turn_scene(
        user_text: str, turn_id: str
    ) -> tuple[str | None, ScenePromptPlan | None, CharacterTracePlan | None, IdiomDisplayPlan | None]:
        """一轮的场景裁决（语音/文本两路共用）：动作场景优先（一次性动作，不改
        current_scene），否则走 persona 场景进出 + 普通对话。返回
        (scene_id, scene_prompt_plan, trace_plan, idiom_plan)。"""
        trace_plan, idiom_plan = await _plan_action_scene(user_text, turn_id)
        if trace_plan is not None or idiom_plan is not None:
            return None, None, trace_plan, idiom_plan
        scene_id_for_turn, scene_prompt_plan = await _scene_for_turn(user_text)
        return scene_id_for_turn, scene_prompt_plan, None, None

    async def _trigger_text_turn(
        tg: anyio.abc.TaskGroup,
        text: str,
        turn_user_id: str,
        trace_plan_override: CharacterTracePlan | None = None,
        idiom_plan_override: IdiomDisplayPlan | None = None,
    ) -> bool:
        """Start a text-driven turn (shared by text_input and HTTP /say).

        When `trace_plan_override` is given (console 「生字管理」 trace-say) or
        `idiom_plan_override` is given (console 「成语学习」 idiom-say), the turn
        skips both the LLM and the intent-based trace detection: it speaks the
        pre-generated narration verbatim and shows the supplied display."""
        nonlocal user_id, active_turn_cancel_scope, barge_in_started_at_ms
        barge_in_started_at_ms = int(anyio.current_time() * 1000)
        _audit(
            user_id,
            event_type="barge_in_received",
            payload={"reason": "new_text"},
        )
        await _cancel_active_turn("barge_in")
        user_id = turn_user_id or user_id
        user_text = (text or "").strip()
        if not user_text:
            await _send_control(
                websocket,
                ServerControlMessage(type="error", message="text must not be empty"),
            )
            return False
        await _send_control(
            websocket,
            ServerControlMessage(type="text_input_ack", text=user_text),
        )
        turn_id = f"ws_text_{uuid.uuid4().hex[:12]}"
        _audit(
            user_id,
            event_type="text_input",
            payload={"text": user_text},
            turn_id=turn_id,
        )
        # An idiom-say override speaks verbatim with an idiom display and must not
        # also trigger single-character trace detection or scene intent detection.
        scene_id_for_turn: str | None = None
        scene_prompt_plan: ScenePromptPlan | None = None
        idiom_plan = idiom_plan_override
        if idiom_plan_override is not None:
            trace_plan = None
        elif trace_plan_override is not None:
            trace_plan = trace_plan_override
        else:
            scene_id_for_turn, scene_prompt_plan, trace_plan, idiom_plan = (
                await _plan_turn_scene(user_text, turn_id)
            )
        cancel_scope = anyio.CancelScope()
        active_turn_cancel_scope = cancel_scope

        async def run_turn_in_scope() -> None:
            nonlocal active_turn_cancel_scope
            try:
                with cancel_scope:
                    await _run_turn(
                        user_text=user_text,
                        turn_id=turn_id,
                        agent=_build_runtime_agent(scene_id=scene_id_for_turn),
                        tts_service=_build_runtime_tts_service(settings),
                        trace_plan=trace_plan,
                        idiom_plan=idiom_plan,
                        scene_id=scene_id_for_turn,
                        scene_prompt_plan=scene_prompt_plan,
                    )
            except anyio.get_cancelled_exc_class():
                raise
            except Exception as exc:
                await _send_control(
                    websocket,
                    ServerControlMessage(
                        type="error",
                        message=f"speech turn failed: {exc}",
                    ),
                )
                _audit(
                    user_id,
                    event_type="speech_turn_error",
                    payload={"error": str(exc)},
                    turn_id=turn_id,
                )
            finally:
                if active_turn_cancel_scope is cancel_scope:
                    active_turn_cancel_scope = None

        tg.start_soon(run_turn_in_scope)
        return True

    try:
        async with anyio.create_task_group() as tg:
            async def consume_commands() -> None:
                # Consume "say" commands pushed by HTTP /api/devices/{id}/say.
                async with command_recv:
                    async for command in command_recv:
                        if not authenticated:
                            continue
                        if command.get("type") == "say":
                            await _trigger_text_turn(
                                tg,
                                command.get("text", ""),
                                command.get("user_id") or user_id,
                            )
                        elif command.get("type") == "trace_say":
                            # Console 「生字管理」: speak a pre-generated explanation
                            # verbatim (no LLM) while showing the character trace.
                            trace_character = str(command.get("character", "")).strip()
                            trace_text = str(command.get("text", "")).strip()
                            trace_payload = (
                                character_trace_payload(trace_character)
                                if trace_character
                                else None
                            )
                            if trace_payload is not None and trace_text:
                                await _trigger_text_turn(
                                    tg,
                                    trace_text,
                                    command.get("user_id") or user_id,
                                    trace_plan_override=CharacterTracePlan(
                                        payload=trace_payload,
                                        narration=trace_text,
                                    ),
                                )
                        elif command.get("type") == "idiom_say":
                            # Console 「成语学习」: speak a pre-generated narration
                            # verbatim (no LLM) while showing the idiom big chars.
                            idiom_text = str(command.get("idiom", "")).strip()
                            say_text = str(command.get("text", "")).strip()
                            glyphs = command.get("glyphs")
                            if idiom_text and say_text and isinstance(glyphs, list):
                                await _trigger_text_turn(
                                    tg,
                                    say_text,
                                    command.get("user_id") or user_id,
                                    idiom_plan_override=IdiomDisplayPlan(
                                        idiom=idiom_text,
                                        pinyin=str(command.get("pinyin", "")),
                                        glyphs=glyphs,
                                        narration=say_text,
                                    ),
                                )
                        elif command.get("type") == "set_volume":
                            await _send_control(
                                websocket,
                                ServerControlMessage(
                                    type="set_volume",
                                    volume=command.get("volume"),
                                ),
                            )
                        elif command.get("type") == "set_conversation":
                            await _send_control(
                                websocket,
                                ServerControlMessage(
                                    type="set_conversation",
                                    enabled=command.get("enabled"),
                                ),
                            )
                        elif command.get("type") == "show_color_test":
                            await _send_control(
                                websocket,
                                ServerControlMessage(
                                    type="show_color_test",
                                    color=command.get("color") or "bars",
                                ),
                            )
                        elif command.get("type") == "show_character_trace":
                            payload = command.get("payload")
                            if isinstance(payload, dict):
                                await _send_control(
                                    websocket,
                                    ServerControlMessage(
                                        type="show_character_trace",
                                        payload=payload,
                                        character=payload.get("character"),
                                        duration_ms=payload.get("duration_ms"),
                                        strokes=payload.get("strokes"),
                                        outlines=payload.get("outlines"),
                                    ),
                                )

            async def recv_loop() -> None:
                nonlocal authenticated, device_id, user_id, audio_chunks, collecting_audio, audio_segment_started_at, audio_segment_bytes, active_turn_cancel_scope, active_audio_codec, active_audio_sample_rate_hz, active_asr_provider, opus_decoder, asr_session, asr_session_failed
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        break

                    if message.get("bytes") is not None:
                        if authenticated and collecting_audio:
                            chunk = message["bytes"]
                            segment_elapsed = (
                                time.perf_counter() - audio_segment_started_at
                                if audio_segment_started_at is not None
                                else 0.0
                            )
                            if (
                                len(chunk) > MAX_AUDIO_FRAME_BYTES
                                or audio_segment_bytes + len(chunk)
                                > MAX_AUDIO_SEGMENT_BYTES
                                or segment_elapsed > MAX_AUDIO_SEGMENT_SECONDS
                            ):
                                await _fail_audio_segment(
                                    RuntimeError(
                                        "audio segment limit exceeded "
                                        f"(frame<={MAX_AUDIO_FRAME_BYTES}, "
                                        f"segment<={MAX_AUDIO_SEGMENT_BYTES} bytes/"
                                        f"{int(MAX_AUDIO_SEGMENT_SECONDS)}s)"
                                    ),
                                    message="这段语音太长，请说短一点再试",
                                    reason="audio_limit",
                                )
                                continue
                            audio_segment_bytes += len(chunk)
                            if active_audio_codec == "pcm_s16le" and asr_session is not None:
                                # Stream PCM chunks straight to the recognizer so
                                # partial transcripts come back while the user is
                                # still speaking. Feeds must stay ordered. An upstream
                                # ASR failure must abort only this segment, never tear
                                # down the whole websocket.
                                try:
                                    await asr_session.feed(message["bytes"])
                                except Exception as exc:  # noqa: BLE001
                                    await _fail_audio_segment(exc)
                            elif active_audio_codec == "pcm_s16le" and asr_session_failed:
                                # Segment already aborted: drop trailing audio quietly
                                # until audio_end instead of spamming errors.
                                pass
                            else:
                                audio_chunks.append(message["bytes"])
                        elif authenticated:
                            await _send_control(
                                websocket,
                                ServerControlMessage(
                                    type="error", message="audio_start required"
                                ),
                            )
                        else:
                            await _send_control(
                                websocket,
                                ServerControlMessage(type="error", message="auth required"),
                            )
                        continue

                    if message.get("text") is None:
                        continue

                    try:
                        control = ClientControlMessage.model_validate_json(message["text"])
                    except ValidationError:
                        await _send_control(
                            websocket,
                            ServerControlMessage(type="error", message="invalid control"),
                        )
                        continue

                    if control.type == "auth":
                        if control.token != settings.auth_token:
                            await _send_control(
                                websocket,
                                ServerControlMessage(type="error", message="auth failed"),
                            )
                            await websocket.close(code=1008)
                            break
                        authenticated = True
                        device_id = control.device_id
                        _register_connection(
                            id(websocket),
                            control.device_id,
                            transport=control.transport,
                            gateway_id=control.gateway_id,
                            command_send=command_send,
                        )
                        await _send_control(
                            websocket,
                            ServerControlMessage(type="auth_ok", text="ok"),
                        )
                        continue

                    if not authenticated:
                        await _send_control(
                            websocket,
                            ServerControlMessage(type="error", message="auth required"),
                        )
                        continue

                    _touch_connection(id(websocket))

                    if control.type == "heartbeat":
                        await _send_control(websocket, ServerControlMessage(type="heartbeat"))
                        continue

                    if control.type == "device_debug":
                        # 固件诊断行（本板串口不可靠，改走 WS）。打印到服务端日志并
                        # 转发给该设备的 SSE 监控端，便于实时观察麦克风/采集状态。
                        print(f"device_debug[{device_id}]: {control.text}", flush=True)
                        _publish_event(
                            device_id,
                            {
                                "event_type": "device_debug",
                                "text": control.text,
                                "ts": anyio.current_time(),
                            },
                        )
                        continue

                    if control.type == "barge_in":
                        nonlocal barge_in_started_at_ms
                        barge_in_started_at_ms = int(anyio.current_time() * 1000)
                        _audit(
                            user_id,
                            event_type="barge_in_received",
                            payload={"reason": getattr(control, "reason", "manual")},
                        )
                        await _cancel_active_turn("barge_in")
                        continue

                    if control.type == "audio_start":
                        # New audio input interrupts any ongoing playback.
                        barge_in_started_at_ms = int(anyio.current_time() * 1000)
                        _audit(
                            user_id,
                            event_type="barge_in_received",
                            payload={"reason": "new_audio"},
                        )
                        await _cancel_active_turn("barge_in")
                        # Discard any half-open session from a previous segment
                        # that never reached audio_end (e.g. back-to-back starts).
                        if asr_session is not None:
                            await asr_session.aclose()
                            asr_session = None
                        asr_session_failed = False
                        user_id = control.user_id
                        audio_chunks = []
                        collecting_audio = True
                        audio_segment_started_at = time.perf_counter()
                        audio_segment_bytes = 0
                        active_audio_codec = control.codec
                        active_audio_sample_rate_hz = control.sample_rate_hz

                        async def _forward_partial(text: str) -> None:
                            await _send_control(
                                websocket,
                                ServerControlMessage(type="asr_partial", text=text),
                            )

                        # Building/opening the recognizer can raise (missing config,
                        # connect/auth failure). Keep the segment-local failure from
                        # tearing down the websocket.
                        try:
                            asr = _build_runtime_asr_service(settings, scene_id=current_scene)
                            active_asr_provider = (
                                "local"
                                if isinstance(asr, LocalASRService)
                                else "volc"
                            )
                            if hasattr(asr, "open_stream") and active_audio_codec == "pcm_s16le":
                                asr_session = asr.open_stream(
                                    sample_rate_hz=control.sample_rate_hz,
                                    on_partial=_forward_partial,
                                )
                            else:
                                # Opus (and any non-streaming ASR mock) buffers the
                                # whole segment and transcribes it at audio_end.
                                asr_session = _BufferingASRSession(asr)
                        except Exception as exc:  # noqa: BLE001
                            await _fail_audio_segment(exc)
                        continue

                    if control.type == "text_input":
                        # New text input interrupts any ongoing playback and triggers a new turn.
                        await _trigger_text_turn(tg, control.text, control.user_id)
                        continue

                    if control.type == "audio_end":
                        if not collecting_audio:
                            await _send_control(
                                websocket,
                                ServerControlMessage(
                                    type="error", message="audio_start required"
                                ),
                            )
                            continue
                        collecting_audio = False
                        if asr_session_failed:
                            # Segment already failed mid-stream and was reported;
                            # clean up and wait for the next audio_start.
                            asr_session_failed = False
                            if asr_session is not None:
                                await asr_session.aclose()
                                asr_session = None
                            audio_chunks = []
                            continue
                        raw_audio = b"".join(audio_chunks)
                        audio_chunks = []
                        wall_audio_duration_ms = (
                            max(
                                0,
                                int(
                                    (time.perf_counter() - audio_segment_started_at)
                                    * 1000
                                ),
                            )
                            if audio_segment_started_at is not None
                            else 0
                        )
                        audio_duration_ms = wall_audio_duration_ms
                        if (
                            active_audio_codec == "pcm_s16le"
                            and active_audio_sample_rate_hz > 0
                            and audio_segment_bytes > 0
                        ):
                            # PCM is mono s16le: sample count is exact even when a
                            # browser uploads faster than real time. Wall time is
                            # retained for Opus where packet bytes are compressed.
                            audio_duration_ms = max(
                                0,
                                round(
                                    audio_segment_bytes
                                    * 1000
                                    / (active_audio_sample_rate_hz * 2)
                                ),
                            )
                        captured_audio_bytes = audio_segment_bytes
                        audio_segment_started_at = None
                        audio_segment_bytes = 0
                        turn_id = f"ws_turn_{uuid.uuid4().hex[:12]}"

                        cancel_scope = anyio.CancelScope()
                        active_turn_cancel_scope = cancel_scope
                        turn_session = asr_session
                        asr_session = None

                        async def run_turn_in_scope(
                            turn_audio: bytes,
                            turn_id_value: str,
                            turn_audio_codec: str,
                            turn_cancel_scope: anyio.CancelScope,
                            session: ASRStreamSession,
                            capture_duration_ms: int,
                            capture_wall_duration_ms: int,
                            capture_bytes: int,
                            capture_sample_rate_hz: int,
                            asr_provider: str,
                        ) -> None:
                            nonlocal active_turn_cancel_scope, opus_decoder, raw_opus_decoder
                            try:
                                with turn_cancel_scope:
                                    asr_started_at = time.perf_counter()
                                    if turn_audio_codec in ("opus", "opus_raw"):
                                        try:
                                            if turn_audio_codec == "opus_raw":
                                                if raw_opus_decoder is None:
                                                    raw_opus_decoder = RawOpusDecoder16k()
                                                pcm_audio = raw_opus_decoder.decode(turn_audio)
                                            else:
                                                if opus_decoder is None:
                                                    opus_decoder = OpusDecoder16k()
                                                pcm_audio = opus_decoder.decode(turn_audio)
                                        except OpusCodecUnavailable as exc:
                                            await _send_control(
                                                websocket,
                                                ServerControlMessage(type="error", message=str(exc)),
                                            )
                                            return
                                        await session.feed(pcm_audio)
                                    user_text = (await session.finish()).strip()
                                    asr_latency_ms = max(
                                        0,
                                        int(
                                            (time.perf_counter() - asr_started_at)
                                            * 1000
                                        ),
                                    )
                                    if not user_text:
                                        await _send_control(
                                            websocket,
                                            ServerControlMessage(
                                                type="error",
                                                message="没有听清，请再说一次",
                                                reason="no_speech",
                                            ),
                                        )
                                        _audit(
                                            user_id,
                                            event_type="speech_turn_error",
                                            payload={"error": "empty_asr_result"},
                                            turn_id=turn_id_value,
                                        )
                                        return
                                    await _send_control(
                                        websocket,
                                        ServerControlMessage(
                                            type="asr_result",
                                            text=user_text,
                                            latency_ms=asr_latency_ms,
                                        ),
                                    )
                                    _audit(
                                        user_id,
                                        event_type="asr_result",
                                        payload={
                                            "metrics_version": 1,
                                            "text": user_text,
                                            "latency_ms": asr_latency_ms,
                                            "end_to_final_latency_ms": asr_latency_ms,
                                            "total_latency_ms": (
                                                capture_wall_duration_ms
                                                + asr_latency_ms
                                            ),
                                            "audio_duration_ms": capture_duration_ms,
                                            "capture_wall_duration_ms": (
                                                capture_wall_duration_ms
                                            ),
                                            "audio_bytes": capture_bytes,
                                            "sample_rate_hz": capture_sample_rate_hz,
                                            "codec": turn_audio_codec,
                                            "provider": asr_provider,
                                        },
                                        turn_id=turn_id_value,
                                    )
                                    (
                                        scene_id_for_turn,
                                        scene_prompt_plan,
                                        trace_plan,
                                        idiom_plan,
                                    ) = await _plan_turn_scene(
                                        user_text, turn_id_value
                                    )
                                    await _run_turn(
                                        user_text=user_text,
                                        turn_id=turn_id_value,
                                        agent=_build_runtime_agent(
                                            scene_id=scene_id_for_turn
                                        ),
                                        tts_service=_build_runtime_tts_service(settings),
                                        trace_plan=trace_plan,
                                        idiom_plan=idiom_plan,
                                        scene_id=scene_id_for_turn,
                                        scene_prompt_plan=scene_prompt_plan,
                                    )
                            except anyio.get_cancelled_exc_class():
                                raise
                            except Exception as exc:
                                await _send_control(
                                    websocket,
                                    ServerControlMessage(
                                        type="error",
                                        message="语音处理失败，请再说一次",
                                        detail=str(exc),
                                        reason="speech_turn_failed",
                                    ),
                                )
                                _audit(
                                        user_id,
                                        event_type="speech_turn_error",
                                        payload={"error": str(exc)},
                                        turn_id=turn_id_value,
                                    )
                            finally:
                                await session.aclose()
                                if active_turn_cancel_scope is turn_cancel_scope:
                                    active_turn_cancel_scope = None

                        if turn_session is None:
                            turn_session = _BufferingASRSession(
                                _build_runtime_asr_service(settings, scene_id=current_scene)
                            )
                        tg.start_soon(
                            run_turn_in_scope,
                            raw_audio,
                            turn_id,
                            active_audio_codec,
                            cancel_scope,
                            turn_session,
                            audio_duration_ms,
                            wall_audio_duration_ms,
                            captured_audio_bytes,
                            active_audio_sample_rate_hz,
                            active_asr_provider,
                        )

            tg.start_soon(consume_commands)
            try:
                await recv_loop()
            finally:
                await command_send.aclose()
    except WebSocketDisconnect:
        pass
    finally:
        _unregister_connection(id(websocket))
        if websocket.client_state is not WebSocketState.DISCONNECTED:
            try:
                await websocket.close()
            except RuntimeError:
                pass
