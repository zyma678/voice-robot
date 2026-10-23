from voice_robot.memory.vocabulary_store import VocabularyStore


def test_vocabulary_store_records_index_and_history(tmp_path) -> None:
    store = VocabularyStore(tmp_path)

    first = store.record_question(
        "xiaoming",
        character="十",
        question="十字怎么写",
        turn_id="turn_1",
        trace_available=True,
    )
    second = store.record_question(
        "xiaoming",
        character="十",
        question="十 字怎么写",
        turn_id="turn_2",
        trace_available=True,
    )

    entries = store.list_vocabulary("xiaoming")
    history = store.list_history("xiaoming", limit=10)

    assert first["character"] == "十"
    assert second["ask_count"] == 2
    assert entries == [
        {
            "character": "十",
            "first_asked_at": entries[0]["first_asked_at"],
            "last_asked_at": entries[0]["last_asked_at"],
            "ask_count": 2,
            "latest_question": "十 字怎么写",
            "trace_available": True,
        }
    ]
    assert [event["question"] for event in history] == ["十字怎么写", "十 字怎么写"]
    assert all(event["character"] == "十" for event in history)
    assert all(event["trace_available"] is True for event in history)


def test_vocabulary_history_limit_returns_recent_items(tmp_path) -> None:
    store = VocabularyStore(tmp_path)

    for index, character in enumerate(["一", "二", "三"]):
        store.record_question(
            "xiaoming",
            character=character,
            question=f"{character}字怎么写",
            turn_id=f"turn_{index}",
            trace_available=True,
        )

    history = store.list_history("xiaoming", limit=2)

    assert [event["character"] for event in history] == ["二", "三"]
