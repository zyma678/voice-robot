import base64
import binascii
import hashlib
import json
import time
from pathlib import Path

from fastapi.responses import Response, StreamingResponse
from typing import Literal

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Query

from voice_robot.agent.graph import VoiceAgent, stream_text_chunks
from voice_robot.config import get_settings
from voice_robot.memory.audit_log_store import AuditLogStore
from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.file_store import FileMemoryStore
from voice_robot.memory.summary_store import SummaryStore
from voice_robot.memory.vocabulary_store import VocabularyStore
from voice_robot.metrics.token_usage_store import TokenUsageStore
from voice_robot.metrics.interaction_metrics import InteractionMetrics
from voice_robot.metrics.context import get_current_scenario, reset_current_scenario, set_current_scenario
from voice_robot.runtime_config import (
    RuntimeConfigStore,
    mask_secret,
    resolve_llm_base_url,
    resolve_llm_api_key,
    resolve_llm_model,
    resolve_llm_system_prompt,
    resolve_llm_user_prompt_template,
    resolve_speech_asr_api_key,
    resolve_speech_asr_resource_id,
    resolve_speech_asr_sample_rate_hz,
    resolve_speech_asr_ws_url,
    resolve_speech_mode,
    resolve_speech_tts_api_key,
    resolve_speech_tts_encoding,
    resolve_speech_tts_resource_id,
    resolve_speech_tts_sample_rate_hz,
    resolve_speech_tts_voice_type,
    resolve_speech_tts_ws_url,
)
from voice_robot.scenes import defaults as scene_defaults
from voice_robot.scenes.registry import effective_scene, effective_scenes
from voice_robot.scenes.scene import SceneDefinition
from voice_robot.services.llm import (
    ArkChatConfig,
    ArkChatLLMService,
    ArkPromptConfig,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_USER_PROMPT_TEMPLATE,
    reply_max_tokens,
)
from voice_robot.services.tts import LocalTTSService


router = APIRouter(prefix="/api", tags=["admin"])


class LLMSettingsUpdate(BaseModel):
    model: str = Field(default="", max_length=256)
    base_url: str = Field(default="", max_length=2048)
    system_prompt: str = Field(default="", max_length=8000)
    user_prompt_template: str = Field(default="", max_length=8000)
    api_key: str = Field(default="", max_length=4096)


class BotProfileSettingsUpdate(BaseModel):
    enabled: bool = False
    name: str = Field(default="", max_length=256)
    persona: str = Field(default="", max_length=2000)
    response_style: str = Field(default="", max_length=2000)
    custom_prompt: str = Field(default="", max_length=4000)


class SceneSettingsUpdate(BaseModel):
    enabled: bool = True
    enter_keywords: list[str] = Field(default_factory=list, max_length=128)
    exit_keywords: list[str] = Field(default_factory=list, max_length=128)
    persona_prompt: str = Field(default="", max_length=8000)
    enter_prompt_text: str = Field(default="", max_length=1000)
    exit_prompt_text: str = Field(default="", max_length=1000)
    asr_hotwords: list[str] = Field(default_factory=list, max_length=256)


class SpeechSettingsUpdate(BaseModel):
    mode: str = Field(default="", max_length=64)
    asr_api_key: str = Field(default="", max_length=4096)
    asr_resource_id: str = Field(default="", max_length=256)
    asr_ws_url: str = Field(default="", max_length=2048)
    asr_sample_rate_hz: int = Field(default=16000, ge=8000, le=48000)
    tts_api_key: str = Field(default="", max_length=4096)
    tts_resource_id: str = Field(default="", max_length=256)
    tts_ws_url: str = Field(default="", max_length=2048)
    tts_sample_rate_hz: int = Field(default=16000, ge=8000, le=48000)
    tts_encoding: str = Field(default="pcm", max_length=64)
    tts_voice_type: str = Field(default="", max_length=128)


class UISettingsUpdate(BaseModel):
    character: Literal["princess", "cat"] = "princess"


class VocabSectionPayload(BaseModel):
    title: str = ""
    characters: list[str] = Field(default_factory=list)


class VocabGroupPayload(BaseModel):
    characters: list[str] = Field(default_factory=list, max_length=500)
    statuses: dict[str, str] = Field(default_factory=dict)
    sections: list[VocabSectionPayload] = Field(default_factory=list)


class VocabGroupsSettingsUpdate(BaseModel):
    active_group: str = Field(default="一年级（下）生字", min_length=1, max_length=128)
    active_status_filter: Literal["all", "learned", "unlearned", "new"] = "all"
    groups: dict[str, VocabGroupPayload] = Field(default_factory=dict)
    prompt_template: str = ""


class IdiomSectionPayload(BaseModel):
    title: str = ""
    idioms: list[str] = Field(default_factory=list)


class IdiomGroupPayload(BaseModel):
    idioms: list[str] = Field(default_factory=list, max_length=500)
    statuses: dict[str, str] = Field(default_factory=dict)
    sections: list[IdiomSectionPayload] = Field(default_factory=list)


class IdiomGroupsSettingsUpdate(BaseModel):
    active_group: str = Field(default="成语 · 第一批", min_length=1, max_length=128)
    active_status_filter: Literal["all", "learned", "unlearned", "new"] = "all"
    groups: dict[str, IdiomGroupPayload] = Field(default_factory=dict)
    explanation_prompt_template: str = ""
    story_prompt_template: str = ""


