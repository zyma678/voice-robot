"""意图识别的纯函数（关键词层 + LLM 兜底的判定信号）。

与 `agent/routing.py` 同风格：无 I/O、确定性、便于单测/给 golden 向量。
LLM 兜底本身是 I/O，由调用方（server/admin）注入回调，本模块只决定
「是否值得调 LLM」。
"""

from voice_robot.scenes.scene import SceneDefinition

# 「弱学习信号」粗筛词：只有出现这些词、且关键词未命中时，才值得调一次 LLM
# 兜底分类。绝大多数普通对话不含这些词 → 完全不触发 LLM（成本护栏，ADR 0008）。
LEARNING_SIGNAL_TERMS: tuple[str, ...] = (
    "英语",
    "英文",
    "english",
    "口语",
    "speak",
    "外语",
    "单词",
    "字母",
)


def match_enter_keyword(
    text: str, scenes: tuple[SceneDefinition, ...]
) -> str | None:
    """关键词层进入判定：返回首个命中场景的 scene_id（仅 enabled 场景）。"""
    normalized = (text or "").lower()
    if not normalized:
        return None
    for scene in scenes:
        if not scene.enabled:
            continue
        for keyword in scene.enter_keywords:
            keyword = keyword.strip().lower()
            if keyword and keyword in normalized:
                return scene.scene_id
    return None


def match_exit_keyword(text: str, scene: SceneDefinition) -> bool:
    """关键词层退出判定：当前场景的 exit_keywords 任一命中即退出。"""
    normalized = (text or "").lower()
    if not normalized:
        return False
    return any(
        keyword.strip().lower() in normalized
        for keyword in scene.exit_keywords
        if keyword.strip()
    )


def has_learning_signal(text: str) -> bool:
    normalized = (text or "").lower()
    return any(term in normalized for term in LEARNING_SIGNAL_TERMS)
