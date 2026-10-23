from fastapi.testclient import TestClient

from voice_robot import admin_api
from voice_robot.config import get_settings
from voice_robot.runtime_config import RuntimeConfigStore
from voice_robot.server import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()
    return TestClient(app)


def test_list_scenes_returns_default_english(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    data = client.get("/api/scenes").json()
    ids = {scene["scene_id"]: scene for scene in data["scenes"]}
    assert "english_tutor" in ids
    english = ids["english_tutor"]
    assert english["name"] == "英语学习"
    assert english["source"] == "default"
    assert english["enabled"] is True
    assert english["enter_prompt_text"]
    assert english["exit_prompt_text"]
    # 英语场景出厂带 ASR 英文热词（Web 可编辑），偏置场景内识别。
    assert isinstance(english["asr_hotwords"], list)
    assert "apple" in english["asr_hotwords"]


def test_list_scenes_includes_action_scenes_with_kind(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    data = client.get("/api/scenes").json()
    ids = {scene["scene_id"]: scene for scene in data["scenes"]}
    assert ids["english_tutor"]["kind"] == "persona"
    assert ids["hanzi_writing"]["kind"] == "action"
    assert ids["idiom_learning"]["kind"] == "action"
    # 动作场景出厂带可配触发词。
    assert ids["hanzi_writing"]["enter_keywords"]
    assert ids["idiom_learning"]["enter_keywords"]


def test_update_scene_persists_asr_hotwords(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    payload = {
        "enabled": True,
        "enter_keywords": ["学英语"],
        "exit_keywords": ["退出英语"],
        "persona_prompt": "Custom.",
        "enter_prompt_text": "in",
        "exit_prompt_text": "out",
        "asr_hotwords": ["banana", "banana", "cat"],
    }
    updated = client.put("/api/scenes/english_tutor", json=payload).json()
    assert updated["asr_hotwords"] == ["banana", "cat"]
    fetched = client.get("/api/scenes/english_tutor").json()
    assert fetched["asr_hotwords"] == ["banana", "cat"]
    # 恢复默认后回到出厂热词。
    reset = client.post("/api/scenes/english_tutor/reset").json()
    assert "apple" in reset["asr_hotwords"]


def test_update_and_reset_scene(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    payload = {
        "enabled": True,
        "enter_keywords": ["开始英语", "学英语"],
        "exit_keywords": ["退出英语"],
        "persona_prompt": "Custom tutor persona.",
        "enter_prompt_text": "进入啦",
        "exit_prompt_text": "退出啦",
    }
    updated = client.put("/api/scenes/english_tutor", json=payload).json()
    assert updated["source"] == "runtime_override"
    assert updated["persona_prompt"] == "Custom tutor persona."
    assert updated["enter_keywords"] == ["开始英语", "学英语"]
    assert updated["enter_prompt_text"] == "进入啦"

    fetched = client.get("/api/scenes/english_tutor").json()
    assert fetched["persona_prompt"] == "Custom tutor persona."
    assert fetched["source"] == "runtime_override"

    reset = client.post("/api/scenes/english_tutor/reset").json()
    assert reset["source"] == "default"
    assert reset["persona_prompt"] != "Custom tutor persona."


def test_unknown_scene_is_404(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/scenes/nope").status_code == 404
    assert client.put("/api/scenes/nope", json={"enabled": True}).status_code == 404
    assert client.post("/api/scenes/nope/reset").status_code == 404


def test_prompt_tts_returns_audio(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    # No Volc keys -> LocalTTSService placeholder tone, still valid PCM bytes.
    resp = client.get("/api/scenes/english_tutor/prompt-tts", params={"kind": "enter"})
    assert resp.status_code == 200
    assert resp.headers["X-TTS-Audio-Format"] == "pcm_s16le"
    assert len(resp.content) > 0
    assert client.get(
        "/api/scenes/english_tutor/prompt-tts", params={"kind": "bogus"}
    ).status_code == 400


def test_scene_persona_injected_into_prompt(monkeypatch, tmp_path) -> None:
    _client(monkeypatch, tmp_path)
    store = RuntimeConfigStore(get_settings().runtime_config_path)

    base, _, _ = admin_api._resolved_prompt_config(store, scene_id=None)
    with_scene, _, _ = admin_api._resolved_prompt_config(store, scene_id="english_tutor")

    assert "英语学习场景" not in base.system_prompt
    assert "英语学习场景" in with_scene.system_prompt
    assert "English tutor" in with_scene.system_prompt


def test_disabled_scene_persona_not_injected(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    client.put(
        "/api/scenes/english_tutor",
        json={
            "enabled": False,
            "enter_keywords": ["学英语"],
            "exit_keywords": ["退出英语"],
            "persona_prompt": "English tutor persona.",
            "enter_prompt_text": "in",
            "exit_prompt_text": "out",
        },
    )
    store = RuntimeConfigStore(get_settings().runtime_config_path)
    with_scene, _, _ = admin_api._resolved_prompt_config(store, scene_id="english_tutor")
    assert "英语学习场景" not in with_scene.system_prompt
