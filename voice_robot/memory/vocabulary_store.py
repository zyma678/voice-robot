import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class VocabularyStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def record_question(
        self,
        user_id: str,
        *,
        character: str,
        question: str,
        turn_id: str,
        trace_available: bool,
    ) -> dict:
        now = self._now()
        entries = self._read_index(user_id)
        current = next(
            (entry for entry in entries if entry.get("character") == character),
            None,
        )
        if current is None:
            current = {
                "character": character,
                "first_asked_at": now,
                "last_asked_at": now,
                "ask_count": 0,
                "latest_question": "",
                "trace_available": bool(trace_available),
            }
            entries.append(current)

        current["last_asked_at"] = now
        current["ask_count"] = int(current.get("ask_count", 0)) + 1
        current["latest_question"] = question
        current["trace_available"] = bool(trace_available)
        self._write_index(user_id, entries)

        event = {
            "event_id": f"vocab_{uuid4().hex[:8]}",
            "character": character,
            "question": question,
            "created_at": now,
            "turn_id": turn_id,
            "trace_available": bool(trace_available),
        }
        history_path = self._history_path(user_id)
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")
        return dict(current)

    def list_vocabulary(self, user_id: str) -> list[dict]:
        return sorted(
            self._read_index(user_id),
            key=lambda entry: (
                str(entry.get("last_asked_at", "")),
                str(entry.get("character", "")),
            ),
            reverse=True,
        )

    def list_history(self, user_id: str, *, limit: int = 100) -> list[dict]:
        if limit <= 0:
            return []
        return self._read_history(user_id)[-limit:]

    def _read_index(self, user_id: str) -> list[dict]:
        path = self._index_path(user_id)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return [entry for entry in payload if self._is_valid_index_entry(entry)]

    def _write_index(self, user_id: str, entries: list[dict]) -> None:
        path = self._index_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = sorted(entries, key=lambda entry: str(entry.get("character", "")))
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(path)

    def _read_history(self, user_id: str) -> list[dict]:
        path = self._history_path(user_id)
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
            if self._is_valid_history_event(event):
                events.append(event)
        return events

    def _vocabulary_dir(self, user_id: str) -> Path:
        return self.root / "vocabulary" / self._safe_component(user_id)

    def _index_path(self, user_id: str) -> Path:
        return self._vocabulary_dir(user_id) / "index.json"

    def _history_path(self, user_id: str) -> Path:
        return self._vocabulary_dir(user_id) / "history.jsonl"

    @staticmethod
    def _is_valid_index_entry(entry: object) -> bool:
        return (
            isinstance(entry, dict)
            and isinstance(entry.get("character"), str)
            and isinstance(entry.get("first_asked_at"), str)
            and isinstance(entry.get("last_asked_at"), str)
            and type(entry.get("ask_count")) is int
            and isinstance(entry.get("latest_question"), str)
            and type(entry.get("trace_available")) is bool
        )

    @staticmethod
    def _is_valid_history_event(event: object) -> bool:
        return (
            isinstance(event, dict)
            and isinstance(event.get("event_id"), str)
            and isinstance(event.get("character"), str)
            and isinstance(event.get("question"), str)
            and isinstance(event.get("created_at"), str)
            and isinstance(event.get("turn_id"), str)
            and type(event.get("trace_available")) is bool
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", value).strip("_") or "item"
