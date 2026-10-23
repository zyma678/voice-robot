import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from voice_robot.memory import ConversationStore


def test_append_turn_assigns_sequence_and_updates_meta(tmp_path) -> None:
    store = ConversationStore(tmp_path)

    first = store.append_turn(
        "xiaoming",
        user_text="你好",
        assistant_text="你好呀",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )
    second = store.append_turn(
        "xiaoming",
        user_text="讲个故事",
        assistant_text="从前有座山",
        reply_source="memory",
        fallback_reason=None,
        safety_passed=True,
    )

    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["turn_id"].startswith("turn_")
    assert second["turn_id"].startswith("turn_")
    assert first["turn_id"] != second["turn_id"]

    meta_path = tmp_path / "conversations" / "xiaoming" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["user_id"] == "xiaoming"
    assert meta["next_sequence"] == 3
    assert meta["last_summarized_sequence"] == 0


def test_concurrent_store_instances_do_not_lose_turns_or_sequences(tmp_path) -> None:
    def append(index: int) -> dict:
        # Runtime agents and background audio persistence construct independent
        # store objects, so the lock must be shared at the normalized path level.
        return ConversationStore(tmp_path).append_turn(
            "../xiaoming",
            user_text=f"q{index}",
            assistant_text=f"a{index}",
            reply_source="llm",
            fallback_reason=None,
            safety_passed=True,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(append, range(40)))

    turns = ConversationStore(tmp_path).list_turns("xiaoming")
    assert len(turns) == 40
    assert sorted(turn["sequence"] for turn in results) == list(range(1, 41))
    assert [turn["sequence"] for turn in turns] == list(range(1, 41))