class ChatRequest(BaseModel):
    user_id: str = Field(default="default-user", max_length=128)
    text: str = Field(min_length=1, max_length=2000)


class TurnPatch(BaseModel):
    user_text: str | None = Field(default=None, max_length=4000)
    assistant_text: str | None = Field(default=None, max_length=4000)
    reply_source: str | None = Field(default=None, max_length=128)
    fallback_reason: str | None = Field(default=None, max_length=1000)
    safety_passed: bool | None = None


class SummaryPatch(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    summary_source: str = Field(default="manual", max_length=128)


class MemoryEntryPatch(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    content: str | None = Field(default=None, max_length=8000)
    tags: list[str] | None = None
    importance: float | None = None
    status: str | None = Field(default=None, max_length=64)
    confidence: float | None = None
    source_turn_id: str | None = Field(default=None, max_length=128)


class AuditEventPatch(BaseModel):
    event_type: str | None = Field(default=None, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    payload: dict | None = None


def _render_bot_profile_prompt(bot_profile: dict[str, str | bool]) -> str:
    if not bot_profile.get("enabled", False):
        return ""
    lines = ["【Bot 画像】"]
    field_labels = (
        ("name", "名称"),
        ("persona", "角色画像"),
        ("response_style", "回复风格"),
        ("custom_prompt", "自定义补充"),
    )
    for key, label in field_labels:
        value = str(bot_profile.get(key, "")).strip()
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _resolved_bot_profile_settings(
    store: RuntimeConfigStore,
) -> dict[str, str | bool]:
    bot_profile = store.load_bot_profile_settings()
    return {
        **bot_profile,
        "source": "runtime_override"
        if bot_profile.get("enabled", False)
        or any(
            str(bot_profile.get(key, "")).strip()
            for key in ("name", "persona", "response_style", "custom_prompt")
        )
        else "unset",
        "rendered_prompt": _render_bot_profile_prompt(bot_profile),
    }


def _resolved_base_prompt_config(
    store: RuntimeConfigStore,
) -> tuple[ArkPromptConfig, str, str]:
    system_prompt, system_source = resolve_llm_system_prompt(
        store, DEFAULT_SYSTEM_PROMPT
    )
    user_prompt_template, user_source = resolve_llm_user_prompt_template(
        store, DEFAULT_USER_PROMPT_TEMPLATE
    )
    return (
        ArkPromptConfig(
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
        ),
        system_source,
        user_source,
    )


def _render_scene_persona_prompt(scene: SceneDefinition) -> str:
    persona = scene.persona_prompt.strip()
    if not persona:
        return ""
    return f"【{scene.name}场景】\n{persona}"


def _resolved_prompt_config(
    store: RuntimeConfigStore, scene_id: str | None = None
) -> tuple[ArkPromptConfig, str, str]:
    prompt_config, system_source, user_source = _resolved_base_prompt_config(store)
    system_prompt = prompt_config.system_prompt
    bot_profile_prompt = _render_bot_profile_prompt(store.load_bot_profile_settings())
    if bot_profile_prompt:
        system_prompt = f"{system_prompt.rstrip()}\n\n{bot_profile_prompt}"
    if scene_id:
        scene = effective_scene(store, scene_id)
        if scene is not None and scene.enabled:
            scene_prompt = _render_scene_persona_prompt(scene)
            if scene_prompt:
                system_prompt = f"{system_prompt.rstrip()}\n\n{scene_prompt}"
    return (
        ArkPromptConfig(
            system_prompt=system_prompt,
            user_prompt_template=prompt_config.user_prompt_template,
        ),
        system_source,
        user_source,
    )


def _record_runtime_llm_usage(
    usage: dict,
    *,
    default_user_id: str,
    model: str,
    scenario: str,
) -> None:
    if not isinstance(usage, dict):
        return

    def metric_int(key: str) -> int:
        value = usage.get(key, 0)
        return value if type(value) is int else 0

    latency = usage.get("latency_ms")
    request_max = usage.get("request_max_tokens")
    _token_usage_store().append_event(
        user_id=str(usage.get("user_id", "")).strip() or default_user_id,
        scenario=scenario or get_current_scenario(),
        vendor="ark",
        model=model,
        prompt_tokens=metric_int("prompt_tokens"),
        completion_tokens=metric_int("completion_tokens"),
        total_tokens=metric_int("total_tokens"),
        success=usage.get("success", True) is not False,
        request_kind=str(usage.get("request_kind", "unknown")),
        request_max_tokens=request_max if type(request_max) is int else None,
        latency_ms=latency if type(latency) is int else None,
        prompt_chars=metric_int("prompt_chars"),
        completion_chars=metric_int("completion_chars"),
        error_code=str(usage.get("error_code", "")),
    )


def _build_runtime_llm_service() -> ArkChatLLMService:
    """构建当前运行态配置下的 Ark LLM 服务（与 `_build_runtime_agent` 同源）。
    供需要直接调用 LLM（绕过对话 agent）的场景使用，如「生字管理」预生成解读。"""
    settings = get_settings()
    runtime_store = _runtime_config_store()
    llm_model, _ = resolve_llm_model(settings, runtime_store)
    llm_base_url, _ = resolve_llm_base_url(settings, runtime_store)

    return ArkChatLLMService(
        config=ArkChatConfig(base_url=llm_base_url, model=llm_model),
        api_key_provider=lambda: resolve_llm_api_key(settings, runtime_store)[0],
        prompt_provider=lambda: _resolved_prompt_config(runtime_store)[0],
        usage_recorder=lambda usage: _record_runtime_llm_usage(
            usage,
            default_user_id=settings.default_user_id,
            model=llm_model,
            scenario=get_current_scenario(),
        ),
    )


def _build_runtime_agent(
    memory_root: Path | None = None, scene_id: str | None = None
) -> VoiceAgent:
    settings = get_settings()
    runtime_store = _runtime_config_store()
    llm_model, _ = resolve_llm_model(settings, runtime_store)
    llm_base_url, _ = resolve_llm_base_url(settings, runtime_store)

    llm_service = ArkChatLLMService(
        config=ArkChatConfig(base_url=llm_base_url, model=llm_model),
        api_key_provider=lambda: resolve_llm_api_key(settings, runtime_store)[0],
        prompt_provider=lambda: _resolved_prompt_config(runtime_store, scene_id)[0],
        usage_recorder=lambda usage: _record_runtime_llm_usage(
            usage,
            default_user_id=settings.default_user_id,
            model=llm_model,
            scenario=get_current_scenario(),
        ),
    )
    return VoiceAgent(memory_root=memory_root or settings.memory_root, llm_service=llm_service)


async def classify_scene_intent(
    text: str, scenes: tuple[SceneDefinition, ...]
) -> str | None:
    """LLM 兜底意图分类（仅在关键词未命中且含弱学习信号时由场景控制器调用）。
    输出受限：返回某个 scene_id 或 None。失败/不确定即返回 None（回落普通对话）。"""
    if not scenes:
        return None
    options = "\n".join(
        f"- {scene.scene_id}: {scene.intent_hint or scene.name}" for scene in scenes
    )
    valid_ids = [scene.scene_id for scene in scenes]
    system_prompt = (
        "你是一个意图分类器，判断小朋友的一句话是否想进入某个学习场景。"
        "只输出一个场景 id，或在都不匹配时输出 none，不要输出任何多余文字、标点或解释。"
    )
    user_prompt = (
        f"可选场景：\n{options}\n\n"
        "示例：\n"
        "用户的话：我们用英语聊天吧 -> english_tutor\n"
        "用户的话：今天天气怎么样 -> none\n\n"
        f"用户的话：{text} -> "
    )
    try:
        reply = await _build_runtime_llm_service().generate_freeform(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=16,
            temperature=0.0,
            usage_user_id="scene-classifier",
            request_kind="scene_classification",
        )
    except Exception:
        return None
    return _parse_scene_classification(reply, valid_ids)


def _parse_scene_classification(reply: str | None, valid_ids: list[str]) -> str | None:
    """Tolerant parse of the classifier reply: strips code fences / quotes / labels,
    accepts an exact id, an id substring, and treats an explicit `none` as no match."""
    answer = (reply or "").strip().lower()
    for noise in ("`", '"', "'", "。", "."):
        answer = answer.replace(noise, "")
    answer = answer.strip()
    if not answer:
        return None
    for scene_id in valid_ids:
        if answer == scene_id.lower():
            return scene_id
    # A bare "none" (possibly with a label like "scene: none") means no match.
    if "none" in answer:
        return None
    for scene_id in valid_ids:
        if scene_id.lower() in answer:
            return scene_id
    return None


def _memory_root() -> Path:
    return get_settings().memory_root


def _runtime_config_store() -> RuntimeConfigStore:
    return RuntimeConfigStore(get_settings().runtime_config_path)


def _conversation_store() -> ConversationStore:
    return ConversationStore(_memory_root())


def _summary_store() -> SummaryStore:
    return SummaryStore(_memory_root())


def _audit_store() -> AuditLogStore:
    return AuditLogStore(_memory_root())


def _file_memory_store() -> FileMemoryStore:
    return FileMemoryStore(_memory_root())


def _vocabulary_store() -> VocabularyStore:
    return VocabularyStore(_memory_root())


def _safe_user_id(user_id: str) -> str:
    return FileMemoryStore._safe_component(user_id)


def _load_json(path: Path) -> dict | list:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _token_usage_store() -> TokenUsageStore:
    return TokenUsageStore(_memory_root())


async def _collect_local_tts_audio(text: str, sample_rate_hz: int) -> bytes:
    service = LocalTTSService(sample_rate_hz=sample_rate_hz)
    chunks: list[bytes] = []
    async for chunk in service.synthesize(text):
        if chunk:
            chunks.append(chunk)
    return b"".join(chunks)


def _resolved_speech_settings(
    runtime_store: RuntimeConfigStore,
    *,
    reveal: bool,
) -> dict[str, str | bool]:
    mode, mode_source = resolve_speech_mode(runtime_store)
    asr_api_key, asr_api_key_source = resolve_speech_asr_api_key(runtime_store)
    asr_resource_id, asr_resource_id_source = resolve_speech_asr_resource_id(runtime_store)
    asr_ws_url, asr_ws_url_source = resolve_speech_asr_ws_url(runtime_store)
    asr_sr, asr_sr_source = resolve_speech_asr_sample_rate_hz(runtime_store)
    tts_api_key, tts_api_key_source = resolve_speech_tts_api_key(runtime_store)
    tts_resource_id, tts_resource_id_source = resolve_speech_tts_resource_id(runtime_store)
    tts_ws_url, tts_ws_url_source = resolve_speech_tts_ws_url(runtime_store)
    tts_sr, tts_sr_source = resolve_speech_tts_sample_rate_hz(runtime_store)
    tts_encoding, tts_encoding_source = resolve_speech_tts_encoding(runtime_store)
    tts_voice, tts_voice_source = resolve_speech_tts_voice_type(runtime_store)
    return {
        "configured": bool(
            (asr_api_key and asr_resource_id)
            or (tts_api_key and tts_voice)
        ),
        "mode": mode,
        "mode_source": mode_source,
        "asr_api_key": asr_api_key if reveal else "",
        "asr_api_key_masked": mask_secret(asr_api_key),
        "asr_api_key_source": asr_api_key_source,
        "asr_resource_id": asr_resource_id,
        "asr_resource_id_source": asr_resource_id_source,
        "asr_ws_url": asr_ws_url,
        "asr_ws_url_source": asr_ws_url_source,
        "asr_sample_rate_hz": str(asr_sr),
        "asr_sample_rate_hz_source": asr_sr_source,
        "tts_api_key": tts_api_key if reveal else "",
        "tts_api_key_masked": mask_secret(tts_api_key),
        "tts_api_key_source": tts_api_key_source,
        "tts_resource_id": tts_resource_id,
        "tts_resource_id_source": tts_resource_id_source,
        "tts_ws_url": tts_ws_url,
        "tts_ws_url_source": tts_ws_url_source,
        "tts_sample_rate_hz": str(tts_sr),
        "tts_sample_rate_hz_source": tts_sr_source,
        "tts_encoding": tts_encoding,
        "tts_encoding_source": tts_encoding_source,
        "tts_voice_type": tts_voice,
        "tts_voice_type_source": tts_voice_source,
    }


@router.get("/status")
async def get_status() -> dict[str, str]:
    settings = get_settings()
    runtime_store = _runtime_config_store()
    llm_api_key, llm_api_key_source = resolve_llm_api_key(settings, runtime_store)
    llm_model, llm_model_source = resolve_llm_model(settings, runtime_store)
    llm_base_url, llm_base_url_source = resolve_llm_base_url(settings, runtime_store)
    _, system_prompt_source, user_prompt_source = _resolved_prompt_config(runtime_store)
    speech_asr_resource_id, speech_asr_resource_id_source = resolve_speech_asr_resource_id(runtime_store)
    speech_asr_ws_url, speech_asr_ws_url_source = resolve_speech_asr_ws_url(runtime_store)
    speech_tts_ws_url, speech_tts_ws_url_source = resolve_speech_tts_ws_url(runtime_store)
    tts_voice_type, tts_voice_type_source = resolve_speech_tts_voice_type(runtime_store)
    return {
        "health": "ok",
        "app_name": settings.app_name,
        "default_user_id": settings.default_user_id,
        "memory_root": str(settings.memory_root),
        "llm_model": llm_model,
        "llm_model_source": llm_model_source,
        "llm_api_base_url": llm_base_url,
        "llm_api_base_url_source": llm_base_url_source,
        "llm_api_key_configured": "true" if llm_api_key else "false",
        "llm_api_key_masked": mask_secret(llm_api_key),
        "llm_api_key_source": llm_api_key_source,
        "llm_system_prompt_source": system_prompt_source,
        "llm_user_prompt_source": user_prompt_source,
        "speech_asr_resource_id": speech_asr_resource_id,
        "speech_asr_resource_id_source": speech_asr_resource_id_source,
        "speech_asr_ws_url": speech_asr_ws_url,
        "speech_asr_ws_url_source": speech_asr_ws_url_source,
        "speech_tts_ws_url": speech_tts_ws_url,
        "speech_tts_ws_url_source": speech_tts_ws_url_source,
        "tts_voice_type": tts_voice_type,
        "tts_voice_type_source": tts_voice_type_source,
    }


@router.get("/settings/llm")
async def get_llm_settings(
    reveal: bool = Query(default=False),
) -> dict[str, str | bool]:
    settings = get_settings()
    runtime_store = _runtime_config_store()
    api_key, source = resolve_llm_api_key(settings, runtime_store)
    model, model_source = resolve_llm_model(settings, runtime_store)
    base_url, base_url_source = resolve_llm_base_url(settings, runtime_store)
    prompt_config, system_prompt_source, user_prompt_source = _resolved_base_prompt_config(
        runtime_store
    )
    return {
        "configured": bool(api_key),
        "api_key_source": source,
        "api_key_masked": mask_secret(api_key),
        "api_key": api_key if reveal else "",
        "model": model,
        "model_source": model_source,
        "base_url": base_url,
        "base_url_source": base_url_source,
        "system_prompt": prompt_config.system_prompt,
        "system_prompt_source": system_prompt_source,
        "user_prompt_template": prompt_config.user_prompt_template,
        "user_prompt_source": user_prompt_source,
    }


@router.put("/settings/llm")
async def update_llm_settings(payload: LLMSettingsUpdate) -> dict[str, str | bool]:
    runtime_store = _runtime_config_store()
    runtime_store.save_llm_settings(
        api_key=payload.api_key,
        model=payload.model,
        base_url=payload.base_url,
        system_prompt=payload.system_prompt,
        user_prompt_template=payload.user_prompt_template,
    )

    settings = get_settings()
    api_key, source = resolve_llm_api_key(settings, runtime_store)
    model, model_source = resolve_llm_model(settings, runtime_store)
    base_url, base_url_source = resolve_llm_base_url(settings, runtime_store)
    prompt_config, system_prompt_source, user_prompt_source = _resolved_base_prompt_config(
        runtime_store
    )
    return {
        "configured": bool(api_key),
        "api_key_source": source,
        "api_key_masked": mask_secret(api_key),
        "api_key": "",
        "model": model,
        "model_source": model_source,
        "base_url": base_url,
        "base_url_source": base_url_source,
        "system_prompt": prompt_config.system_prompt,
        "system_prompt_source": system_prompt_source,
        "user_prompt_template": prompt_config.user_prompt_template,
        "user_prompt_source": user_prompt_source,
        "message": "LLM settings updated",
    }


@router.get("/settings/bot-profile")
async def get_bot_profile_settings() -> dict[str, str | bool]:
    return _resolved_bot_profile_settings(_runtime_config_store())


@router.put("/settings/bot-profile")
async def update_bot_profile_settings(
    payload: BotProfileSettingsUpdate,
) -> dict[str, str | bool]:
    runtime_store = _runtime_config_store()
    runtime_store.save_bot_profile_settings(
        enabled=payload.enabled,
        name=payload.name,
        persona=payload.persona,
        response_style=payload.response_style,
        custom_prompt=payload.custom_prompt,
    )
    return {
        **_resolved_bot_profile_settings(runtime_store),
        "message": "Bot profile settings updated",
    }


def _scene_payload(scene: SceneDefinition, store: RuntimeConfigStore) -> dict:
    override = store.load_scene_settings(scene.scene_id)
    return {
        "scene_id": scene.scene_id,
        "name": scene.name,
        "kind": scene.kind,
        "enabled": scene.enabled,
        "enter_keywords": list(scene.enter_keywords),
        "exit_keywords": list(scene.exit_keywords),
        "intent_hint": scene.intent_hint,
        "persona_prompt": scene.persona_prompt,
        "enter_prompt_text": scene.enter_prompt_text,
        "exit_prompt_text": scene.exit_prompt_text,
        "asr_hotwords": list(scene.asr_hotwords),
        "source": "runtime_override" if override else "default",
    }


@router.get("/scenes")
async def list_scenes() -> dict[str, list[dict]]:
    store = _runtime_config_store()
    return {"scenes": [_scene_payload(scene, store) for scene in effective_scenes(store)]}


@router.get("/scenes/{scene_id}")
async def get_scene(scene_id: str) -> dict:
    store = _runtime_config_store()
    scene = effective_scene(store, scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    return _scene_payload(scene, store)


@router.put("/scenes/{scene_id}")
async def update_scene(scene_id: str, payload: SceneSettingsUpdate) -> dict:
    store = _runtime_config_store()
    if scene_defaults.builtin_scene(scene_id) is None:
        raise HTTPException(status_code=404, detail="scene not found")
    store.save_scene_settings(
        scene_id,
        enabled=payload.enabled,
        enter_keywords=payload.enter_keywords,
        exit_keywords=payload.exit_keywords,
        persona_prompt=payload.persona_prompt,
        enter_prompt_text=payload.enter_prompt_text,
        exit_prompt_text=payload.exit_prompt_text,
        asr_hotwords=payload.asr_hotwords,
    )
    scene = effective_scene(store, scene_id)
    assert scene is not None
    return {**_scene_payload(scene, store), "message": "scene settings updated"}


@router.post("/scenes/{scene_id}/reset")
async def reset_scene(scene_id: str) -> dict:
    store = _runtime_config_store()
    if scene_defaults.builtin_scene(scene_id) is None:
        raise HTTPException(status_code=404, detail="scene not found")
    store.delete_scene_settings(scene_id)
    scene = effective_scene(store, scene_id)
    assert scene is not None
    return {**_scene_payload(scene, store), "message": "scene settings reset to default"}


@router.get("/settings/ui")
async def get_ui_settings() -> dict[str, str]:
    return _runtime_config_store().load_ui_settings()


@router.put("/settings/ui")
async def update_ui_settings(payload: UISettingsUpdate) -> dict[str, str]:
    runtime_store = _runtime_config_store()
    runtime_store.save_ui_settings(character=payload.character)
    return {**runtime_store.load_ui_settings(), "message": "UI settings updated"}


@router.get("/vocab/groups")
async def get_vocab_groups_settings() -> dict[str, object]:
    return _runtime_config_store().load_vocab_groups_settings()


@router.put("/vocab/groups")
async def update_vocab_groups_settings(
    payload: VocabGroupsSettingsUpdate,
) -> dict[str, object]:
    runtime_store = _runtime_config_store()
    runtime_store.save_vocab_groups_settings(
        active_group=payload.active_group,
        active_status_filter=payload.active_status_filter,
        groups={name: group.model_dump() for name, group in payload.groups.items()},
        prompt_template=payload.prompt_template,
    )
    return {
        **runtime_store.load_vocab_groups_settings(),
        "message": "Vocab groups updated",
    }


@router.get("/idiom/groups")
async def get_idiom_groups_settings() -> dict[str, object]:
    return _runtime_config_store().load_idiom_groups_settings()


@router.put("/idiom/groups")
async def update_idiom_groups_settings(
    payload: IdiomGroupsSettingsUpdate,
) -> dict[str, object]:
    runtime_store = _runtime_config_store()
    runtime_store.save_idiom_groups_settings(
        active_group=payload.active_group,
        active_status_filter=payload.active_status_filter,
        groups={name: group.model_dump() for name, group in payload.groups.items()},
        explanation_prompt_template=payload.explanation_prompt_template,
        story_prompt_template=payload.story_prompt_template,
    )
    return {
        **runtime_store.load_idiom_groups_settings(),
        "message": "Idiom groups updated",
    }


@router.get("/settings/speech")
async def get_speech_settings(
    reveal: bool = Query(default=False),
) -> dict[str, str | bool]:
    runtime_store = _runtime_config_store()
    return _resolved_speech_settings(runtime_store, reveal=reveal)


@router.put("/settings/speech")
async def update_speech_settings(payload: SpeechSettingsUpdate) -> dict[str, str | bool]:
    runtime_store = _runtime_config_store()
    runtime_store.save_speech_settings(
        mode=payload.mode,
        asr_api_key=payload.asr_api_key,
        asr_resource_id=payload.asr_resource_id,
        asr_ws_url=payload.asr_ws_url,
        asr_sample_rate_hz=payload.asr_sample_rate_hz,
        tts_api_key=payload.tts_api_key,
        tts_resource_id=payload.tts_resource_id,
        tts_ws_url=payload.tts_ws_url,
        tts_sample_rate_hz=payload.tts_sample_rate_hz,
        tts_encoding=payload.tts_encoding,
        tts_voice_type=payload.tts_voice_type,
    )

    return {
        **_resolved_speech_settings(runtime_store, reveal=False),
        "message": "Speech settings updated",
    }


@router.post("/chat")
async def chat_with_agent(payload: ChatRequest) -> dict:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty")

    started_at = time.perf_counter()
    token = set_current_scenario("chat")
    try:
        agent = _build_runtime_agent()
        result = await agent.ainvoke(text, user_id=payload.user_id)
    finally:
        reset_current_scenario(token)
    if result.reply_source in {"local-rule", "fallback-local"}:
        try:
            _token_usage_store().append_event(
                user_id=payload.user_id,
                scenario="chat",
                vendor="local",
                model=result.reply_source,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                success=True,
                request_kind="generate_reply",
                request_max_tokens=reply_max_tokens(text),
                latency_ms=max(
                    0, round((time.perf_counter() - started_at) * 1000)
                ),
                prompt_chars=len(text),
                completion_chars=len(result.response_text),
                extra={"safety_passed": result.safety_passed},
            )
        except Exception:
            # Metrics are best-effort; a full/readonly disk must not turn a
            # successful local fallback into an HTTP 500.
            pass
    return {
        "reply": result.response_text,
        "reply_source": result.reply_source,
        "fallback_reason": result.fallback_reason or "",
        "safety_passed": result.safety_passed,
        "prompt_debug": result.prompt_debug or {},
    }


@router.post("/chat/stream")
async def stream_chat_with_agent(payload: ChatRequest) -> StreamingResponse:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty")

    async def events():
        started_at = time.perf_counter()
        token = set_current_scenario("chat_stream")
        try:
            agent = _build_runtime_agent()
            result = await agent.ainvoke(text, user_id=payload.user_id)
        finally:
            reset_current_scenario(token)
        if result.reply_source in {"local-rule", "fallback-local"}:
            try:
                _token_usage_store().append_event(
                    user_id=payload.user_id,
                    scenario="chat_stream",
                    vendor="local",
                    model=result.reply_source,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    success=True,
                    request_kind="generate_reply",
                    request_max_tokens=reply_max_tokens(text),
                    latency_ms=max(
                        0, round((time.perf_counter() - started_at) * 1000)
                    ),
                    prompt_chars=len(text),
                    completion_chars=len(result.response_text),
                    extra={"safety_passed": result.safety_passed},
                )
            except Exception:
                pass
        prompt_debug = result.prompt_debug or {}
        try:
            _audit_store().append_event(
                payload.user_id,
                event_type="llm_prompt",
                payload=prompt_debug,
                turn_id="",
            )
        except Exception:
            pass
        yield json.dumps(
            {
                "type": "prompt",
                "prompt_debug": prompt_debug,
            },
            ensure_ascii=False,
        ) + "\n"
        yield json.dumps(
            {
                "type": "start",
                "reply_source": result.reply_source,
                "fallback_reason": result.fallback_reason or "",
                "safety_passed": result.safety_passed,
            },
            ensure_ascii=False,
        ) + "\n"
        for chunk in stream_text_chunks(result.response_text):
            yield json.dumps(
                {"type": "delta", "text": chunk},
                ensure_ascii=False,
            ) + "\n"
        yield json.dumps(
            {
                "type": "end",
                "reply": result.response_text,
                "reply_source": result.reply_source,
                "fallback_reason": result.fallback_reason or "",
                "safety_passed": result.safety_passed,
            },
            ensure_ascii=False,
        ) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@router.get("/metrics/token-usage")
async def get_token_usage_metrics(
    group_by: str = Query(default="", max_length=128),
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    fields = [item.strip() for item in group_by.split(",") if item.strip()]
    return _token_usage_store().aggregate(group_by=fields, days=days)


@router.get("/metrics/token-usage/daily")
async def get_token_usage_daily(
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    return _token_usage_store().daily_breakdown(days=days)


@router.get("/metrics/interactions")
async def get_interaction_metrics(
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    return InteractionMetrics(_memory_root()).aggregate(days=days)


@router.get("/memory/users")
async def list_memory_users() -> dict[str, list[str]]:
    root = _memory_root()
    profiles_dir = root / "profiles"
    memories_dir = root / "memories"
    conversations_dir = root / "conversations"
    summaries_dir = root / "summaries"
    audit_dir = root / "audit"
    vocabulary_dir = root / "vocabulary"
    users: set[str] = set()

    if profiles_dir.exists():
        users.update(path.stem for path in profiles_dir.glob("*.json"))
    for directory in [
        memories_dir,
        conversations_dir,
        summaries_dir,
        audit_dir,
        vocabulary_dir,
    ]:
        if directory.exists():
            users.update(path.name for path in directory.iterdir() if path.is_dir())

    return {"users": sorted(users)}


@router.get("/memory/users/{user_id}/profile")
async def get_profile(user_id: str) -> dict:
    root = _memory_root()
    safe_user_id = _safe_user_id(user_id)
    path = root / "profiles" / f"{safe_user_id}.json"
    try:
        data = _load_json(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="profile not found") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="invalid profile payload")
    return data


@router.patch("/memory/users/{user_id}/profile")
async def patch_profile(user_id: str, payload: dict) -> dict:
    return {"profile": _file_memory_store().patch_profile(user_id, payload)}


@router.get("/memory/users/{user_id}/index")
async def get_memory_index(user_id: str) -> dict[str, list[dict]]:
    root = _memory_root()
    safe_user_id = _safe_user_id(user_id)
    path = root / "memories" / safe_user_id / "index.json"
    if not path.exists():
        return {"entries": []}
    data = _load_json(path)
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="invalid memory index payload")
    return {"entries": data}


@router.get("/memory/users/{user_id}/entries/{memory_id}")
async def get_memory_entry(user_id: str, memory_id: str) -> dict[str, str]:
    root = _memory_root()
    safe_user_id = _safe_user_id(user_id)
    index_path = root / "memories" / safe_user_id / "index.json"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="memory entry not found")

    data = _load_json(index_path)
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="invalid memory index payload")

    match = next((entry for entry in data if entry.get("id") == memory_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="memory entry not found")

    path_value = match.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        raise HTTPException(status_code=500, detail="invalid memory entry path")

    root_resolved = root.resolve()
    user_memory_root = (root / "memories" / safe_user_id).resolve()
    entry_path = (root / path_value).resolve()
    if not entry_path.is_relative_to(root_resolved):
        raise HTTPException(status_code=500, detail="invalid memory entry path")
    if not entry_path.is_relative_to(user_memory_root):
        raise HTTPException(status_code=500, detail="invalid memory entry path")
    if entry_path.suffix.lower() != ".md":
        raise HTTPException(status_code=500, detail="invalid memory entry path")
    if not entry_path.exists() or not entry_path.is_file():
        raise HTTPException(status_code=404, detail="memory entry content not found")

    return {
        "id": memory_id,
        "title": str(match.get("title", "")),
        "content": entry_path.read_text(encoding="utf-8"),
    }


@router.patch("/memory/users/{user_id}/entries/{memory_id}")
async def patch_memory_entry(
    user_id: str,
    memory_id: str,
    payload: MemoryEntryPatch,
) -> dict:
    updated = _file_memory_store().update_memory(
        user_id,
        memory_id,
        payload.model_dump(exclude_none=True),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="memory entry not found")
    return {"entry": updated}


@router.delete("/memory/users/{user_id}/entries/{memory_id}")
async def delete_memory_entry(user_id: str, memory_id: str) -> dict[str, bool]:
    deleted = _file_memory_store().delete_memory(user_id, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory entry not found")
    return {"deleted": True}


@router.get("/memory/users/{user_id}/turns")
async def list_turns(
    user_id: str,
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, list[dict]]:
    turns = _conversation_store().list_recent_turns(user_id, limit=limit)
    return {"turns": turns}


@router.delete("/memory/users/{user_id}/turns")
async def delete_all_turns(user_id: str) -> dict[str, int]:
    deleted_count = _conversation_store().delete_all_turns(user_id)
    return {"deleted_count": deleted_count}


@router.patch("/memory/users/{user_id}/turns/{turn_id}")
async def patch_turn(user_id: str, turn_id: str, payload: TurnPatch) -> dict:
    patch = payload.model_dump(exclude_none=True)
    updated = _conversation_store().update_turn(user_id, turn_id, patch)
    if updated is None:
        raise HTTPException(status_code=404, detail="turn not found")
    return {"turn": updated}


@router.delete("/memory/users/{user_id}/turns/{turn_id}")
async def delete_turn(user_id: str, turn_id: str) -> dict[str, bool]:
    deleted = _conversation_store().delete_turn(user_id, turn_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="turn not found")
    return {"deleted": True}


@router.get("/memory/users/{user_id}/turns/{turn_id}/tts")
async def get_turn_tts_audio(user_id: str, turn_id: str) -> Response:
    store = _conversation_store()
    turn = store.get_turn(user_id, turn_id, include_audio_payload=True)
    if not turn:
        raise HTTPException(status_code=404, detail="turn not found")

    audio_format = turn.get("tts_audio_format")
    sample_rate_hz = turn.get("tts_sample_rate_hz")
    audio_bytes = b""
    if audio_format == "pcm_s16le" and type(sample_rate_hz) is int:
        # New records point at a SHA-256 addressed PCM sidecar. Historical
        # turn-id sidecars remain readable, and embedded Base64 is the final
        # compatibility fallback below.
        path_value = turn.get("tts_audio_path")
        if isinstance(path_value, str) and path_value:
            root = _memory_root().resolve()
            safe_user_id = _safe_user_id(user_id)
            expected_root = (root / "conversations" / safe_user_id / "tts").resolve()
            audio_path = (root / path_value).resolve()
            if (
                audio_path.is_relative_to(root)
                and audio_path.is_relative_to(expected_root)
                and audio_path.suffix.lower() == ".pcm"
                and audio_path.exists()
                and audio_path.is_file()
            ):
                candidate = audio_path.read_bytes()
                expected_digest = turn.get("tts_audio_sha256")
                if isinstance(expected_digest, str) and expected_digest:
                    is_valid_digest = (
                        len(expected_digest) == 64
                        and all(char in "0123456789abcdef" for char in expected_digest)
                        and audio_path.stem == expected_digest
                        and hashlib.sha256(candidate).hexdigest() == expected_digest
                    )
                    if is_valid_digest:
                        audio_bytes = candidate
                else:
                    # Pre-content-addressing sidecars had no checksum metadata.
                    audio_bytes = candidate

        if not audio_bytes:
            encoded_audio = turn.get("tts_audio_base64")
            if isinstance(encoded_audio, str) and encoded_audio:
                try:
                    audio_bytes = base64.b64decode(encoded_audio, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise HTTPException(
                        status_code=404, detail="turn tts audio not found"
                    ) from exc

        assistant_text = str(turn.get("assistant_text", ""))
        if assistant_text and audio_bytes == assistant_text.encode("utf-8"):
            audio_bytes = await _collect_local_tts_audio(
                assistant_text,
                sample_rate_hz=sample_rate_hz,
            )
            if audio_bytes:
                store.attach_tts_audio(
                    user_id,
                    turn_id,
                    audio_bytes=audio_bytes,
                    audio_format=audio_format,
                    sample_rate_hz=sample_rate_hz,
                    byte_length=len(audio_bytes),
                )

    if not audio_bytes:
        raise HTTPException(status_code=404, detail="turn tts audio not found")

    return Response(
        content=audio_bytes,
        media_type=f"audio/L16; rate={sample_rate_hz}; channels=1",
        headers={
            "X-TTS-Audio-Format": audio_format,
            "X-TTS-Sample-Rate-Hz": str(sample_rate_hz),
        },
    )


@router.get("/memory/users/{user_id}/summaries")
async def list_summaries(user_id: str) -> dict[str, list[dict]]:
    store = _summary_store()
    summaries = []
    for summary in store.list_summaries(user_id):
        summaries.append(
            {
                **summary,
                "content": store.load_summary_content(
                    user_id, str(summary.get("summary_id", ""))
                )
                or "",
            }
        )
    return {"summaries": summaries}


@router.get("/memory/users/{user_id}/vocabulary")
async def list_vocabulary(user_id: str) -> dict[str, list[dict]]:
    return {"vocabulary": _vocabulary_store().list_vocabulary(user_id)}


@router.get("/memory/users/{user_id}/vocabulary/history")
async def list_vocabulary_history(
    user_id: str,
    limit: int = Query(default=100, ge=0, le=500),
) -> dict[str, list[dict]]:
    return {"history": _vocabulary_store().list_history(user_id, limit=limit)}


@router.patch("/memory/users/{user_id}/summaries/{summary_id}")
async def patch_summary(
    user_id: str,
    summary_id: str,
    payload: SummaryPatch,
) -> dict:
    updated = _summary_store().update_summary(
        user_id,
        summary_id,
        content=payload.content,
        summary_source=payload.summary_source,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="summary not found")
    return {"summary": updated}


@router.delete("/memory/users/{user_id}/summaries/{summary_id}")
async def delete_summary(user_id: str, summary_id: str) -> dict[str, bool]:
    deleted = _summary_store().delete_summary(user_id, summary_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="summary not found")
    return {"deleted": True}


@router.get("/memory/users/{user_id}/audit")
async def list_audit_events(
    user_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, list[dict]]:
    return {"events": _audit_store().list_events(user_id, limit=limit)}


@router.patch("/memory/users/{user_id}/audit/{event_id}")
async def patch_audit_event(
    user_id: str,
    event_id: str,
    payload: AuditEventPatch,
) -> dict:
    updated = _audit_store().update_event(
        user_id,
        event_id,
        payload.model_dump(exclude_none=True),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="audit event not found")
    return {"event": updated}


@router.delete("/memory/users/{user_id}/audit/{event_id}")
async def delete_audit_event(user_id: str, event_id: str) -> dict[str, bool]:
    deleted = _audit_store().delete_event(user_id, event_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="audit event not found")
    return {"deleted": True}
