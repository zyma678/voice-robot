from voice_robot.memory.vocab_explanation_store import VocabExplanationStore


def test_upsert_and_get_roundtrip(tmp_path) -> None:
    store = VocabExplanationStore(tmp_path)
    store.upsert(
        "console-vocab",
        character="十",
        word="十分",
        explanation="先写一横，再写一竖。",
        source="ark",
        tts_audio=b"\x01\x02\x03\x04",
        tts_sample_rate_hz=16000,
    )

    entry = store.get("console-vocab", "十")
    assert entry is not None
    assert entry["character"] == "十"
    assert entry["word"] == "十分"
    assert entry["explanation"] == "先写一横，再写一竖。"
    assert entry["source"] == "ark"
    assert entry["tts_sample_rate_hz"] == 16000
    assert entry["tts_byte_length"] == 4


def test_read_tts_audio_returns_bytes_and_rate(tmp_path) -> None:
    store = VocabExplanationStore(tmp_path)
    store.upsert(
        "console-vocab",
        character="火",
        word="火车",
        explanation="火字像跳动的火苗。",
        source="ark",
        tts_audio=b"abcd1234",
        tts_sample_rate_hz=24000,
    )

    result = store.read_tts_audio("console-vocab", "火")
    assert result is not None
    audio, rate = result
    assert audio == b"abcd1234"
    assert rate == 24000


def test_read_tts_audio_missing_returns_none(tmp_path) -> None:
    store = VocabExplanationStore(tmp_path)
    store.upsert(
        "console-vocab",
        character="水",
        word="水果",
        explanation="水字有三点。",
        source="ark",
    )
    assert store.read_tts_audio("console-vocab", "水") is None
    assert store.read_tts_audio("console-vocab", "缺") is None


def test_upsert_overwrites_and_clears_stale_tts(tmp_path) -> None:
    store = VocabExplanationStore(tmp_path)
    store.upsert(
        "console-vocab",
        character="日",
        word="日子",
        explanation="旧解读。",
        source="ark",
        tts_audio=b"oldaudio",
        tts_sample_rate_hz=16000,
    )
    # Re-generate text without audio -> stale TTS marker dropped.
    store.upsert(
        "console-vocab",
        character="日",
        word="日出",
        explanation="新解读。",
        source="ark",
    )

    entry = store.get("console-vocab", "日")
    assert entry["explanation"] == "新解读。"
    assert "tts_path" not in entry
    assert store.read_tts_audio("console-vocab", "日") is None


def test_list_returns_all_entries(tmp_path) -> None:
    store = VocabExplanationStore(tmp_path)
    for ch in ("一", "二", "三"):
        store.upsert(
            "console-vocab",
            character=ch,
            word=f"{ch}个",
            explanation=f"{ch}的解读。",
            source="ark",
        )
    items = store.list("console-vocab")
    assert {item["character"] for item in items} == {"一", "二", "三"}


def test_tolerates_corrupt_index(tmp_path) -> None:
    store = VocabExplanationStore(tmp_path)
    path = tmp_path / "vocab_explanations" / "console-vocab" / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json", encoding="utf-8")
    assert store.list("console-vocab") == []
