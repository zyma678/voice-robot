"""场景注册表：内置出厂默认 + 运行态覆盖 → 生效场景集。

运行态覆盖由 `RuntimeConfigStore.load_scene_settings(scene_id)` 提供（部分字段
即可）；本模块把覆盖合并到出厂默认上。空字符串/缺失字段回落默认。
"""

from typing import Any, Protocol

from voice_robot.scenes import defaults
from voice_robot.scenes.scene import SceneDefinition


class _SceneOverrideStore(Protocol):
    def load_scene_settings(self, scene_id: str) -> dict[str, Any]: ...


def _merge(base: SceneDefinition, override: dict[str, Any]) -> SceneDefinition:
    def keywords(key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
        value = override.get(key)
        if isinstance(value, list):
            cleaned = tuple(
                str(item).strip() for item in value if str(item).strip()
            )
            return cleaned if cleaned else fallback
        return fallback

    def text(key: str, fallback: str) -> str:
        value = override.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback

    enabled = override.get("enabled")
    if not isinstance(enabled, bool):
        enabled = base.enabled

    return SceneDefinition(
        scene_id=base.scene_id,
        name=base.name,
        enabled=enabled,
        enter_keywords=keywords("enter_keywords", base.enter_keywords),
        exit_keywords=keywords("exit_keywords", base.exit_keywords),
        intent_hint=base.intent_hint,
        persona_prompt=text("persona_prompt", base.persona_prompt),
        enter_prompt_text=text("enter_prompt_text", base.enter_prompt_text),
        exit_prompt_text=text("exit_prompt_text", base.exit_prompt_text),
        asr_hotwords=keywords("asr_hotwords", base.asr_hotwords),
        kind=base.kind,
    )


def effective_scenes(store: _SceneOverrideStore) -> tuple[SceneDefinition, ...]:
    result: list[SceneDefinition] = []
    for base in defaults.BUILTIN_SCENES:
        try:
            override = store.load_scene_settings(base.scene_id)
        except Exception:
            override = {}
        if not isinstance(override, dict):
            override = {}
        result.append(_merge(base, override))
    return tuple(result)


def effective_scene(
    store: _SceneOverrideStore, scene_id: str
) -> SceneDefinition | None:
    for scene in effective_scenes(store):
        if scene.scene_id == scene_id:
            return scene
    return None
