import base64
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from voice_robot.config import get_settings
from voice_robot.admin_api import _resolved_prompt_config
from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.file_store import FileMemoryStore
from voice_robot.memory.vocabulary_store import VocabularyStore
from voice_robot.runtime_config import RuntimeConfigStore
from voice_robot.server import app


def test_status_returns_runtime_configuration(monkeypatch, tmp_path: Path) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    RuntimeConfigStore(runtime_config_path).save_speech_settings(
        mode="split",
        tts_api_key="tts-api-key-001",
        tts_ws_url="wss://example.com/api/v3/tts/unidirectional/stream",
        tts_voice_type="voice-runtime-a",
    )
    client = TestClient(app)

    response = client.get("/api/status")

    assert response.status_code == 200
    body = response.json()
    assert body["health"] == "ok"
    assert body["app_name"] == "voice-robot"
    assert body["default_user_id"] == "default-user"
    assert body["llm_api_key_configured"] == "false"
    assert body["speech_tts_ws_url"] == "wss://example.com/api/v3/tts/unidirectional/stream"
    assert body["tts_voice_type"] == "voice-runtime-a"
    assert body["tts_voice_type_source"] == "runtime_override"
    assert body["speech_asr_ws_url"] == "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"


def test_llm_settings_return_masked_value_without_reveal(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    RuntimeConfigStore(tmp_path / "console_settings.json").save_llm_settings(
        api_key="sk-test-123456",
        model="ep-test-123",
        base_url="https://example.com/api/v3",
        system_prompt="system prompt from runtime",
        user_prompt_template="user: {user_text}",
    )
    client = TestClient(app)

    response = client.get("/api/settings/llm")

    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["api_key_source"] == "runtime_override"
    assert response.json()["api_key"] == ""
    assert response.json()["api_key_masked"].startswith("sk")
    assert response.json()["model"] == "ep-test-123"
    assert response.json()["model_source"] == "runtime_override"
    assert response.json()["base_url"] == "https://example.com/api/v3"
    assert response.json()["base_url_source"] == "runtime_override"
    assert response.json()["system_prompt"] == "system prompt from runtime"
    assert response.json()["system_prompt_source"] == "runtime_override"
    assert response.json()["user_prompt_template"] == "user: {user_text}"
    assert response.json()["user_prompt_source"] == "runtime_override"


def test_llm_settings_reveal_returns_plaintext_value(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    RuntimeConfigStore(tmp_path / "console_settings.json").save_llm_settings(
        api_key="sk-test-abcdef",
        model="",
        base_url="",
        system_prompt="",
        user_prompt_template="",
    )
    client = TestClient(app)

    response = client.get("/api/settings/llm?reveal=true")

    assert response.status_code == 200
    assert response.json()["api_key"] == "sk-test-abcdef"


def test_llm_settings_update_persists_runtime_override(
    monkeypatch, tmp_path: Path
) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.put(
        "/api/settings/llm",
        json={
            "api_key": "sk-updated-9999",
            "model": "ep-updated-9999",
            "base_url": "https://example.com/runtime/v3",
            "system_prompt": "updated system",
            "user_prompt_template": "u={user_text}",
        },
    )

    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["api_key_source"] == "runtime_override"
    assert response.json()["model"] == "ep-updated-9999"
    assert response.json()["base_url"] == "https://example.com/runtime/v3"
    assert response.json()["system_prompt"] == "updated system"
    assert response.json()["user_prompt_template"] == "u={user_text}"

    stored = RuntimeConfigStore(runtime_config_path).load_llm_settings()
    assert stored["api_key"] == "sk-updated-9999"
    assert stored["model"] == "ep-updated-9999"
    assert stored["base_url"] == "https://example.com/runtime/v3"
    assert stored["system_prompt"] == "updated system"
    assert stored["user_prompt_template"] == "u={user_text}"


def test_bot_profile_settings_roundtrip_and_prompt_injection(
    monkeypatch, tmp_path: Path
) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    client = TestClient(app)

    initial = client.get("/api/settings/bot-profile")
    assert initial.status_code == 200
    assert initial.json()["enabled"] is False
    assert initial.json()["custom_prompt"] == ""

    response = client.put(
        "/api/settings/bot-profile",
        json={
            "enabled": True,
            "name": "故事老师",
            "persona": "耐心、鼓励、会主动追问小朋友的兴趣",
            "response_style": "回答先给一句结论，再给一个小例子",
            "custom_prompt": "后续回复必须优先用用户喜欢的主题举例。",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["name"] == "故事老师"
    assert body["source"] == "runtime_override"

    stored = RuntimeConfigStore(runtime_config_path).load_bot_profile_settings()
    assert stored["custom_prompt"] == "后续回复必须优先用用户喜欢的主题举例。"

    prompt_config, _, _ = _resolved_prompt_config(RuntimeConfigStore(runtime_config_path))
    assert "【Bot 画像】" in prompt_config.system_prompt
    assert "名称: 故事老师" in prompt_config.system_prompt
    assert "后续回复必须优先用用户喜欢的主题举例。" in prompt_config.system_prompt

    llm_settings = client.get("/api/settings/llm")
    assert llm_settings.status_code == 200
    assert "【Bot 画像】" not in llm_settings.json()["system_prompt"]


def test_ui_settings_default_princess_and_roundtrip(monkeypatch, tmp_path: Path) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    client = TestClient(app)

    # Default character is the princess.
    initial = client.get("/api/settings/ui")
    assert initial.status_code == 200
    assert initial.json() == {"character": "princess"}

    # Switch to cat persists in the runtime config.
    updated = client.put("/api/settings/ui", json={"character": "cat"})
    assert updated.status_code == 200
    assert updated.json()["character"] == "cat"
    assert RuntimeConfigStore(runtime_config_path).load_ui_settings() == {"character": "cat"}
    assert client.get("/api/settings/ui").json() == {"character": "cat"}

    # Unknown character is rejected.
    assert client.put("/api/settings/ui", json={"character": "dragon"}).status_code == 422


def test_vocab_groups_settings_roundtrip(monkeypatch, tmp_path: Path) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    client = TestClient(app)

    initial = client.get("/api/vocab/groups")
    assert initial.status_code == 200
    assert initial.json() == {
        "active_group": "一年级（下）生字",
        "active_status_filter": "all",
        "groups": {"一年级（下）生字": {"characters": [], "statuses": {}, "sections": []}},
        "prompt_template": "",
    }

    response = client.put(
        "/api/vocab/groups",
        json={
            "active_group": "二年级",
            "active_status_filter": "learned",
            "groups": {
                "二年级": {
                    "characters": ["木", "林", "木"],
                    "statuses": {"木": "learned", "林": "unknown"},
                }
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["active_group"] == "二年级"
    assert response.json()["active_status_filter"] == "learned"
    assert response.json()["groups"]["二年级"] == {
        "characters": ["木", "林"],
        "statuses": {"木": "learned", "林": "unlearned"},
        "sections": [],
    }
    assert client.get("/api/vocab/groups").json()["groups"]["二年级"]["statuses"]["木"] == "learned"
    assert RuntimeConfigStore(runtime_config_path).load_vocab_groups_settings()["groups"]["二年级"][
        "statuses"
    ]["木"] == "learned"


def test_vocab_groups_settings_rejects_invalid_status_filter(
    monkeypatch, tmp_path: Path
) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.put(
        "/api/vocab/groups",
        json={
            "active_group": "一年级（下）生字",
            "active_status_filter": "done",
            "groups": {},
        },
    )

    assert response.status_code == 422


def test_speech_settings_roundtrip(monkeypatch, tmp_path: Path) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.get("/api/settings/speech")
    assert response.status_code == 200
    assert response.json()["configured"] is False

    update = client.put(
        "/api/settings/speech",
        json={
            "mode": "split",
            "asr_api_key": "asr-api-key-123",
            "asr_resource_id": "volc.bigasr.sauc.duration",
            "asr_ws_url": "wss://example.com/api/v3/sauc/bigmodel",
            "asr_sample_rate_hz": 16000,
            "tts_api_key": "tts-api-key-123",
            "tts_resource_id": "seed-tts-1.0",
            "tts_ws_url": "wss://example.com/api/v3/tts/unidirectional/stream",
            "tts_sample_rate_hz": 24000,
            "tts_encoding": "pcm",
            "tts_voice_type": "zh_female_vv_jupiter_bigtts",
        },
    )
    assert update.status_code == 200
    assert update.json()["configured"] is True
    assert update.json()["mode"] == "split"
    assert update.json()["asr_resource_id"] == "volc.bigasr.sauc.duration"
    assert update.json()["asr_ws_url"] == "wss://example.com/api/v3/sauc/bigmodel"
    assert update.json()["asr_api_key"] == ""
    assert update.json()["asr_api_key_masked"].startswith("as")
    assert update.json()["tts_resource_id"] == "seed-tts-1.0"
    assert update.json()["tts_ws_url"] == "wss://example.com/api/v3/tts/unidirectional/stream"
    assert update.json()["tts_api_key"] == ""
    assert update.json()["tts_api_key_masked"].startswith("tt")
    assert update.json()["tts_encoding"] == "pcm"
    assert update.json()["tts_voice_type"] == "zh_female_vv_jupiter_bigtts"
    assert update.json()["asr_sample_rate_hz"] == "16000"
    assert update.json()["tts_sample_rate_hz"] == "24000"

    reveal = client.get("/api/settings/speech?reveal=true")
    assert reveal.status_code == 200
    assert reveal.json()["asr_api_key"] == "asr-api-key-123"
    assert reveal.json()["tts_api_key"] == "tts-api-key-123"

    stored = RuntimeConfigStore(runtime_config_path).load_speech_settings()
    assert stored["mode"] == "split"
    assert stored["asr_api_key"] == "asr-api-key-123"
    assert stored["asr_resource_id"] == "volc.bigasr.sauc.duration"
    assert stored["asr_ws_url"] == "wss://example.com/api/v3/sauc/bigmodel"
    assert stored["tts_api_key"] == "tts-api-key-123"
    assert stored["tts_resource_id"] == "seed-tts-1.0"
    assert stored["tts_ws_url"] == "wss://example.com/api/v3/tts/unidirectional/stream"
    assert stored["tts_encoding"] == "pcm"


def test_speech_settings_default_resource_id_when_only_api_key(
    monkeypatch, tmp_path: Path
) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    client = TestClient(app)

    update = client.put(
        "/api/settings/speech",
        json={"mode": "split", "asr_api_key": "asr-api-key-123"},
    )

    assert update.status_code == 200
    assert update.json()["configured"] is True
    assert update.json()["asr_resource_id"] == "volc.bigasr.sauc.duration"
    assert update.json()["asr_ws_url"] == "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    assert update.json()["tts_sample_rate_hz"] == "16000"


def test_chat_endpoint_returns_reply(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.post("/api/chat", json={"user_id": "xiaoming", "text": "讲一个恐龙故事"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"]
    assert body["reply_source"] in {"ark", "fallback-local", "local-rule"}
    assert "safety_passed" in body
    assert "prompt_debug" in body
    assert body["prompt_debug"]["memory_block"]


def test_chat_stream_endpoint_returns_ndjson_events(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.post(
        "/api/chat/stream",
        json={"user_id": "xiaoming", "text": "讲一个恐龙故事"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[0]["type"] == "prompt"
    assert events[0]["prompt_debug"]["memory_block"]
    assert events[1]["type"] == "start"
    assert events[-1]["type"] == "end"
    deltas = [event["text"] for event in events if event["type"] == "delta"]
    assert deltas
    assert events[-1]["reply"] == "".join(deltas)
    assert events[-1]["reply_source"] in {"ark", "fallback-local", "local-rule"}


def test_memory_endpoints_return_profile_index_and_entry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    store = FileMemoryStore(tmp_path)
    store.save_profile(
        "xiaoming",
        {"name": "小明", "grade": "三年级", "interests": ["恐龙"]},
    )
    memory_id = store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="小明喜欢恐龙故事。",
        tags=["恐龙"],
        importance=0.8,
    )

    client = TestClient(app)

    users = client.get("/api/memory/users")
    assert users.status_code == 200
    assert users.json() == {"users": ["xiaoming"]}

    profile = client.get("/api/memory/users/xiaoming/profile")
    assert profile.status_code == 200
    assert profile.json()["name"] == "小明"

    index = client.get("/api/memory/users/xiaoming/index")
    assert index.status_code == 200
    assert index.json()["entries"][0]["id"] == memory_id

    entry = client.get(f"/api/memory/users/xiaoming/entries/{memory_id}")
    assert entry.status_code == 200
    assert entry.json()["title"] == "喜欢恐龙"
    assert "小明喜欢恐龙故事" in entry.json()["content"]


def test_memory_entry_rejects_unsafe_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    safe_user_id = FileMemoryStore._safe_component("xiaoming")
    index_path = tmp_path / "memories" / safe_user_id / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            [
                {
                    "id": "mem_bad",
                    "category": "preference",
                    "title": "bad",
                    "path": "../profiles/xiaoming.json",
                    "importance": 0.1,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    client = TestClient(app)

    response = client.get("/api/memory/users/xiaoming/entries/mem_bad")

    assert response.status_code == 500
    assert response.json()["detail"] == "invalid memory entry path"


def test_memory_profile_not_found(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.get("/api/memory/users/unknown/profile")

    assert response.status_code == 404
    assert response.json()["detail"] == "profile not found"


def test_memory_index_missing_returns_empty_list(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    response = client.get("/api/memory/users/unknown/index")

    assert response.status_code == 200
    assert response.json() == {"entries": []}


def test_vocabulary_admin_endpoints_return_index_and_history(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    VocabularyStore(tmp_path).record_question(
        "xiaoming",
        character="十",
        question="十字怎么写",
        turn_id="turn_1",
        trace_available=True,
    )
    client = TestClient(app)

    users = client.get("/api/memory/users")
    vocab = client.get("/api/memory/users/xiaoming/vocabulary")
    history = client.get("/api/memory/users/xiaoming/vocabulary/history?limit=10")

    assert users.status_code == 200
    assert "xiaoming" in users.json()["users"]
    assert vocab.status_code == 200
    assert history.status_code == 200
    assert vocab.json()["vocabulary"][0]["character"] == "十"
    assert vocab.json()["vocabulary"][0]["ask_count"] == 1
    assert history.json()["history"][0]["question"] == "十字怎么写"


def test_memory_turns_api_supports_list_patch_delete(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH",
        str(tmp_path / "console_settings.json"),
    )
    get_settings.cache_clear()
    client = TestClient(app)
    client.post("/api/chat", json={"user_id": "xiaoming", "text": "讲一个恐龙故事"})

    turns_response = client.get("/api/memory/users/xiaoming/turns?limit=20")
    assert turns_response.status_code == 200
    turn = turns_response.json()["turns"][0]

    patch_response = client.patch(
        f"/api/memory/users/xiaoming/turns/{turn['turn_id']}",
        json={"assistant_text": "编辑后的回复"},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["turn"]["assistant_text"] == "编辑后的回复"

    delete_response = client.delete(
        f"/api/memory/users/xiaoming/turns/{turn['turn_id']}"
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True


def test_turn_tts_audio_endpoint_returns_saved_audio(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    store = ConversationStore(tmp_path)
    turn = store.append_turn(
        "xiaoming",
        user_text="讲故事",
        assistant_text="小恐龙回家了。",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )
    store.attach_tts_audio(
        "xiaoming",
        turn["turn_id"],
        audio_bytes=b"pcm-audio",
        audio_format="pcm_s16le",
        sample_rate_hz=16000,
        byte_length=9,
    )
    client = TestClient(app)

    response = client.get(f"/api/memory/users/xiaoming/turns/{turn['turn_id']}/tts")

    assert response.status_code == 200
    assert response.content == b"pcm-audio"
    assert response.headers["content-type"] == "audio/L16; rate=16000; channels=1"
    assert response.headers["x-tts-audio-format"] == "pcm_s16le"

    turns_response = client.get("/api/memory/users/xiaoming/turns?limit=1")
    assert turns_response.status_code == 200
    stored_turn = turns_response.json()["turns"][0]
    assert "tts_audio_base64" not in stored_turn
    digest = hashlib.sha256(b"pcm-audio").hexdigest()
    assert stored_turn["tts_audio_sha256"] == digest
    assert stored_turn["tts_audio_path"].endswith(f"/tts/{digest}.pcm")


def test_turn_tts_audio_endpoint_reads_legacy_embedded_base64(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    store = ConversationStore(tmp_path)
    turn = store.append_turn(
        "xiaoming",
        user_text="讲故事",
        assistant_text="小恐龙回家了。",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )
    legacy_audio = b"legacy-pcm"
    turns_path = tmp_path / "conversations" / "xiaoming" / "turns.jsonl"
    legacy_turn = {
        **turn,
        "tts_audio_base64": base64.b64encode(legacy_audio).decode("ascii"),
        "tts_audio_format": "pcm_s16le",
        "tts_sample_rate_hz": 16000,
        "tts_byte_length": len(legacy_audio),
    }
    turns_path.write_text(json.dumps(legacy_turn, ensure_ascii=False) + "\n", encoding="utf-8")
    client = TestClient(app)

    response = client.get(f"/api/memory/users/xiaoming/turns/{turn['turn_id']}/tts")

    assert response.status_code == 200
    assert response.content == legacy_audio
    # A read is compatibility-only: migration happens on the next metadata rewrite.
    stored = json.loads(turns_path.read_text(encoding="utf-8"))
    assert stored["tts_audio_base64"] == legacy_turn["tts_audio_base64"]


def test_turn_tts_audio_endpoint_rejects_missing_audio(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    store = ConversationStore(tmp_path)
    turn = store.append_turn(
        "xiaoming",
        user_text="讲故事",
        assistant_text="小恐龙回家了。",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )
    client = TestClient(app)

    response = client.get(f"/api/memory/users/xiaoming/turns/{turn['turn_id']}/tts")

    assert response.status_code == 404
    assert response.json()["detail"] == "turn tts audio not found"


def test_turn_tts_audio_endpoint_rehydrates_legacy_text_payload(
    monkeypatch, tmp_path: Path
) -> None:
    runtime_config_path = tmp_path / "console_settings.json"
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(runtime_config_path))
    get_settings.cache_clear()
    store = ConversationStore(tmp_path)
    turn = store.append_turn(
        "xiaoming",
        user_text="讲故事",
        assistant_text="小恐龙回家了。",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )
    legacy_path = tmp_path / "conversations" / "xiaoming" / "tts" / f"{turn['turn_id']}.pcm"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(turn["assistant_text"].encode("utf-8"))
    store.update_turn(
        "xiaoming",
        turn["turn_id"],
        {
            "tts_audio_path": str(legacy_path.relative_to(tmp_path)),
            "tts_audio_format": "pcm_s16le",
            "tts_sample_rate_hz": 16000,
            "tts_byte_length": legacy_path.stat().st_size,
        },
    )

    import voice_robot.admin_api as admin_api_mod

    class FakeLocalTTS:
        def __init__(self, sample_rate_hz: int = 16000) -> None:
            self.sample_rate_hz = sample_rate_hz

        async def synthesize(self, text: str):
            assert text == "小恐龙回家了。"
            yield b"\x01\x00\x02\x00"

    monkeypatch.setattr(admin_api_mod, "LocalTTSService", FakeLocalTTS)
    client = TestClient(app)

    response = client.get(f"/api/memory/users/xiaoming/turns/{turn['turn_id']}/tts")

    assert response.status_code == 200
    assert response.content == b"\x01\x00\x02\x00"
    stored = store.list_turns("xiaoming", include_audio_payload=True)[0]
    digest = hashlib.sha256(b"\x01\x00\x02\x00").hexdigest()
    assert stored["tts_audio_sha256"] == digest
    assert stored["tts_audio_path"].endswith(f"/tts/{digest}.pcm")
    assert "tts_audio_base64" not in stored
    # Rehydration must not remove the legacy file or any prior data.
    assert legacy_path.read_bytes() == turn["assistant_text"].encode("utf-8")


def test_memory_summaries_api_supports_list_patch_delete(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    from voice_robot.memory.summary_store import SummaryStore

    store = SummaryStore(tmp_path)
    summary = store.save_summary(
        "xiaoming",
        start_sequence=1,
        end_sequence=20,
        content="- 旧摘要",
        summary_source="local",
    )
    client = TestClient(app)

    list_response = client.get("/api/memory/users/xiaoming/summaries")
    assert list_response.status_code == 200
    assert list_response.json()["summaries"][0]["summary_id"] == summary["summary_id"]

    patch_response = client.patch(
        f"/api/memory/users/xiaoming/summaries/{summary['summary_id']}",
        json={"content": "- 新摘要", "summary_source": "manual"},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["summary"]["summary_source"] == "manual"

    delete_response = client.delete(
        f"/api/memory/users/xiaoming/summaries/{summary['summary_id']}"
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True


def test_profile_entries_and_audit_management_api(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    store = FileMemoryStore(tmp_path)
    store.save_profile("xiaoming", {"user_id": "xiaoming", "interests": ["恐龙"]})
    memory_id = store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="喜欢恐龙故事。",
        tags=["恐龙"],
        importance=0.8,
    )
    from voice_robot.memory.audit_log_store import AuditLogStore

    event = AuditLogStore(tmp_path).append_event(
        "xiaoming",
        event_type="asr_result",
        payload={"text": "hello"},
    )
    client = TestClient(app)

    profile_response = client.patch(
        "/api/memory/users/xiaoming/profile",
        json={"style_preferences": ["简短"]},
    )
    assert profile_response.status_code == 200
    assert profile_response.json()["profile"]["style_preferences"] == ["简短"]

    entry_response = client.patch(
        f"/api/memory/users/xiaoming/entries/{memory_id}",
        json={"title": "喜欢太空恐龙", "content": "喜欢太空恐龙故事。"},
    )
    assert entry_response.status_code == 200
    assert entry_response.json()["entry"]["title"] == "喜欢太空恐龙"

    audit_response = client.get("/api/memory/users/xiaoming/audit?limit=10")
    assert audit_response.status_code == 200
    assert audit_response.json()["events"][0]["event_id"] == event["event_id"]

    delete_event = client.delete(f"/api/memory/users/xiaoming/audit/{event['event_id']}")
    assert delete_event.status_code == 200
    assert delete_event.json()["deleted"] is True


def test_memory_entry_delete_and_audit_patch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    store = FileMemoryStore(tmp_path)
    memory_id = store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="喜欢恐龙故事。",
        tags=["恐龙"],
        importance=0.8,
    )
    from voice_robot.memory.audit_log_store import AuditLogStore

    event = AuditLogStore(tmp_path).append_event(
        "xiaoming",
        event_type="asr_result",
        payload={"text": "hello"},
    )
    client = TestClient(app)

    patch_event = client.patch(
        f"/api/memory/users/xiaoming/audit/{event['event_id']}",
        json={"event_type": "updated", "payload": {"ok": True}},
    )
    assert patch_event.status_code == 200
    assert patch_event.json()["event"]["event_type"] == "updated"
    assert patch_event.json()["event"]["payload"] == {"ok": True}

    delete_entry = client.delete(f"/api/memory/users/xiaoming/entries/{memory_id}")
    assert delete_entry.status_code == 200
    assert delete_entry.json()["deleted"] is True


def test_token_usage_metrics_endpoint(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    from voice_robot.metrics.token_usage_store import TokenUsageStore

    TokenUsageStore(tmp_path).append_event(
        user_id="xiaoming",
        scenario="chat",
        vendor="ark",
        model="ep-test",
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        success=True,
        request_kind="generate_reply",
        latency_ms=125,
        prompt_chars=80,
        completion_chars=24,
    )
    client = TestClient(app)

    response = client.get(
        "/api/metrics/token-usage"
        "?group_by=user_id,scenario,vendor,request_kind&days=90"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"]["calls"] == 1
    assert payload["total"]["successful_calls"] == 1
    assert payload["total"]["failed_calls"] == 0
    assert payload["total"]["success_rate"] == 100.0
    assert payload["total"]["avg_latency_ms"] == 125.0
    assert payload["total"]["p95_latency_ms"] == 125
    assert payload["total"]["avg_prompt_tokens"] == 10.0
    assert payload["total"]["total_tokens"] == 30
    assert payload["rows"][0]["user_id"] == "xiaoming"
    assert payload["rows"][0]["request_kind"] == "generate_reply"
