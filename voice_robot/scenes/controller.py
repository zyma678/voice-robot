"""单连接场景状态决策：根据当前场景 + 本轮文本，给出 进入/退出/对话 决策。

`decide_scene_action` 是 async（因为 LLM 兜底是 I/O），但 LLM 分类器以回调注入，
不传则只走关键词层——便于单测与零成本默认路径。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from voice_robot.scenes import intent
from voice_robot.scenes.scene import SceneDefinition

# (text, enabled_scenes) -> scene_id | None
SceneLLMClassifier = Callable[
    [str, tuple[SceneDefinition, ...]], Awaitable[str | None]
]


@dataclass(frozen=True)
class SceneDecision:
    action: str  # "enter" | "exit" | "converse"
    scene_id: str | None


async def decide_scene_action(
    *,
    text: str,
    current_scene: str | None,
    scenes: tuple[SceneDefinition, ...],
    llm_classify: SceneLLMClassifier | None = None,
) -> SceneDecision:
    cleaned = (text or "").strip()
    scene_map = {scene.scene_id: scene for scene in scenes}

    # 已在某个（仍生效的）场景中：只判定退出，其余正常对话（用场景人设）。
    active = scene_map.get(current_scene or "")
    if active is not None and active.enabled:
        if cleaned and intent.match_exit_keyword(cleaned, active):
            return SceneDecision(action="exit", scene_id=active.scene_id)
        return SceneDecision(action="converse", scene_id=active.scene_id)

    # 不在场景中（或当前场景已被禁用/删除）：跑进入判定。
    if not cleaned:
        return SceneDecision(action="converse", scene_id=None)

    hit = intent.match_enter_keyword(cleaned, scenes)
    if hit is not None:
        return SceneDecision(action="enter", scene_id=hit)

    if llm_classify is not None and intent.has_learning_signal(cleaned):
        enabled = tuple(scene for scene in scenes if scene.enabled)
        if enabled:
            classified = await llm_classify(cleaned, enabled)
            scene = scene_map.get((classified or "").strip())
            if scene is not None and scene.enabled:
                return SceneDecision(action="enter", scene_id=scene.scene_id)

    return SceneDecision(action="converse", scene_id=None)
