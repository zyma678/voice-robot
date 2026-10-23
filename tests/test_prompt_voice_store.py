from voice_robot.memory.prompt_voice_store import PromptVoiceCacheStore, prompt_text_hash


def _store(tmp_path):
    return PromptVoiceCacheStore(tmp_path)


def test_miss_then_hit(tmp_path) -> None:
    store = _store(tmp_path)
    text = "Let's practice English!"
    key = dict(
        text_hash=prompt_text_hash(text),
        voice_type="zh_female",
        sample_rate_hz=16000,
        encoding="pcm",
    )
    assert store.get_cached("english_tutor", "enter", **key) is None

    store.store("english_tutor", "enter", audio=b"\x01\x02\x03\x04", **key)
    cached = store.get_cached("english_tutor", "enter", **key)
    assert cached is not None
    audio, sample_rate = cached
    assert audio == b"\x01\x02\x03\x04"
    assert sample_rate == 16000


def test_text_change_invalidates(tmp_path) -> None:
    store = _store(tmp_path)
    base = dict(voice_type="v", sample_rate_hz=16000, encoding="pcm")
    store.store(
        "english_tutor", "enter", text_hash=prompt_text_hash("old"), audio=b"aaaa", **base
    )
    # New text hash -> miss (no stale audio served).
    assert (
        store.get_cached(
            "english_tutor", "enter", text_hash=prompt_text_hash("new"), **base
        )
        is None
    )


def test_voice_or_sample_rate_change_invalidates(tmp_path) -> None:
    store = _store(tmp_path)
    th = prompt_text_hash("hello")
    store.store(
        "english_tutor",
        "exit",
        text_hash=th,
        voice_type="v1",
        sample_rate_hz=16000,
        encoding="pcm",
        audio=b"zzzz",
    )
    assert (
        store.get_cached(
            "english_tutor", "exit", text_hash=th, voice_type="v2", sample_rate_hz=16000, encoding="pcm"
        )
        is None
    )
    assert (
        store.get_cached(
            "english_tutor", "exit", text_hash=th, voice_type="v1", sample_rate_hz=24000, encoding="pcm"
        )
        is None
    )


def test_enter_and_exit_kinds_are_independent(tmp_path) -> None:
    store = _store(tmp_path)
    key = dict(voice_type="v", sample_rate_hz=16000, encoding="pcm")
    store.store("english_tutor", "enter", text_hash=prompt_text_hash("in"), audio=b"in__", **key)
    store.store("english_tutor", "exit", text_hash=prompt_text_hash("out"), audio=b"out_", **key)
    enter = store.get_cached("english_tutor", "enter", text_hash=prompt_text_hash("in"), **key)
    exit_ = store.get_cached("english_tutor", "exit", text_hash=prompt_text_hash("out"), **key)
    assert enter is not None and enter[0] == b"in__"
    assert exit_ is not None and exit_[0] == b"out_"
