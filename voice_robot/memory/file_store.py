import json
import re
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4


class FileMemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def save_profile(self, user_id: str, profile: dict) -> None:
        safe_user_id = self._safe_component(user_id)
        profile["user_id"] = safe_user_id
        path = self._profile_path(safe_user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def load_profile(self, user_id: str) -> dict:
        safe_user_id = self._safe_component(user_id)
        path = self._profile_path(safe_user_id)
        if not path.exists():
            return {"user_id": safe_user_id}
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile["user_id"] = safe_user_id
        return profile

    def patch_profile(self, user_id: str, patch: dict) -> dict:
        safe_user_id = self._safe_component(user_id)
        profile = self.load_profile(safe_user_id)
        for key, value in patch.items():
            if key == "user_id":
                continue
            if isinstance(value, list):
                profile[key] = self._dedupe_list(value)
            else:
                profile[key] = value
        profile["user_id"] = safe_user_id
        profile["updated_at"] = self._now()
        self.save_profile(safe_user_id, profile)
        return profile

    def save_memory(
        self,
        user_id: str,
        *,
        category: str,
        title: str,
        content: str,
        tags: list[str],
        importance: float,
    ) -> str:
        safe_user_id = self._safe_component(user_id)
        safe_category = self._safe_component(category)
        memory_id = f"mem_{uuid4().hex[:8]}"
        now = self._now()
        memory_dir = self.root / "memories" / safe_user_id / safe_category
        memory_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{memory_id}_{self._safe_filename(title)}.md"
        memory_path = memory_dir / filename
        memory_path.write_text(
            self._render_memory_markdown(
                memory_id=memory_id,
                category=safe_category,
                importance=importance,
                tags=tags,
                status="active",
                confidence=1.0,
                source_turn_id="",
                title=title,
                summary=content,
            ),
            encoding="utf-8",
        )

        index_path = self._index_path(safe_user_id)
        entries = self._load_json_list(index_path)
        entries.append(
            {
                "id": memory_id,
                "category": safe_category,
                "title": title,
                "summary": content,
                "tags": tags,
                "importance": importance,
                "status": "active",
                "confidence": 1.0,
                "created_at": now,
                "updated_at": now,
                "last_accessed": now,
                "source_turn_id": "",
                "path": str(memory_path.relative_to(self.root)),
            }
        )
        self._write_json_list(index_path, entries)
        return memory_id

    def load_index(self, user_id: str) -> list[dict]:
        safe_user_id = self._safe_component(user_id)
        return self._load_json_list(self._index_path(safe_user_id))

    def apply_index_snapshot(self, user_id: str, entries: list[dict]) -> list[dict]:
        """Replace the memory index with ``entries`` and (re)render each .md body.

        Used by control-plane sync to land merged memories that may have
        originated on a device (whose .md bodies don't exist on this side). The
        canonical path is recomputed so it always matches save_memory's layout.
        """
        safe_user_id = self._safe_component(user_id)
        normalized: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            memory_id = str(entry.get("id", "")).strip()
            if not memory_id:
                continue
            category = self._safe_component(str(entry.get("category", "") or "preference"))
            title = str(entry.get("title", "") or "用户记忆")
            tags = entry.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            memory_dir = self.root / "memories" / safe_user_id / category
            memory_dir.mkdir(parents=True, exist_ok=True)
            memory_path = memory_dir / f"{memory_id}_{self._safe_filename(title)}.md"
            memory_path.write_text(
                self._render_memory_markdown(
                    memory_id=memory_id,
                    category=category,
                    importance=entry.get("importance", 0.0),
                    tags=tags,
                    status=str(entry.get("status", "active") or "active"),
                    confidence=entry.get("confidence", 1.0),
                    source_turn_id=str(entry.get("source_turn_id", "") or ""),
                    title=title,
                    summary=str(entry.get("summary", "") or ""),
                ),
                encoding="utf-8",
            )
            merged_entry = dict(entry)
            merged_entry["category"] = category
            merged_entry["tags"] = tags
            merged_entry["path"] = str(memory_path.relative_to(self.root))
            normalized.append(merged_entry)
        self._write_json_list(self._index_path(safe_user_id), normalized)
        return normalized

    def update_memory(self, user_id: str, memory_id: str, patch: dict) -> dict | None:
        safe_user_id = self._safe_component(user_id)
        index_path = self._index_path(safe_user_id)
        entries = self._load_json_list(index_path)
        allowed_fields = {
            "title",
            "content",
            "tags",
            "importance",
            "status",
            "confidence",
            "source_turn_id",
        }

        for entry in entries:
            if entry.get("id") != memory_id:
                continue

            if not self._is_valid_memory_entry(entry):
                return None
            memory_path = self._resolve_memory_path(safe_user_id, entry["path"])
            if memory_path is None:
                return None

            for key, value in patch.items():
                if key not in allowed_fields:
                    continue
                if key == "content":
                    entry["summary"] = value
                elif key == "tags":
                    entry["tags"] = self._dedupe_list(value)
                else:
                    entry[key] = value

            entry["updated_at"] = self._now()
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            memory_path.write_text(
                self._render_memory_markdown(
                    memory_id=entry["id"],
                    category=entry["category"],
                    importance=entry["importance"],
                    tags=entry.get("tags", []),
                    status=entry.get("status", "active"),
                    confidence=entry.get("confidence", 1.0),
                    source_turn_id=entry.get("source_turn_id", ""),
                    title=entry["title"],
                    summary=entry.get("summary", ""),
                ),
                encoding="utf-8",
            )
            self._write_json_list(index_path, entries)
            return entry
        return None

    def delete_memory(self, user_id: str, memory_id: str) -> bool:
        safe_user_id = self._safe_component(user_id)
        index_path = self._index_path(safe_user_id)
        entries = self._load_json_list(index_path)

        for index, entry in enumerate(entries):
            if entry.get("id") != memory_id:
                continue

            if not self._is_valid_memory_entry(entry):
                return False
            memory_path = self._resolve_memory_path(safe_user_id, entry["path"])
            if memory_path is None:
                return False

            if memory_path.exists():
                memory_path.unlink()

            del entries[index]
            self._write_json_list(index_path, entries)
            return True
        return False

    @staticmethod
    def _load_json_list(path: Path) -> list[dict]:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json_list(path: Path, entries: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as tmp:
            json.dump(entries, tmp, ensure_ascii=False, indent=2, sort_keys=True)
            temp_path = Path(tmp.name)
        temp_path.replace(path)

    @staticmethod
    def _dedupe_list(values: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = str(value).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    @staticmethod
    def _safe_filename(value: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value).strip("_") or "memory"

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", value).strip("_") or "item"

    def _profile_path(self, safe_user_id: str) -> Path:
        return self.root / "profiles" / f"{safe_user_id}.json"

    def _index_path(self, safe_user_id: str) -> Path:
        return self.root / "memories" / safe_user_id / "index.json"

    def _resolve_memory_path(self, safe_user_id: str, relative_path: str) -> Path | None:
        if not relative_path:
            return None
        candidate = (self.root / relative_path).resolve()
        root = self.root.resolve()
        user_memory_root = (self.root / "memories" / safe_user_id).resolve()
        if not candidate.is_relative_to(root):
            return None
        if not candidate.is_relative_to(user_memory_root):
            return None
        return candidate

    @staticmethod
    def _is_valid_memory_entry(entry: dict) -> bool:
        required_text_fields = {"id", "category", "title", "path"}
        for field in required_text_fields:
            value = entry.get(field)
            if value is None:
                return False
            if isinstance(value, str) and not value.strip():
                return False

        importance = entry.get("importance")
        return importance is not None

    @staticmethod
    def _render_memory_markdown(
        *,
        memory_id: str,
        category: str,
        importance: float,
        tags: list[str],
        status: str,
        confidence: float,
        source_turn_id: str,
        title: str,
        summary: str,
    ) -> str:
        return (
            "---\n"
            f'id: "{memory_id}"\n'
            f'category: "{category}"\n'
            f"importance: {importance}\n"
            f"tags: {json.dumps(tags, ensure_ascii=False)}\n"
            f'status: "{FileMemoryStore._escape_frontmatter_value(status)}"\n'
            f"confidence: {confidence}\n"
            f'source_turn_id: "{FileMemoryStore._escape_frontmatter_value(source_turn_id)}"\n'
            "---\n"
            f"# {title}\n\n"
            f"{summary}\n"
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _escape_frontmatter_value(value: str) -> str:
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace("\n", " ")
            .replace('"', '\\"')
        )
