import json

from voice_robot.memory.idiom_explanation_store import IdiomExplanationStore


def test_upsert_and_get_roundtrip(tmp_path) -> None:
    store = IdiomExplanationStore(tmp_path)
    store.upsert(
        "console-idiom",
        idiom="守株待兔",
        pinyin="shǒu zhū dài tù",
        explanation="比喻不主动努力，只想靠运气。",
        story="从前有个农夫……",
        source="ark",
        explanation_tts=b"\x01\x02\x03\x04",
        explanation_sample_rate_hz=16000,
        story_tts=b"\x05\x06",
        story_sample_rate_hz=24000,
    )

    entry = store.get("console-idiom", "守株待兔")
    assert entry is not None
    assert entry["idiom"] == "守株待兔"
    assert entry["pinyin"] == "shǒu zhū dài tù"
    assert entry["explanation"].startswith("比喻")
    assert entry["story"].startswith("从前")
    assert entry["source"] == "ark"
    assert entry["explanation_tts_sample_rate_hz"] == 16000
    assert entry["explanation_tts_byte_length"] == 4
    assert entry["story_tts_sample_rate_hz"] == 24000


def test_read_tts_audio_per_kind(tmp_path) -> None:
    store = IdiomExplanationStore(tmp_path)
    store.upsert(
        "console-idiom",
        idiom="画蛇添足",
        pinyin="",
        explanation="解读。",
        story="故事。",
        source="ark",
        explanation_tts=b"explain!!",
        explanation_sample_rate_hz=16000,
        story_tts=b"storyaudio",
        story_sample_rate_hz=16000,
    )

    expl = store.read_tts_audio("console-idiom", "画蛇添足", "explanation")
    assert expl == (b"explain!!", 16000)
    story = store.read_tts_audio("console-idiom", "画蛇添足", "story")
    assert story == (b"storyaudio", 16000)
    # Unknown kind / missing idiom return None.
    assert store.read_tts_audio("console-idiom", "画蛇添足", "bogus") is None
    assert store.read_tts_audio("console-idiom", "不存在", "explanation") is None


def test_read_tts_audio_missing_returns_none(tmp_path) -> None:
    store = IdiomExplanationStore(tmp_path)
    store.upsert(
        "console-idiom",
        idiom="刻舟求剑",
        pinyin="",
        explanation="解读。",
        story="故事。",
        source="ark",
    )
    assert store.read_tts_audio("console-idiom", "刻舟求剑", "explanation") is None
    assert store.read_tts_audio("console-idiom", "刻舟求剑", "story") is None


def test_upsert_overwrites_and_clears_stale_tts(tmp_path) -> None:
    store = IdiomExplanationStore(tmp_path)
    store.upsert(
        "console-idiom",
        idiom="亡羊补牢",
        pinyin="",
        explanation="旧解读。",
        story="旧故事。",
        source="ark",
        explanation_tts=b"oldaudio",
        explanation_sample_rate_hz=16000,
        story_tts=b"oldstory",
        story_sample_rate_hz=16000,
    )
    # Re-generate text without audio -> both stale TTS markers dropped.
    store.upsert(
        "console-idiom",
        idiom="亡羊补牢",
        pinyin="",
        explanation="新解读。",
        story="新故事。",
        source="ark",
    )

    entry = store.get("console-idiom", "亡羊补牢")
    assert entry["explanation"] == "新解读。"
    assert "explanation_tts_path" not in entry
    assert "story_tts_path" not in entry
    assert store.read_tts_audio("console-idiom", "亡羊补牢", "explanation") is None
    assert store.read_tts_audio("console-idiom", "亡羊补牢", "story") is None


def test_list_returns_all_entries(tmp_path) -> None:
    store = IdiomExplanationStore(tmp_path)
    for idiom in ("守株待兔", "画蛇添足", "刻舟求剑"):
        store.upsert(
            "console-idiom",
            idiom=idiom,
            pinyin="",
            explanation=f"{idiom}的解读。",
            story=f"{idiom}的故事。",
            source="ark",
        )
    items = store.list("console-idiom")
    assert {item["idiom"] for item in items} == {"守株待兔", "画蛇添足", "刻舟求剑"}


def test_tolerates_corrupt_index(tmp_path) -> None:
    store = IdiomExplanationStore(tmp_path)
    path = tmp_path / "idiom_explanations" / "console-idiom" / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json", encoding="utf-8")
    assert store.list("console-idiom") == []


def test_read_tts_audio_rejects_path_traversal(tmp_path) -> None:
    store = IdiomExplanationStore(tmp_path)
    store.upsert(
        "console-idiom",
        idiom="守株待兔",
        pinyin="",
        explanation="解读。",
        story="故事。",
        source="ark",
        explanation_tts=b"data",
        explanation_sample_rate_hz=16000,
    )
    index_path = tmp_path / "idiom_explanations" / "console-idiom" / "index.json"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    for entry in data:
        entry["explanation_tts_path"] = "../../../etc/passwd"
    index_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert store.read_tts_audio("console-idiom", "守株待兔", "explanation") is None
