import anyio

from voice_robot import admin_api
from voice_robot.admin_api import _parse_scene_classification, classify_scene_intent
from voice_robot.scenes import defaults


VALID = ["english_tutor", "math_tutor"]


def test_parse_exact_id() -> None:
    assert _parse_scene_classification("english_tutor", VALID) == "english_tutor"


def test_parse_strips_quotes_and_punctuation() -> None:
    assert _parse_scene_classification('"english_tutor".', VALID) == "english_tutor"
    assert _parse_scene_classification("`english_tutor`", VALID) == "english_tutor"


def test_parse_label_prefix() -> None:
    assert _parse_scene_classification("scene: english_tutor", VALID) == "english_tutor"


def test_parse_none() -> None:
    assert _parse_scene_classification("none", VALID) is None
    assert _parse_scene_classification("scene: none", VALID) is None
    assert _parse_scene_classification("", VALID) is None


def test_parse_unknown_returns_none() -> None:
    assert _parse_scene_classification("i think they want to play", VALID) is None


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def generate_freeform(
        self,
        *,
        system_prompt,
        user_prompt,
        max_tokens=16,
        temperature=0.0,
        **kwargs,
    ):
        self.calls += 1
        return self.reply


def test_classify_scene_intent_uses_llm(monkeypatch) -> None:
    fake = _FakeLLM("english_tutor")
    monkeypatch.setattr(admin_api, "_build_runtime_llm_service", lambda: fake)
    result = anyio.run(
        lambda: classify_scene_intent("能用英文陪我玩吗", defaults.BUILTIN_SCENES)
    )
    assert result == "english_tutor"
    assert fake.calls == 1


def test_classify_scene_intent_swallows_llm_errors(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("no api key")

    monkeypatch.setattr(admin_api, "_build_runtime_llm_service", _boom)
    result = anyio.run(
        lambda: classify_scene_intent("随便说点啥", defaults.BUILTIN_SCENES)
    )
    assert result is None
