"""Proves the intent-scene framework is extensible: registering a second built-in
scene flows through registry / API / intent / persona injection with no other
code changes (ADR 0008 「可扩展」 requirement)."""

import anyio
from fastapi.testclient import TestClient

from voice_robot import admin_api
from voice_robot.config import get_settings
from voice_robot.runtime_config import RuntimeConfigStore
from voice_robot.scenes import defaults, registry
from voice_robot.scenes.controller import decide_scene_action
from voice_robot.scenes.scene import SceneDefinition
from voice_robot.server import app


MATH_TUTOR = SceneDefinition(
    scene_id="math_tutor",
    name="数学辅导",
    enabled=True,
    enter_keywords=("数学辅导", "辅导数学", "学数学"),
    exit_keywords=("退出数学", "不学数学了"),
    intent_hint="用户想进入数学辅导场景",
    persona_prompt="You are a patient kids' math tutor.",
    enter_prompt_text="数学辅导开始啦！",
    exit_prompt_text="数学辅导结束啦。",
)


def _two_scenes(monkeypatch):
    monkeypatch.setattr(
        defaults, "BUILTIN_SCENES", (defaults.ENGLISH_TUTOR, MATH_TUTOR)
    )


def test_registry_merges_second_scene(monkeypatch, tmp_path) -> None:
    _two_scenes(monkeypatch)
    store = RuntimeConfigStore(tmp_path / "console_settings.json")
    ids = {scene.scene_id for scene in registry.effective_scenes(store)}
    assert ids == {"english_tutor", "math_tutor"}


def test_api_lists_and_resets_second_scene(monkeypatch, tmp_path) -> None:
    _two_scenes(monkeypatch)
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()
    client = TestClient(app)

    listed = {s["scene_id"] for s in client.get("/api/scenes").json()["scenes"]}
    assert "math_tutor" in listed
    assert client.get("/api/scenes/math_tutor").json()["name"] == "数学辅导"
    assert client.post("/api/scenes/math_tutor/reset").status_code == 200


def test_intent_routes_to_second_scene(monkeypatch, tmp_path) -> None:
    _two_scenes(monkeypatch)
    scenes = registry.effective_scenes(
        RuntimeConfigStore(tmp_path / "console_settings.json")
    )
    decision = anyio.run(
        lambda: decide_scene_action(text="我要学数学", current_scene=None, scenes=scenes)
    )
    assert decision.action == "enter"
    assert decision.scene_id == "math_tutor"


def test_persona_injection_for_second_scene(monkeypatch, tmp_path) -> None:
    _two_scenes(monkeypatch)
    store = RuntimeConfigStore(tmp_path / "console_settings.json")
    config, _, _ = admin_api._resolved_prompt_config(store, scene_id="math_tutor")
    assert "数学辅导场景" in config.system_prompt
    assert "math tutor" in config.system_prompt
