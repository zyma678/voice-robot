import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from voice_robot.config import Settings, get_settings


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


class RuntimeConfigStore:
    _VOCAB_DEFAULT_GROUP_NAME = "一年级（下）生字"
    _VOCAB_STATUS_LEARNED = "learned"
    _VOCAB_STATUS_UNLEARNED = "unlearned"
    _VOCAB_STATUS_NEW = "new"
    _VOCAB_STATUS_FILTERS = ("all", _VOCAB_STATUS_LEARNED, _VOCAB_STATUS_UNLEARNED, _VOCAB_STATUS_NEW)
    _IDIOM_DEFAULT_GROUP_NAME = "成语 · 第一批"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("runtime config payload must be a JSON object")
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        temp_path = self.path.with_name(
            f".{self.path.name}.{uuid4().hex}.tmp"
        )
        fd = -1
        try:
            fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as file:
                fd = -1
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def load_llm_settings(self) -> dict[str, str]:
        data = self.load()
        llm_settings = data.get("llm")
        if llm_settings is None:
            return {}
        if not isinstance(llm_settings, dict):
            raise ValueError("runtime llm settings payload must be a JSON object")
        normalized: dict[str, str] = {}
        for key in (
            "api_key",
            "model",
            "base_url",
            "system_prompt",
            "user_prompt_template",
        ):
            value = llm_settings.get(key, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ValueError(f"runtime llm {key} must be a string")
            normalized[key] = value
        return normalized

    def save_llm_settings(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        system_prompt: str,
        user_prompt_template: str,
    ) -> None:
        data = self.load()
        llm_settings = data.get("llm")
        if not isinstance(llm_settings, dict):
            llm_settings = {}

        normalized_api_key = api_key.strip()
        normalized_model = model.strip()
        normalized_base_url = base_url.strip().rstrip("/")
        normalized_system_prompt = system_prompt.strip()
        normalized_user_prompt_template = user_prompt_template.strip()

        if normalized_api_key:
            llm_settings["api_key"] = normalized_api_key
        else:
            llm_settings.pop("api_key", None)

        if normalized_model:
            llm_settings["model"] = normalized_model
        else:
            llm_settings.pop("model", None)

        if normalized_base_url:
            llm_settings["base_url"] = normalized_base_url
        else:
            llm_settings.pop("base_url", None)

        if normalized_system_prompt:
            llm_settings["system_prompt"] = normalized_system_prompt
        else:
            llm_settings.pop("system_prompt", None)

        if normalized_user_prompt_template:
            llm_settings["user_prompt_template"] = normalized_user_prompt_template
        else:
            llm_settings.pop("user_prompt_template", None)

        if llm_settings:
            data["llm"] = llm_settings
        else:
            data.pop("llm", None)

        self.save(data)

    def load_bot_profile_settings(self) -> dict[str, str | bool]:
        data = self.load()
        bot_profile = data.get("bot_profile")
        if bot_profile is None:
            return {
                "enabled": False,
                "name": "",
                "persona": "",
                "response_style": "",
                "custom_prompt": "",
            }
        if not isinstance(bot_profile, dict):
            raise ValueError("runtime bot profile payload must be a JSON object")
        enabled = bot_profile.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("runtime bot profile enabled must be a bool")
        normalized: dict[str, str | bool] = {"enabled": enabled}
        for key in ("name", "persona", "response_style", "custom_prompt"):
            value = bot_profile.get(key, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ValueError(f"runtime bot profile {key} must be a string")
            normalized[key] = value
        return normalized

    def save_bot_profile_settings(
        self,
        *,
        enabled: bool,
        name: str,
        persona: str,
        response_style: str,
        custom_prompt: str,
    ) -> None:
        data = self.load()
        bot_profile = {
            "enabled": bool(enabled),
            "name": name.strip(),
            "persona": persona.strip(),
            "response_style": response_style.strip(),
            "custom_prompt": custom_prompt.strip(),
        }
        has_content = any(
            str(bot_profile[key]).strip()
            for key in ("name", "persona", "response_style", "custom_prompt")
        )
        if bool(bot_profile["enabled"]) or has_content:
            data["bot_profile"] = bot_profile
        else:
            data.pop("bot_profile", None)
        self.save(data)

    # UI character shown on the device screen (and the web simulator). Backend
    # setting so it applies across clients / the real device. Default princess.
    _UI_CHARACTERS = ("princess", "cat")

    def load_ui_settings(self) -> dict[str, str]:
        data = self.load()
        ui = data.get("ui")
        if ui is None:
            return {"character": "princess"}
        if not isinstance(ui, dict):
            raise ValueError("runtime ui settings payload must be a JSON object")
        character = ui.get("character", "princess")
        if character not in self._UI_CHARACTERS:
            character = "princess"
        return {"character": character}

    def save_ui_settings(self, *, character: str) -> None:
        if character not in self._UI_CHARACTERS:
            raise ValueError(f"unsupported ui character: {character}")
        data = self.load()
        data["ui"] = {"character": character}
        self.save(data)

    def _normalize_vocab_status(self, value: Any) -> str:
        if value == self._VOCAB_STATUS_LEARNED:
            return self._VOCAB_STATUS_LEARNED
        if value == self._VOCAB_STATUS_NEW:
            return self._VOCAB_STATUS_NEW
        return self._VOCAB_STATUS_UNLEARNED

    def _unique_vocab_characters(self, value: Any) -> list[str]:
        source = value if isinstance(value, list) else []
        seen: set[str] = set()
        result: list[str] = []
        for item in source:
            character = str(item or "").strip()
            if character and character not in seen:
                seen.add(character)
                result.append(character)
        return result

    @staticmethod
    def _normalize_vocab_sections(sections: Any, char_set: set[str]) -> list[dict[str, Any]]:
        if not isinstance(sections, list):
            return []
        result: list[dict[str, Any]] = []
        for s in sections:
            if not isinstance(s, dict):
                continue
            title = str(s.get("title") or "").strip()
            if not title:
                continue
            chars = [ch for ch in (s.get("characters") or []) if isinstance(ch, str) and ch in char_set]
            result.append({"title": title, "characters": chars})
        return result

    def _normalize_vocab_groups(self, groups: Any) -> dict[str, dict[str, Any]]:
        source = groups if isinstance(groups, dict) else {}
        normalized: dict[str, dict[str, Any]] = {}
        for raw_name, raw_group in source.items():
            name = str(raw_name or "").strip()
            if not name:
                continue
            if isinstance(raw_group, list):
                characters = self._unique_vocab_characters(raw_group)
                statuses_source = {}
                sections_source = []
            elif isinstance(raw_group, dict):
                characters = self._unique_vocab_characters(raw_group.get("characters"))
                statuses_source = raw_group.get("statuses")
                sections_source = raw_group.get("sections")
            else:
                characters = []
                statuses_source = {}
                sections_source = []
            statuses_raw = statuses_source if isinstance(statuses_source, dict) else {}
            char_set = set(characters)
            sections = self._normalize_vocab_sections(sections_source, char_set)
            normalized[name] = {
                "characters": characters,
                "statuses": {
                    character: self._normalize_vocab_status(statuses_raw.get(character))
                    for character in characters
                },
                "sections": sections,
            }
        if self._VOCAB_DEFAULT_GROUP_NAME not in normalized:
            normalized[self._VOCAB_DEFAULT_GROUP_NAME] = {
                "characters": [],
                "statuses": {},
                "sections": [],
            }
        return normalized

    def load_vocab_groups_settings(self) -> dict[str, Any]:
        data = self.load()
        vocab = data.get("vocab_groups")
        if vocab is None:
            vocab = {}
        if not isinstance(vocab, dict):
            raise ValueError("runtime vocab groups payload must be a JSON object")
        groups = self._normalize_vocab_groups(vocab.get("groups"))
        active_group = str(vocab.get("active_group") or "").strip()
        if active_group not in groups:
            active_group = self._VOCAB_DEFAULT_GROUP_NAME
        active_status_filter = str(vocab.get("active_status_filter") or "all").strip()
        if active_status_filter not in self._VOCAB_STATUS_FILTERS:
            active_status_filter = "all"
        prompt_template = str(vocab.get("prompt_template") or "").strip()
        return {
            "active_group": active_group,
            "active_status_filter": active_status_filter,
            "groups": groups,
            "prompt_template": prompt_template,
        }

    def save_vocab_groups_settings(
        self,
        *,
        active_group: str,
        active_status_filter: str,
        groups: dict[str, Any],
        prompt_template: str = "",
    ) -> None:
        if active_status_filter not in self._VOCAB_STATUS_FILTERS:
            raise ValueError(f"unsupported vocab status filter: {active_status_filter}")
        normalized_groups = self._normalize_vocab_groups(groups)
        normalized_active_group = active_group.strip()
        if normalized_active_group not in normalized_groups:
            normalized_active_group = self._VOCAB_DEFAULT_GROUP_NAME
        data = self.load()
        data["vocab_groups"] = {
            "active_group": normalized_active_group,
            "active_status_filter": active_status_filter,
            "groups": normalized_groups,
            "prompt_template": prompt_template.strip(),
        }
        self.save(data)

    def _unique_idioms(self, value: Any) -> list[str]:
        source = value if isinstance(value, list) else []
        seen: set[str] = set()
        result: list[str] = []
        for item in source:
            idiom = str(item or "").strip()
            if idiom and idiom not in seen:
                seen.add(idiom)
                result.append(idiom)
        return result

    @staticmethod
    def _normalize_idiom_sections(sections: Any, idiom_set: set[str]) -> list[dict[str, Any]]:
        if not isinstance(sections, list):
            return []
        result: list[dict[str, Any]] = []
        for s in sections:
            if not isinstance(s, dict):
                continue
            title = str(s.get("title") or "").strip()
            if not title:
                continue
            idioms = [w for w in (s.get("idioms") or []) if isinstance(w, str) and w in idiom_set]
            result.append({"title": title, "idioms": idioms})
        return result

    def _normalize_idiom_groups(self, groups: Any) -> dict[str, dict[str, Any]]:
        source = groups if isinstance(groups, dict) else {}
        normalized: dict[str, dict[str, Any]] = {}
        for raw_name, raw_group in source.items():
            name = str(raw_name or "").strip()
            if not name:
                continue
            if isinstance(raw_group, list):
                idioms = self._unique_idioms(raw_group)
                statuses_source = {}
                sections_source = []
            elif isinstance(raw_group, dict):
                idioms = self._unique_idioms(raw_group.get("idioms"))
                statuses_source = raw_group.get("statuses")
                sections_source = raw_group.get("sections")
            else:
                idioms = []
                statuses_source = {}
                sections_source = []
            statuses_raw = statuses_source if isinstance(statuses_source, dict) else {}
            idiom_set = set(idioms)
            sections = self._normalize_idiom_sections(sections_source, idiom_set)
            normalized[name] = {
                "idioms": idioms,
                "statuses": {
                    idiom: self._normalize_vocab_status(statuses_raw.get(idiom))
                    for idiom in idioms
                },
                "sections": sections,
            }
        if self._IDIOM_DEFAULT_GROUP_NAME not in normalized:
            normalized[self._IDIOM_DEFAULT_GROUP_NAME] = {
                "idioms": [],
                "statuses": {},
                "sections": [],
            }
        return normalized

    def load_idiom_groups_settings(self) -> dict[str, Any]:
        data = self.load()
        idiom = data.get("idiom_groups")
        if idiom is None:
            idiom = {}
        if not isinstance(idiom, dict):
            raise ValueError("runtime idiom groups payload must be a JSON object")
        groups = self._normalize_idiom_groups(idiom.get("groups"))
        active_group = str(idiom.get("active_group") or "").strip()
        if active_group not in groups:
            active_group = self._IDIOM_DEFAULT_GROUP_NAME
        active_status_filter = str(idiom.get("active_status_filter") or "all").strip()
        if active_status_filter not in self._VOCAB_STATUS_FILTERS:
            active_status_filter = "all"
        return {
            "active_group": active_group,
            "active_status_filter": active_status_filter,
            "groups": groups,
            "explanation_prompt_template": str(idiom.get("explanation_prompt_template") or "").strip(),
            "story_prompt_template": str(idiom.get("story_prompt_template") or "").strip(),
        }

    def save_idiom_groups_settings(
        self,
        *,
        active_group: str,
        active_status_filter: str,
        groups: dict[str, Any],
        explanation_prompt_template: str = "",
        story_prompt_template: str = "",
    ) -> None:
        if active_status_filter not in self._VOCAB_STATUS_FILTERS:
            raise ValueError(f"unsupported idiom status filter: {active_status_filter}")
        normalized_groups = self._normalize_idiom_groups(groups)
        normalized_active_group = active_group.strip()
        if normalized_active_group not in normalized_groups:
            normalized_active_group = self._IDIOM_DEFAULT_GROUP_NAME
        data = self.load()
        data["idiom_groups"] = {
            "active_group": normalized_active_group,
            "active_status_filter": active_status_filter,
            "groups": normalized_groups,
            "explanation_prompt_template": explanation_prompt_template.strip(),
            "story_prompt_template": story_prompt_template.strip(),
        }
        self.save(data)

    def load_speech_settings(self) -> dict[str, str]:
        data = self.load()
        speech_settings = data.get("speech")
        if speech_settings is None:
            return {}
        if not isinstance(speech_settings, dict):
            raise ValueError("runtime speech settings payload must be a JSON object")
        normalized: dict[str, str] = {}
        for key in (
            "mode",
            "asr_api_key",
            "asr_resource_id",
            "asr_ws_url",
            "asr_sample_rate_hz",
            "tts_api_key",
            "tts_resource_id",
            "tts_ws_url",
            "tts_sample_rate_hz",
            "tts_encoding",
            "tts_voice_type",
        ):
            value = speech_settings.get(key, "")
            if value is None:
                value = ""
            if key in {"asr_sample_rate_hz", "tts_sample_rate_hz"}:
                if isinstance(value, int):
                    normalized[key] = str(value)
                elif isinstance(value, str):
                    normalized[key] = value.strip()
                else:
                    raise ValueError(f"runtime speech {key} must be an int or string")
            else:
                if not isinstance(value, str):
                    raise ValueError(f"runtime speech {key} must be a string")
                normalized[key] = value
        return normalized

    def save_speech_settings(
        self,
        *,
        mode: str = "",
        asr_api_key: str = "",
        asr_resource_id: str = "",
        asr_ws_url: str = "",
        asr_sample_rate_hz: int | str = "",
        tts_api_key: str = "",
        tts_resource_id: str = "",
        tts_ws_url: str = "",
        tts_sample_rate_hz: int | str = "",
        tts_encoding: str = "",
        tts_voice_type: str = "",
    ) -> None:
        data = self.load()
        speech_settings = data.get("speech")
        if not isinstance(speech_settings, dict):
            speech_settings = {}

        normalized_mode = (mode or "").strip()
        normalized_asr_api_key = asr_api_key.strip()
        normalized_asr_resource_id = asr_resource_id.strip()
        normalized_asr_ws_url = asr_ws_url.strip().rstrip("/")
        normalized_asr_sr = str(asr_sample_rate_hz).strip() if asr_sample_rate_hz is not None else ""
        normalized_tts_api_key = tts_api_key.strip()
        normalized_tts_resource_id = tts_resource_id.strip()
        normalized_tts_ws_url = tts_ws_url.strip().rstrip("/")
        normalized_tts_sr = str(tts_sample_rate_hz).strip() if tts_sample_rate_hz is not None else ""
        normalized_tts_encoding = (tts_encoding or "").strip()
        normalized_tts_voice = (tts_voice_type or "").strip()

        if normalized_mode:
            speech_settings["mode"] = normalized_mode
        else:
            speech_settings.pop("mode", None)

        if normalized_asr_api_key:
            speech_settings["asr_api_key"] = normalized_asr_api_key
        else:
            speech_settings.pop("asr_api_key", None)

        if normalized_asr_resource_id:
            speech_settings["asr_resource_id"] = normalized_asr_resource_id
        else:
            speech_settings.pop("asr_resource_id", None)

        if normalized_asr_ws_url:
            speech_settings["asr_ws_url"] = normalized_asr_ws_url
        else:
            speech_settings.pop("asr_ws_url", None)

        if normalized_asr_sr:
            speech_settings["asr_sample_rate_hz"] = normalized_asr_sr
        else:
            speech_settings.pop("asr_sample_rate_hz", None)

        if normalized_tts_api_key:
            speech_settings["tts_api_key"] = normalized_tts_api_key
        else:
            speech_settings.pop("tts_api_key", None)

        if normalized_tts_resource_id:
            speech_settings["tts_resource_id"] = normalized_tts_resource_id
        else:
            speech_settings.pop("tts_resource_id", None)

        if normalized_tts_ws_url:
            speech_settings["tts_ws_url"] = normalized_tts_ws_url
        else:
            speech_settings.pop("tts_ws_url", None)

        if normalized_tts_sr:
            speech_settings["tts_sample_rate_hz"] = normalized_tts_sr
        else:
            speech_settings.pop("tts_sample_rate_hz", None)

        if normalized_tts_encoding:
            speech_settings["tts_encoding"] = normalized_tts_encoding
        else:
            speech_settings.pop("tts_encoding", None)

        if normalized_tts_voice:
            speech_settings["tts_voice_type"] = normalized_tts_voice
        else:
            speech_settings.pop("tts_voice_type", None)

        if speech_settings:
            data["speech"] = speech_settings
        else:
            data.pop("speech", None)

        self.save(data)

    @staticmethod
    def _normalize_keyword_list(value: Any) -> list[str]:
        source = value if isinstance(value, list) else []
        seen: set[str] = set()
        result: list[str] = []
        for item in source:
            keyword = str(item or "").strip()
            if keyword and keyword not in seen:
                seen.add(keyword)
                result.append(keyword)
        return result

    def load_scene_settings(self, scene_id: str) -> dict[str, Any]:
        """运行态覆盖（部分字段即可），与出厂默认的合并由 scenes.registry 负责。
        只返回显式配置过的字段；未配置字段缺省由调用方回落默认。"""
        data = self.load()
        scenes = data.get("scenes")
        if not isinstance(scenes, dict):
            return {}
        entry = scenes.get(scene_id)
        if not isinstance(entry, dict):
            return {}
        normalized: dict[str, Any] = {}
        if isinstance(entry.get("enabled"), bool):
            normalized["enabled"] = entry["enabled"]
        for key in ("enter_keywords", "exit_keywords", "asr_hotwords"):
            if key in entry:
                normalized[key] = self._normalize_keyword_list(entry.get(key))
        for key in ("persona_prompt", "enter_prompt_text", "exit_prompt_text"):
            value = entry.get(key)
            if isinstance(value, str):
                normalized[key] = value
        return normalized

    def save_scene_settings(
        self,
        scene_id: str,
        *,
        enabled: bool,
        enter_keywords: list[str],
        exit_keywords: list[str],
        persona_prompt: str,
        enter_prompt_text: str,
        exit_prompt_text: str,
        asr_hotwords: list[str],
    ) -> None:
        data = self.load()
        scenes = data.get("scenes")
        if not isinstance(scenes, dict):
            scenes = {}
        scenes[scene_id] = {
            "enabled": bool(enabled),
            "enter_keywords": self._normalize_keyword_list(enter_keywords),
            "exit_keywords": self._normalize_keyword_list(exit_keywords),
            "persona_prompt": persona_prompt.strip(),
            "enter_prompt_text": enter_prompt_text.strip(),
            "exit_prompt_text": exit_prompt_text.strip(),
            "asr_hotwords": self._normalize_keyword_list(asr_hotwords),
        }
        data["scenes"] = scenes
        self.save(data)

    def delete_scene_settings(self, scene_id: str) -> None:
        """删除运行态覆盖，使该场景回落到出厂默认（「恢复默认」用）。"""
        data = self.load()
        scenes = data.get("scenes")
        if not isinstance(scenes, dict):
            return
        if scenes.pop(scene_id, None) is None:
            return
        if scenes:
            data["scenes"] = scenes
        else:
            data.pop("scenes", None)
        self.save(data)


def resolve_llm_api_key(
    settings: Settings, store: RuntimeConfigStore
) -> tuple[str, str]:
    runtime_api_key = store.load_llm_settings().get("api_key", "")
    if runtime_api_key:
        return runtime_api_key, "runtime_override"
    if settings.llm_api_key:
        return settings.llm_api_key, "env"
    return "", "unset"


def resolve_llm_model(
    settings: Settings, store: RuntimeConfigStore
) -> tuple[str, str]:
    runtime_model = store.load_llm_settings().get("model", "").strip()
    if runtime_model:
        return runtime_model, "runtime_override"
    return settings.llm_model, "env"


def resolve_llm_base_url(
    settings: Settings, store: RuntimeConfigStore
) -> tuple[str, str]:
    runtime_base_url = store.load_llm_settings().get("base_url", "").strip()
    if runtime_base_url:
        return runtime_base_url, "runtime_override"
    return settings.llm_api_base_url, "env"


def resolve_llm_system_prompt(
    store: RuntimeConfigStore,
    default_value: str,
) -> tuple[str, str]:
    runtime_value = store.load_llm_settings().get("system_prompt", "").strip()
    if runtime_value:
        return runtime_value, "runtime_override"
    return default_value, "default"


def resolve_llm_user_prompt_template(
    store: RuntimeConfigStore,
    default_value: str,
) -> tuple[str, str]:
    runtime_value = store.load_llm_settings().get("user_prompt_template", "").strip()
    if runtime_value:
        return runtime_value, "runtime_override"
    return default_value, "default"

def _resolve_speech_string(
    store: RuntimeConfigStore,
    key: str,
    default_value: str = "",
    settings: Settings | None = None,
    settings_key: str | None = None,
) -> tuple[str, str]:
    runtime_value = store.load_speech_settings().get(key, "").strip()
    if runtime_value:
        return runtime_value, "runtime_override"
    configured = settings or get_settings()
    env_value = str(getattr(configured, settings_key or key, "") or "").strip()
    if env_value:
        return env_value, "env"
    return default_value, "default"


def resolve_speech_asr_api_key(
    store: RuntimeConfigStore,
    settings: Settings | None = None,
) -> tuple[str, str]:
    """New console APP Key, sent as the X-Api-Key header."""
    return _resolve_speech_string(store, "asr_api_key", settings=settings)


def resolve_speech_asr_resource_id(
    store: RuntimeConfigStore,
    settings: Settings | None = None,
) -> tuple[str, str]:
    """Streaming ASR 2.0 resource id, sent as the X-Api-Resource-Id header."""
    return _resolve_speech_string(
        store,
        "asr_resource_id",
        "volc.bigasr.sauc.duration",
        settings,
    )


def resolve_speech_asr_ws_url(
    store: RuntimeConfigStore,
    settings: Settings | None = None,
) -> tuple[str, str]:
    """Volc bidirectional streaming ASR WebSocket endpoint."""
    return _resolve_speech_string(
        store,
        "asr_ws_url",
        "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
        settings,
    )


def resolve_speech_tts_api_key(
    store: RuntimeConfigStore,
    settings: Settings | None = None,
) -> tuple[str, str]:
    """New console APP Key, sent as the X-Api-Key header."""
    return _resolve_speech_string(store, "tts_api_key", settings=settings)


def resolve_speech_tts_resource_id(
    store: RuntimeConfigStore,
    settings: Settings | None = None,
) -> tuple[str, str]:
    """TTS large-model resource id, sent as the X-Api-Resource-Id header."""
    return _resolve_speech_string(store, "tts_resource_id", "seed-tts-1.0", settings)


def resolve_speech_tts_ws_url(
    store: RuntimeConfigStore,
    settings: Settings | None = None,
) -> tuple[str, str]:
    """Volc V3 unidirectional streaming TTS WebSocket endpoint."""
    return _resolve_speech_string(
        store,
        "tts_ws_url",
        "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream",
        settings,
    )


def resolve_speech_mode(
    store: RuntimeConfigStore,
    default_value: str = "split",
    settings: Settings | None = None,
) -> tuple[str, str]:
    return _resolve_speech_string(
        store,
        "mode",
        default_value,
        settings,
        "speech_mode",
    )


def resolve_speech_asr_sample_rate_hz(
    store: RuntimeConfigStore,
    default_value: int = 16000,
    settings: Settings | None = None,
) -> tuple[int, str]:
    raw, source = _resolve_speech_string(
        store,
        "asr_sample_rate_hz",
        settings=settings,
    )
    if raw:
        try:
            return int(raw), source
        except ValueError:
            return default_value, "default"
    return default_value, "default"


def resolve_speech_tts_sample_rate_hz(
    store: RuntimeConfigStore,
    default_value: int = 16000,
    settings: Settings | None = None,
) -> tuple[int, str]:
    raw, source = _resolve_speech_string(
        store,
        "tts_sample_rate_hz",
        settings=settings,
    )
    if raw:
        try:
            return int(raw), source
        except ValueError:
            return default_value, "default"
    return default_value, "default"


def resolve_speech_tts_encoding(
    store: RuntimeConfigStore,
    default_value: str = "pcm",
    settings: Settings | None = None,
) -> tuple[str, str]:
    return _resolve_speech_string(
        store,
        "tts_encoding",
        default_value,
        settings,
    )


def resolve_speech_tts_voice_type(
    store: RuntimeConfigStore,
    default_value: str = "",
    settings: Settings | None = None,
) -> tuple[str, str]:
    return _resolve_speech_string(
        store,
        "tts_voice_type",
        default_value,
        settings,
    )