def test_recent_turns_returns_latest_window_in_ascending_order(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    for sequence in range(1, 26):
        store.append_turn(
            "xiaoming",
            user_text=f"user {sequence}",
            assistant_text=f"assistant {sequence}",
            reply_source="llm",
            fallback_reason=None,
            safety_passed=True,
        )

    turns = store.list_recent_turns("xiaoming", limit=20)

    assert [turn["sequence"] for turn in turns] == list(range(6, 26))


def test_turns_can_be_read_by_sequence_range(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    for sequence in range(1, 6):
        store.append_turn(
            "xiaoming",
            user_text=f"user {sequence}",
            assistant_text=f"assistant {sequence}",
            reply_source="llm",
            fallback_reason=None,
            safety_passed=True,
        )

    turns = store.list_turns("xiaoming", start_sequence=2, end_sequence=4)

    assert [turn["sequence"] for turn in turns] == [2, 3, 4]


def test_update_turn_rewrites_turn_and_keeps_backup(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    turn = store.append_turn(
        "xiaoming",
        user_text="旧问题",
        assistant_text="旧回答",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )

    updated = store.update_turn(
        "xiaoming",
        turn["turn_id"],
        {"user_text": "新问题", "assistant_text": "新回答"},
    )

    assert updated is not None
    assert updated["turn_id"] == turn["turn_id"]
    assert updated["sequence"] == 1
    assert updated["user_text"] == "新问题"
    assert updated["assistant_text"] == "新回答"

    turns = store.list_turns("xiaoming")
    assert [stored["user_text"] for stored in turns] == ["新问题"]
    backup_files = list((tmp_path / "conversations" / "xiaoming").glob("turns.jsonl.*.bak"))
    assert len(backup_files) == 1


def test_attach_tts_audio_writes_content_addressed_sidecar(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    turn = store.append_turn(
        "xiaoming",
        user_text="讲故事",
        assistant_text="小恐龙回家了。",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )

    updated = store.attach_tts_audio(
        "xiaoming",
        turn["turn_id"],
        audio_bytes=b"\x01\x00\x02\x00",
        audio_format="pcm_s16le",
        sample_rate_hz=16000,
        byte_length=4,
    )

    assert updated is not None
    assert updated["tts_audio_format"] == "pcm_s16le"
    assert updated["tts_sample_rate_hz"] == 16000
    assert updated["tts_byte_length"] == 4
    digest = hashlib.sha256(b"\x01\x00\x02\x00").hexdigest()
    assert updated["tts_audio_sha256"] == digest
    assert updated["tts_audio_path"] == f"conversations/xiaoming/tts/{digest}.pcm"
    assert "tts_audio_base64" not in updated

    loaded = store.list_turns("xiaoming", include_audio_payload=True)[0]
    assert loaded == updated
    sidecar = tmp_path / loaded["tts_audio_path"]
    assert sidecar.read_bytes() == b"\x01\x00\x02\x00"
    assert list(sidecar.parent.parent.glob("turns.jsonl.*.bak")) == []


def test_attach_tts_audio_deduplicates_identical_pcm(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    turns = [
        store.append_turn(
            "xiaoming",
            user_text=f"问题 {index}",
            assistant_text="同一句回答",
            reply_source="llm",
            fallback_reason=None,
            safety_passed=True,
        )
        for index in range(2)
    ]

    updated = [
        store.attach_tts_audio(
            "xiaoming",
            turn["turn_id"],
            audio_bytes=b"same-pcm",
            audio_format="pcm_s16le",
            sample_rate_hz=16000,
            byte_length=8,
        )
        for turn in turns
    ]

    assert updated[0] is not None and updated[1] is not None
    assert updated[0]["tts_audio_path"] == updated[1]["tts_audio_path"]
    sidecars = list((tmp_path / "conversations" / "xiaoming" / "tts").glob("*.pcm"))
    assert len(sidecars) == 1
    assert sidecars[0].read_bytes() == b"same-pcm"


def test_attach_tts_audio_does_not_overwrite_corrupt_existing_sidecar(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    turn = store.append_turn(
        "xiaoming",
        user_text="问题",
        assistant_text="回答",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )
    audio = b"expected-pcm"
    digest = hashlib.sha256(audio).hexdigest()
    sidecar = tmp_path / "conversations" / "xiaoming" / "tts" / f"{digest}.pcm"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(b"existing-unexpected-data")

    with pytest.raises(OSError, match="invalid existing TTS sidecar"):
        store.attach_tts_audio(
            "xiaoming",
            turn["turn_id"],
            audio_bytes=audio,
            audio_format="pcm_s16le",
            sample_rate_hz=16000,
            byte_length=len(audio),
        )

    assert sidecar.read_bytes() == b"existing-unexpected-data"
    stored = store.get_turn("xiaoming", turn["turn_id"], include_audio_payload=True)
    assert stored is not None
    assert "tts_audio_path" not in stored


def test_many_audio_turns_keep_active_jsonl_metadata_sized(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    turns = [
        store.append_turn(
            "xiaoming",
            user_text=f"问题 {index}",
            assistant_text=f"回答 {index}",
            reply_source="llm",
            fallback_reason=None,
            safety_passed=True,
        )
        for index in range(100)
    ]
    pcm_bytes = 0
    for index, turn in enumerate(turns):
        audio = index.to_bytes(2, "big") + b"x" * 4094
        pcm_bytes += len(audio)
        assert store.attach_tts_audio(
            "xiaoming",
            turn["turn_id"],
            audio_bytes=audio,
            audio_format="pcm_s16le",
            sample_rate_hz=16000,
            byte_length=len(audio),
        ) is not None

    turns_path = tmp_path / "conversations" / "xiaoming" / "turns.jsonl"
    assert pcm_bytes == 409_600
    assert turns_path.stat().st_size < 100_000
    assert b"tts_audio_base64" not in turns_path.read_bytes()
    assert len(list((turns_path.parent / "tts").glob("*.pcm"))) == 100


def test_rewrite_externalizes_legacy_base64_without_changing_backup(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    turn = store.append_turn(
        "xiaoming",
        user_text="旧问题",
        assistant_text="旧回答",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )
    audio = b"legacy-pcm"
    legacy_turn = {
        **turn,
        "tts_audio_base64": base64.b64encode(audio).decode("ascii"),
        "tts_audio_format": "pcm_s16le",
        "tts_sample_rate_hz": 16000,
        "tts_byte_length": len(audio),
    }
    turns_path = tmp_path / "conversations" / "xiaoming" / "turns.jsonl"
    turns_path.write_text(json.dumps(legacy_turn, ensure_ascii=False) + "\n", encoding="utf-8")

    updated = store.update_turn("xiaoming", turn["turn_id"], {"assistant_text": "新回答"})

    assert updated is not None
    assert "tts_audio_base64" not in updated
    digest = hashlib.sha256(audio).hexdigest()
    assert updated["tts_audio_sha256"] == digest
    assert (tmp_path / updated["tts_audio_path"]).read_bytes() == audio
    backups = list(turns_path.parent.glob("turns.jsonl.*.bak"))
    assert len(backups) == 1
    backed_up = json.loads(backups[0].read_text(encoding="utf-8"))
    assert backed_up["tts_audio_base64"] == legacy_turn["tts_audio_base64"]


def test_rewrite_keeps_invalid_legacy_base64_verbatim(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    turn = store.append_turn(
        "xiaoming",
        user_text="旧问题",
        assistant_text="旧回答",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )
    turns_path = tmp_path / "conversations" / "xiaoming" / "turns.jsonl"
    legacy_turn = {
        **turn,
        "tts_audio_base64": "not-valid-base64!",
        "tts_audio_format": "pcm_s16le",
        "tts_sample_rate_hz": 16000,
        "tts_byte_length": 99,
    }
    turns_path.write_text(json.dumps(legacy_turn, ensure_ascii=False) + "\n", encoding="utf-8")

    updated = store.update_turn("xiaoming", turn["turn_id"], {"assistant_text": "新回答"})

    assert updated is not None
    assert updated["tts_audio_base64"] == "not-valid-base64!"
    assert "tts_audio_path" not in updated


def test_attach_tts_audio_rejects_invalid_payload(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    turn = store.append_turn(
        "xiaoming",
        user_text="讲故事",
        assistant_text="小恐龙回家了。",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )

    updated = store.attach_tts_audio(
        "xiaoming",
        turn["turn_id"],
        audio_bytes=b"",
        audio_format="pcm_s16le",
        sample_rate_hz=16000,
        byte_length=128,
    )

    assert updated is None
    loaded = store.list_turns("xiaoming", include_audio_payload=True)[0]
    assert "tts_audio_base64" not in loaded


def test_recent_turns_reads_from_tail_without_full_history_reader(
    monkeypatch, tmp_path
) -> None:
    store = ConversationStore(tmp_path)
    for sequence in range(1, 31):
        store.append_turn(
            "xiaoming",
            user_text=f"user {sequence}",
            assistant_text="x" * 5000,
            reply_source="llm",
            fallback_reason=None,
            safety_passed=True,
        )

    def fail_full_read(user_id: str) -> list[dict]:
        raise AssertionError(f"unexpected full read for {user_id}")

    monkeypatch.setattr(store, "_read_turns", fail_full_read)

    turns = store.list_recent_turns("xiaoming", limit=3)

    assert [turn["sequence"] for turn in turns] == [28, 29, 30]


def test_failed_atomic_rewrite_leaves_live_jsonl_unchanged(
    monkeypatch, tmp_path
) -> None:
    store = ConversationStore(tmp_path)
    turn = store.append_turn(
        "xiaoming",
        user_text="旧问题",
        assistant_text="旧回答",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )
    turns_path = tmp_path / "conversations" / "xiaoming" / "turns.jsonl"
    original_payload = turns_path.read_bytes()
    original_replace = type(turns_path).replace

    def fail_live_replace(path, target):
        if target == turns_path:
            raise OSError("simulated replace failure")
        return original_replace(path, target)

    monkeypatch.setattr(type(turns_path), "replace", fail_live_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        store.update_turn("xiaoming", turn["turn_id"], {"assistant_text": "新回答"})

    assert turns_path.read_bytes() == original_payload
    assert not list(turns_path.parent.glob(".turns.jsonl.*.tmp"))


def test_delete_turn_removes_only_matching_turn(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    first = store.append_turn(
        "xiaoming",
        user_text="第一轮",
        assistant_text="回答一",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )
    second = store.append_turn(
        "xiaoming",
        user_text="第二轮",
        assistant_text="回答二",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )

    assert store.delete_turn("xiaoming", first["turn_id"]) is True

    turns = store.list_turns("xiaoming")
    assert [turn["turn_id"] for turn in turns] == [second["turn_id"]]


def test_update_missing_turn_returns_none(tmp_path) -> None:
    store = ConversationStore(tmp_path)

    updated = store.update_turn("xiaoming", "turn_missing", {"user_text": "新问题"})

    assert updated is None


def test_store_sanitizes_unsafe_user_id_path_component(tmp_path) -> None:
    store = ConversationStore(tmp_path)

    turn = store.append_turn(
        "../xiao/ming?!小明",
        user_text="你好",
        assistant_text="你好呀",
        reply_source="llm",
        fallback_reason=None,
        safety_passed=True,
    )

    conversation_dir = tmp_path / "conversations" / "xiao_ming_小明"
    assert (conversation_dir / "turns.jsonl").exists()
    assert (conversation_dir / "meta.json").exists()
    assert ".." not in (conversation_dir / "turns.jsonl").relative_to(tmp_path).parts
    assert store.list_turns("../xiao/ming?!小明") == [turn]


def test_turn_reader_skips_empty_invalid_and_incomplete_jsonl_rows(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    conversation_dir = tmp_path / "conversations" / "xiaoming"
    conversation_dir.mkdir(parents=True)
    turns_path = conversation_dir / "turns.jsonl"
    valid_turn = {
        "turn_id": "turn_valid",
        "sequence": 10,
        "created_at": "2026-06-03T00:00:00+00:00",
        "user_text": "你好",
        "assistant_text": "你好呀",
        "reply_source": "llm",
        "fallback_reason": None,
        "safety_passed": True,
    }
    bool_sequence_turn = {
        **valid_turn,
        "turn_id": "turn_bool_sequence",
        "sequence": True,
    }
    turns_path.write_text(
        "\n"
        "not-json\n"
        '{"turn_id":"turn_missing_sequence"}\n'
        '{"sequence":9}\n'
        '{"turn_id":42,"sequence":"bad"}\n'
        f"{json.dumps(bool_sequence_turn, ensure_ascii=False)}\n"
        f"{json.dumps(valid_turn, ensure_ascii=False)}\n",
        encoding="utf-8",
    )

    assert store.list_turns("xiaoming") == [valid_turn]
    assert store.update_turn("xiaoming", "turn_missing_sequence", {"user_text": "新问题"}) is None
    assert store.delete_turn("xiaoming", "turn_missing_sequence") is False
