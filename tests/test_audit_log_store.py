import json

from voice_robot.memory import AuditLogStore


def test_append_and_list_audit_events(tmp_path):
    store = AuditLogStore(tmp_path)

    first = store.append_event(
        "user-1",
        event_type="message.sent",
        payload={"text": "你好"},
        turn_id="turn_1",
    )
    second = store.append_event(
        "user-1",
        event_type="tool.called",
        payload={"name": "search"},
    )

    events = store.list_events("user-1", limit=10)

    assert events == [first, second]
    assert first["event_id"].startswith("evt_")
    assert second["event_id"].startswith("evt_")
    assert first["payload"] == {"text": "你好"}
    assert second["payload"] == {"name": "search"}


def test_update_and_delete_audit_event(tmp_path):
    store = AuditLogStore(tmp_path)
    event = store.append_event(
        "user-1",
        event_type="message.sent",
        payload={"text": "old"},
        turn_id="turn_1",
    )
    path = tmp_path / "audit" / "user-1" / "events.jsonl"

    updated = store.update_event(
        "user-1",
        event["event_id"],
        {
            "event_type": "message.updated",
            "payload": {"text": "new"},
            "event_id": "evt_should_not_replace",
            "created_at": "should-not-replace",
        },
    )

    assert updated is not None
    assert updated["event_id"] == event["event_id"]
    assert updated["created_at"] == event["created_at"]
    assert updated["event_type"] == "message.updated"
    assert updated["payload"] == {"text": "new"}
    update_backups = sorted(path.parent.glob("events.jsonl.*.bak"))
    assert len(update_backups) >= 1
    assert any(event in _read_jsonl(backup) for backup in update_backups)

    assert store.delete_event("user-1", event["event_id"]) is True
    assert store.list_events("user-1") == []
    delete_backups = sorted(path.parent.glob("events.jsonl.*.bak"))
    assert len(delete_backups) >= 2
    assert any(updated in _read_jsonl(backup) for backup in delete_backups)


def test_corrupt_audit_line_is_skipped(tmp_path):
    store = AuditLogStore(tmp_path)
    event = store.append_event(
        "user-1",
        event_type="message.sent",
        payload={"text": "ok"},
    )
    path = tmp_path / "audit" / "user-1" / "events.jsonl"
    path.write_text(
        json.dumps(event, ensure_ascii=False) + "\nnot-json\n",
        encoding="utf-8",
    )

    assert store.list_events("user-1") == [event]


def test_unsafe_user_id_path_is_normalized(tmp_path):
    store = AuditLogStore(tmp_path)

    event = store.append_event("../张 三!?", event_type="login", payload={})

    assert (tmp_path / "audit" / "张_三" / "events.jsonl").exists()
    assert store.list_events("../张 三!?") == [event]


def test_invalid_audit_lines_are_skipped(tmp_path):
    store = AuditLogStore(tmp_path)
    valid = store.append_event("user-1", event_type="ok", payload={"safe": True})
    path = tmp_path / "audit" / "user-1" / "events.jsonl"
    invalid_lines = [
        "",
        json.dumps(["not", "dict"]),
        json.dumps({"event_id": "evt_missing"}),
        json.dumps(
            {
                "event_id": "evt_bad_payload",
                "created_at": valid["created_at"],
                "event_type": "bad",
                "turn_id": "",
                "payload": [],
            }
        ),
        json.dumps(
            {
                "event_id": "evt_bad_type",
                "created_at": valid["created_at"],
                "event_type": 123,
                "turn_id": "",
                "payload": {},
            }
        ),
    ]
    path.write_text(
        "\n".join(
            [json.dumps(valid, ensure_ascii=False), *invalid_lines]
        ),
        encoding="utf-8",
    )

    assert store.list_events("user-1") == [valid]


def test_append_rejects_invalid_payload_and_event_type(tmp_path):
    store = AuditLogStore(tmp_path)

    try:
        store.append_event("user-1", event_type="ok", payload=[])
    except TypeError as exc:
        assert "payload" in str(exc)
    else:
        raise AssertionError("append_event should reject non-dict payload")

    try:
        store.append_event("user-1", event_type=123, payload={})
    except TypeError as exc:
        assert "event_type" in str(exc)
    else:
        raise AssertionError("append_event should reject non-str event_type")


def test_update_rejects_invalid_patch_field_types(tmp_path):
    store = AuditLogStore(tmp_path)
    event = store.append_event("user-1", event_type="ok", payload={})

    try:
        store.update_event("user-1", event["event_id"], {"payload": []})
    except TypeError as exc:
        assert "payload" in str(exc)
    else:
        raise AssertionError("update_event should reject non-dict payload")

    try:
        store.update_event("user-1", event["event_id"], {"turn_id": 123})
    except TypeError as exc:
        assert "turn_id" in str(exc)
    else:
        raise AssertionError("update_event should reject non-str turn_id")


def test_missing_event_update_delete_are_noops(tmp_path):
    store = AuditLogStore(tmp_path)
    store.append_event("user-1", event_type="ok", payload={})

    assert store.update_event("user-1", "evt_missing", {"event_type": "new"}) is None
    assert store.delete_event("user-1", "evt_missing") is False


def test_list_events_returns_latest_limit_in_write_order(tmp_path):
    store = AuditLogStore(tmp_path)
    events = [
        store.append_event("user-1", event_type=f"type.{index}", payload={"i": index})
        for index in range(5)
    ]

    assert store.list_events("user-1", limit=3) == events[-3:]
    assert store.list_events("user-1", limit=0) == []
    assert store.list_events("user-1", limit=-1) == []


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
