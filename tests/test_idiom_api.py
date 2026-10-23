import json

import pytest
from fastapi.testclient import TestClient

from voice_robot import server
from voice_robot.config import get_settings
from voice_robot.server import app


@pytest.fixture(autouse=True)
def _isolate_speech_runtime(monkeypatch, tmp_path) -> None:
    """Keep API tests off the developer's configured cloud speech account."""
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()


class _FakeIdiomLLM:
    async def generate_freeform(
        self,
        *,
        system_prompt,
        user_prompt,
        max_tokens=640,
        timeout_seconds=None,
        **kwargs,
    ):
        return (
            '{"pinyin":"shǒu zhū dài tù","explanation":"比喻死守经验、不知变通。",'
            '"story":"从前有个农夫，看见一只兔子撞死在树桩上……"}'
        )


def test_idiom_content_rejects_non_idiom(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    assert client.post("/api/idiom/content", json={"idiom": ""}).status_code == 400
    assert client.post("/api/idiom/content", json={"idiom": "字"}).status_code == 400
    assert client.post("/api/idiom/content", json={"idiom": "ab12"}).status_code == 400
    assert client.post("/api/idiom/content", json={"idiom": "守株待兔abc"}).status_code == 400


def test_idiom_content_uses_llm_output(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    monkeypatch.setattr(server, "_build_runtime_llm_service", lambda: _FakeIdiomLLM())
    client = TestClient(app)

    response = client.post("/api/idiom/content", json={"idiom": "守株待兔"})
    assert response.status_code == 200
    data = response.json()
    assert data["idiom"] == "守株待兔"
    assert data["pinyin"] == "shǒu zhū dài tù"
    assert "不知变通" in data["explanation"]
    assert data["story"].startswith("从前")
    assert data["source"] == "ark"
    # Both narratable artifacts get cached TTS (LocalTTSService placeholder tone).
    assert data["has_explanation_tts"] is True
    assert data["has_story_tts"] is True
    assert data["explanation_tts_url"]
    assert data["story_tts_url"]


def test_idiom_content_falls_back_when_llm_unavailable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()

    def _boom():
        raise RuntimeError("no api key")

    monkeypatch.setattr(server, "_build_runtime_llm_service", _boom)
    client = TestClient(app)

    response = client.post("/api/idiom/content", json={"idiom": "画蛇添足"})
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "fallback"
    assert "画蛇添足" in data["explanation"]
    assert "detail" in data


def test_idiom_content_caches_and_serves_tts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    monkeypatch.setattr(server, "_build_runtime_llm_service", lambda: _FakeIdiomLLM())
    client = TestClient(app)

    gen = client.post("/api/idiom/content", json={"idiom": "守株待兔"})
    assert gen.status_code == 200
    data = gen.json()

    for url in (data["explanation_tts_url"], data["story_tts_url"]):
        audio_resp = client.get(url)
        assert audio_resp.status_code == 200
        assert audio_resp.headers["content-type"].startswith("audio/L16")
        assert audio_resp.headers["X-TTS-Audio-Format"] == "pcm_s16le"
        assert len(audio_resp.content) > 0

    # Survives as a listed record for a fresh session.
    list_resp = client.get("/api/idiom/contents")
    assert list_resp.status_code == 200
    record = next(i for i in list_resp.json()["idioms"] if i["idiom"] == "守株待兔")
    assert record["pinyin"] == "shǒu zhū dài tù"
    assert record["has_explanation_tts"] is True
    assert record["has_story_tts"] is True

    # A second non-regenerate call returns the cache without hitting the LLM.
    monkeypatch.setattr(server, "_build_runtime_llm_service", lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called")))
    cached = client.post("/api/idiom/content", json={"idiom": "守株待兔"})
    assert cached.status_code == 200
    assert cached.json()["cached"] is True


def test_idiom_tts_audio_validation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    assert client.get("/api/idiom/守株待兔/tts").status_code == 404
    assert client.get("/api/idiom/字/tts").status_code == 400
    assert client.get("/api/idiom/守株待兔/tts", params={"kind": "bogus"}).status_code == 400


def test_idiom_contents_list_empty_initially(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.get("/api/idiom/contents")
    assert response.status_code == 200
    assert response.json() == {"idioms": []}


def test_device_idiom_say_validates_inputs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()
    client = TestClient(app)

    assert (
        client.post("/api/devices/ghost/idiom-say", json={"idiom": "", "text": "x"}).status_code
        == 400
    )
    assert (
        client.post("/api/devices/ghost/idiom-say", json={"idiom": "守株待兔", "text": ""}).status_code
        == 400
    )
    # Valid inputs but device offline -> 409.
    assert (
        client.post(
            "/api/devices/ghost/idiom-say", json={"idiom": "守株待兔", "text": "比喻死守经验。"}
        ).status_code
        == 409
    )


def test_device_idiom_say_speaks_text_and_shows_idiom(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()
    client = TestClient(app)
    token = get_settings().auth_token

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "esp32-idiom", "token": token})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        response = client.post(
            "/api/devices/esp32-idiom/idiom-say",
            json={"idiom": "守株待兔", "pinyin": "shǒu zhū dài tù", "text": "比喻死守经验。"},
        )
        assert response.status_code == 200

        types = []
        reply_text = None
        show_idiom_event = None
        for _ in range(40):
            event = ws.receive_json()
            types.append(event.get("type"))
            if event.get("type") == "llm_reply_end":
                reply_text = event.get("text")
            if event.get("type") == "show_idiom":
                show_idiom_event = event
                break
        assert "show_idiom" in types
        assert reply_text == "比喻死守经验。"
        assert show_idiom_event["idiom"] == "守株待兔"
        assert show_idiom_event["pinyin"] == "shǒu zhū dài tù"
        assert len(show_idiom_event["glyphs"]) == 4


def test_idiom_groups_settings_roundtrip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    # Defaults: an empty default group exists.
    initial = client.get("/api/idiom/groups")
    assert initial.status_code == 200
    assert "成语 · 第一批" in initial.json()["groups"]

    payload = {
        "active_group": "三年级成语",
        "active_status_filter": "unlearned",
        "groups": {
            "三年级成语": {
                "idioms": ["守株待兔", "画蛇添足", "守株待兔"],
                "statuses": {"守株待兔": "learned"},
                "sections": [{"title": "寓言", "idioms": ["守株待兔"]}],
            }
        },
        "explanation_prompt_template": "先讲意思",
        "story_prompt_template": "讲个故事",
    }
    put = client.put("/api/idiom/groups", json=payload)
    assert put.status_code == 200

    got = client.get("/api/idiom/groups").json()
    assert got["active_group"] == "三年级成语"
    assert got["active_status_filter"] == "unlearned"
    # De-duplicated idioms.
    assert got["groups"]["三年级成语"]["idioms"] == ["守株待兔", "画蛇添足"]
    assert got["groups"]["三年级成语"]["statuses"]["守株待兔"] == "learned"
    assert got["explanation_prompt_template"] == "先讲意思"
    assert got["story_prompt_template"] == "讲个故事"
