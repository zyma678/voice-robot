import os

from voice_robot.memory.tts_response_cache import TTSResponseCacheStore


def _identity(**overrides):
    values = {
        "provider": "volc",
        "resource_id": "seed-tts-1.0",
        "voice_type": "child",
        "sample_rate_hz": 16000,
        "encoding": "pcm",
        "configuration_fingerprint": "endpoint-account-a",
    }
    values.update(overrides)
    return values


def test_tts_response_cache_hits_only_for_same_identity(tmp_path) -> None:
    store = TTSResponseCacheStore(tmp_path)
    store.put("你好", audio=b"pcm-audio", **_identity())

    assert store.get("你好", **_identity()) == b"pcm-audio"
    assert store.get("你好呀", **_identity()) is None
    assert store.get("你好", **_identity(voice_type="other")) is None
    assert store.get("你好", **_identity(sample_rate_hz=24000)) is None
    assert (
        store.get(
            "你好",
            **_identity(configuration_fingerprint="endpoint-account-b"),
        )
        is None
    )


def test_tts_response_cache_prunes_oldest_entry(tmp_path) -> None:
    store = TTSResponseCacheStore(tmp_path, max_entries=2, max_bytes=1024)
    store.put("一", audio=b"1", **_identity())
    first = next((tmp_path / "tts_response_cache").glob("*.pcm"))
    os.utime(first, ns=(1, 1))
    store.put("二", audio=b"22", **_identity())
    store.put("三", audio=b"333", **_identity())

    assert store.get("一", **_identity()) is None
    assert store.get("二", **_identity()) == b"22"
    assert store.get("三", **_identity()) == b"333"
    assert store.stats()["entries"] == 2


def test_tts_response_cache_rejects_entry_larger_than_budget(tmp_path) -> None:
    store = TTSResponseCacheStore(tmp_path, max_entries=2, max_bytes=3)

    assert store.put("太长", audio=b"1234", **_identity()) is None
    assert store.stats()["entries"] == 0


def test_tts_response_cache_rejects_corrupt_pcm(tmp_path) -> None:
    store = TTSResponseCacheStore(tmp_path)
    key = store.put("你好", audio=b"valid-pcm", **_identity())
    assert key is not None
    (tmp_path / "tts_response_cache" / f"{key}.pcm").write_bytes(b"corrupt")

    assert store.get("你好", **_identity()) is None
