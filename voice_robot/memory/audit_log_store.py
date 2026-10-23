import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class AuditLogStore:
    _REQUIRED_EVENT_FIELDS = {
        "event_id",
        "created_at",
        "event_type",
        "turn_id",
        "payload",
    }
    _PATCH_FIELDS = {"event_type", "turn_id", "payload"}

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def append_event(
        self,
        user_id: str,
        *,
        event_type: str,
        payload: dict,
        turn_id: str = "",
    ) -> dict:
        self._validate_event_type(event_type)
        self._validate_payload(payload)
        self._validate_turn_id(turn_id)

        event = {
            "event_id": f"evt_{uuid4().hex[:8]}",
            "created_at": self._now(),
            "event_type": event_type,
            "turn_id": turn_id,
            "payload": payload,
        }

        path = self._events_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def list_events(self, user_id: str, *, limit: int = 100) -> list[dict]:
        if limit <= 0:
            return []
        return self._read_events(user_id)[-limit:]

    def update_event(self, user_id: str, event_id: str, patch: dict) -> dict | None:
        events = self._read_events(user_id)
        sanitized_patch = self._sanitize_patch(patch)
        updated_event = None

        for index, event in enumerate(events):
            if event.get("event_id") != event_id:
                continue
            updated_event = {
                **event,
                **sanitized_patch,
                "event_id": event["event_id"],
                "created_at": event["created_at"],
            }
            events[index] = updated_event
            break

        if updated_event is None:
            return None

        self._rewrite_events(user_id, events)
        return updated_event

    def delete_event(self, user_id: str, event_id: str) -> bool:
        events = self._read_events(user_id)
        remaining_events = [
            event for event in events if event.get("event_id") != event_id
        ]
        if len(remaining_events) == len(events):
            return False

        self._rewrite_events(user_id, remaining_events)
        return True

    def _read_events(self, user_id: str) -> list[dict]:
        path = self._events_path(user_id)
        if not path.exists():
            return []

        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if self._is_valid_event(event):
                events.append(event)
        return events

    def _rewrite_events(self, user_id: str, events: list[dict]) -> None:
        path = self._events_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup_path = path.with_name(f"{path.name}.{self._backup_stamp()}.bak")
            path.replace(backup_path)

        path.write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
            encoding="utf-8",
        )

    def _events_path(self, user_id: str) -> Path:
        return self.root / "audit" / self._safe_component(user_id) / "events.jsonl"

    @classmethod
    def _sanitize_patch(cls, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise TypeError("patch must be a dict")

        sanitized = {}
        for field in cls._PATCH_FIELDS:
            if field not in patch:
                continue
            value = patch[field]
            if field == "event_type":
                cls._validate_event_type(value)
            elif field == "turn_id":
                cls._validate_turn_id(value)
            elif field == "payload":
                cls._validate_payload(value)
            sanitized[field] = value
        return sanitized

    @classmethod
    def _is_valid_event(cls, event: object) -> bool:
        if not isinstance(event, dict):
            return False
        if not cls._REQUIRED_EVENT_FIELDS.issubset(event):
            return False
        return (
            isinstance(event["event_id"], str)
            and isinstance(event["created_at"], str)
            and isinstance(event["event_type"], str)
            and isinstance(event["turn_id"], str)
            and isinstance(event["payload"], dict)
        )

    @staticmethod
    def _validate_event_type(event_type: object) -> None:
        if not isinstance(event_type, str):
            raise TypeError("event_type must be a str")

    @staticmethod
    def _validate_turn_id(turn_id: object) -> None:
        if not isinstance(turn_id, str):
            raise TypeError("turn_id must be a str")

    @staticmethod
    def _validate_payload(payload: object) -> None:
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _backup_stamp() -> str:
        return datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", value).strip("_") or "item"
