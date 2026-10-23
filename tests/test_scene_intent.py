import anyio

from voice_robot.scenes import defaults, intent
from voice_robot.scenes.controller import decide_scene_action
from voice_robot.scenes.scene import SceneDefinition


SCENES = defaults.BUILTIN_SCENES
ENGLISH = defaults.ENGLISH_TUTOR


def test_match_enter_keyword_hits_english() -> None:
    assert intent.match_enter_keyword("我想学英语", SCENES) == "english_tutor"
    assert intent.match_enter_keyword("教我说英语吧", SCENES) == "english_tutor"
    assert intent.match_enter_keyword("Let's speak English", SCENES) == "english_tutor"


def test_match_enter_keyword_misses_plain_chat() -> None:
    assert intent.match_enter_keyword("今天天气怎么样", SCENES) is None
    assert intent.match_enter_keyword("", SCENES) is None


def test_match_enter_keyword_skips_disabled_scene() -> None:
    disabled = (
        SceneDefinition(
            **{**ENGLISH.__dict__, "enabled": False},
        ),
    )
    assert intent.match_enter_keyword("学英语", disabled) is None


def test_match_exit_keyword() -> None:
    assert intent.match_exit_keyword("退出英语", ENGLISH) is True
    assert intent.match_exit_keyword("我们换回中文吧", ENGLISH) is True
    assert intent.match_exit_keyword("继续聊", ENGLISH) is False


def test_has_learning_signal_coarse_filter() -> None:
    assert intent.has_learning_signal("帮我学单词") is True
    assert intent.has_learning_signal("english time") is True
    # No coarse signal -> never worth a LLM fallback call.
    assert intent.has_learning_signal("讲个恐龙故事") is False


def _decide(text, current, llm=None):
    return anyio.run(
        lambda: decide_scene_action(
            text=text, current_scene=current, scenes=SCENES, llm_classify=llm
        )
    )


def test_decide_enter_via_keyword() -> None:
    decision = _decide("我想学英语", None)
    assert decision.action == "enter"
    assert decision.scene_id == "english_tutor"


def test_decide_exit_when_in_scene() -> None:
    decision = _decide("退出英语", "english_tutor")
    assert decision.action == "exit"
    assert decision.scene_id == "english_tutor"


def test_decide_converse_in_scene_keeps_scene() -> None:
    decision = _decide("How are you", "english_tutor")
    assert decision.action == "converse"
    assert decision.scene_id == "english_tutor"


def test_decide_converse_when_no_match() -> None:
    decision = _decide("今天几点了", None)
    assert decision.action == "converse"
    assert decision.scene_id is None


def test_common_english_intent_uses_keyword_without_llm() -> None:
    calls: list[str] = []

    async def classifier(text, scenes):
        calls.append(text)
        return "english_tutor"

    # Common "用英文" phrasing stays on the deterministic zero-latency path.
    decision = _decide("能不能用英文跟我玩个游戏", None, llm=classifier)
    assert decision.action == "enter"
    assert decision.scene_id == "english_tutor"
    assert calls == []


def test_llm_fallback_only_when_coarse_signal_and_no_keyword() -> None:
    calls: list[str] = []

    async def classifier(text, scenes):
        calls.append(text)
        return "english_tutor"

    decision = _decide("陪我玩一个英语单词游戏", None, llm=classifier)
    assert decision.action == "enter"
    assert decision.scene_id == "english_tutor"
    assert calls == ["陪我玩一个英语单词游戏"]


def test_llm_fallback_skipped_without_coarse_signal() -> None:
    calls: list[str] = []

    async def classifier(text, scenes):
        calls.append(text)
        return "english_tutor"

    decision = _decide("讲个恐龙故事", None, llm=classifier)
    assert decision.action == "converse"
    assert decision.scene_id is None
    assert calls == []  # zero-cost path: LLM never invoked


def test_llm_fallback_none_keeps_general_chat() -> None:
    async def classifier(text, scenes):
        return None

    decision = _decide("我对英语没兴趣", None, llm=classifier)
    assert decision.action == "converse"
    assert decision.scene_id is None
