import json

from fastapi.testclient import TestClient

from voice_robot.config import get_settings
from voice_robot.memory.idiom_explanation_store import IdiomExplanationStore
from voice_robot.runtime_config import RuntimeConfigStore
from voice_robot.server import app


def _recv_json(ws):
    while True:
        message = ws.receive()
        text = message.get("text")
        if text is not None:
            return json.loads(text)
        if message.get("bytes") is not None:
            continue
        raise AssertionError(f"unexpected ws frame: {message}")


def _drain_turn(ws):
    events = []
    while True:
        msg = _recv_json(ws)
        events.append(msg)
        if msg.get("type") == "tts_end":
            return events


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()
    return TestClient(app)


def _auth(ws):
    ws.send_json({"type": "auth", "device_id": "web-dev", "token": "local-token"})
    assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}


def test_hanzi_writing_action_shows_trace(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect("/ws") as ws:
        _auth(ws)
        ws.send_json({"type": "text_input", "user_id": "u", "text": "大怎么写"})
        ws.receive_json()  # ack
        events = _drain_turn(ws)
        traces = [e for e in events if e.get("type") == "show_character_trace"]
        assert len(traces) == 1
        assert traces[0].get("character") == "大"


def test_hanzi_writing_handles_repeated_punctuated_speech(monkeypatch, tmp_path) -> None:
    # 说两遍 + 标点导致 ASR 拼成「大，怎么写？大，怎么写？」——标准抽取失败，
    # 触发词兜底仍应描红「大」。
    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect("/ws") as ws:
        _auth(ws)
        ws.send_json({"type": "text_input", "user_id": "u", "text": "大，怎么写？大，怎么写？"})
        ws.receive_json()
        events = _drain_turn(ws)
        traces = [e for e in events if e.get("type") == "show_character_trace"]
        assert len(traces) == 1
        assert traces[0].get("character") == "大"


def test_hanzi_writing_disabled_skips_trace(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    # 关闭汉字书写场景 → 不再描红。
    client.put("/api/scenes/hanzi_writing", json={"enabled": False, "enter_keywords": []})
    with client.websocket_connect("/ws") as ws:
        _auth(ws)
        ws.send_json({"type": "text_input", "user_id": "u", "text": "大怎么写"})
        ws.receive_json()
        events = _drain_turn(ws)
        assert not [e for e in events if e.get("type") == "show_character_trace"]


def test_idiom_query_adds_new_idiom_and_shows(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect("/ws") as ws:
        _auth(ws)
        ws.send_json({"type": "text_input", "user_id": "u", "text": "守株待兔是什么意思"})
        ws.receive_json()
        events = _drain_turn(ws)
        idioms = [e for e in events if e.get("type") == "show_idiom"]
        assert len(idioms) == 1
        assert idioms[0].get("idiom") == "守株待兔"
    # 新成语已写入控制台默认成语库（路径 A.2）。
    groups = client.get("/api/idiom/groups").json()["groups"]
    members = {i for g in groups.values() for i in g.get("idioms", [])}
    assert "守株待兔" in members


def test_idiom_query_with_leading_filler_still_shows(monkeypatch, tmp_path) -> None:
    # ASR 把口语前缀「请问」一并识别进来，仍应切到四字成语而非走普通对话。
    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect("/ws") as ws:
        _auth(ws)
        ws.send_json({"type": "text_input", "user_id": "u", "text": "请问守株待兔是什么意思呀"})
        ws.receive_json()
        events = _drain_turn(ws)
        idioms = [e for e in events if e.get("type") == "show_idiom"]
        assert len(idioms) == 1
        assert idioms[0].get("idiom") == "守株待兔"
    groups = client.get("/api/idiom/groups").json()["groups"]
    members = {i for g in groups.values() for i in g.get("idioms", [])}
    assert "守株待兔" in members


def test_idiom_query_non_four_char_not_added(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    with client.websocket_connect("/ws") as ws:
        _auth(ws)
        # 「苹果」二字、非库内 → 不当成语处理，走普通对话（无 show_idiom）。
        ws.send_json({"type": "text_input", "user_id": "u", "text": "苹果是什么意思"})
        ws.receive_json()
        events = _drain_turn(ws)
        assert not [e for e in events if e.get("type") == "show_idiom"]
    groups = client.get("/api/idiom/groups").json()["groups"]
    members = {i for g in groups.values() for i in g.get("idioms", [])}
    assert "苹果" not in members


def test_idiom_generic_intent_plays_random_from_library(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    settings = get_settings()
    # 预置库成员 + 缓存内容。
    idiom_store = IdiomExplanationStore(settings.memory_root)
    idiom_store.upsert(
        "console-idiom",
        idiom="画蛇添足",
        pinyin="huà shé tiān zú",
        explanation="比喻多此一举。",
        story="从前有人画蛇，给蛇添了脚，反而输了。",
        source="test",
    )
    store = RuntimeConfigStore(settings.runtime_config_path)
    store.save_idiom_groups_settings(
        active_group="成语 · 第一批",
        active_status_filter="all",
        groups={"成语 · 第一批": {"idioms": ["画蛇添足"]}},
    )
    with client.websocket_connect("/ws") as ws:
        _auth(ws)
        ws.send_json({"type": "text_input", "user_id": "u", "text": "讲个成语"})
        ws.receive_json()
        events = _drain_turn(ws)
        idioms = [e for e in events if e.get("type") == "show_idiom"]
        assert len(idioms) == 1
        assert idioms[0].get("idiom") == "画蛇添足"


def test_collect_idiom_cached_audio_reuses_stored_pcm(monkeypatch, tmp_path) -> None:
    # 缓存命中 → 直接拼接 explanation+story 的 PCM，零现合成（修复「走缓存仍久等」）。
    from voice_robot import server

    _client(monkeypatch, tmp_path)  # sets memory root + clears settings cache
    settings = get_settings()
    sr = 16000
    idiom_store = IdiomExplanationStore(settings.memory_root)
    idiom_store.upsert(
        server.IDIOM_CONTENT_USER_ID,
        idiom="画蛇添足",
        pinyin="",
        explanation="比喻多此一举。",
        story="从前有人画蛇添足。",
        source="test",
        explanation_tts=b"AAAA",
        explanation_sample_rate_hz=sr,
        story_tts=b"BBBB",
        story_sample_rate_hz=sr,
    )
    assert server._collect_idiom_cached_audio("画蛇添足", sr) == b"AAAABBBB"
    # 采样率不匹配 → 回退现合成（返回空让调用方走 synthesize）。
    assert server._collect_idiom_cached_audio("画蛇添足", 24000) == b""
    # 未缓存 → 空。
    assert server._collect_idiom_cached_audio("守株待兔", sr) == b""
