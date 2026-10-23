import re
from pathlib import Path

from voice_robot.memory.file_store import FileMemoryStore


class FileMemoryRetriever:
    def __init__(self, root: Path) -> None:
        self.store = FileMemoryStore(root)

    def search(self, query: str, user_id: str, top_k: int = 3) -> list[dict]:
        keywords = self._keywords(query)
        scored = []
        for entry in self.store.load_index(user_id):
            if entry.get("status") == "deleted":
                continue
            score = self._safe_score(entry, keywords)
            if score is None:
                continue
            scored.append((score, entry))
        ranked = [
            entry
            for score, entry in sorted(
                scored,
                key=lambda item: (
                    item[0],
                    item[1].get("created_at", ""),
                    item[1].get("id", ""),
                ),
                reverse=True,
            )
            if score > 0
        ]
        return ranked[:top_k]

    def _safe_score(self, entry: dict, keywords: set[str]) -> float | None:
        try:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            tags = entry.get("tags", [])
            importance = float(entry.get("importance", 0.0))

            if not isinstance(title, str) or not isinstance(summary, str):
                return None
            if not isinstance(tags, list) or not all(
                isinstance(tag, str) for tag in tags
            ):
                return None

            normalized_entry = {
                **entry,
                "title": title,
                "summary": summary,
                "tags": tags,
                "importance": importance,
            }
            return self._score(normalized_entry, keywords)
        except (TypeError, ValueError):
            return None

    def _score(self, entry: dict, keywords: set[str]) -> float:
        searchable = " ".join(
            [
                entry.get("title", ""),
                entry.get("summary", ""),
                " ".join(entry.get("tags", [])),
            ]
        ).lower()
        keyword_hits = sum(1 for keyword in keywords if keyword in searchable)
        if keyword_hits == 0:
            return 0.0
        keyword_score = keyword_hits / max(len(keywords), 1)
        score = keyword_score * 0.7 + float(entry.get("importance", 0.0)) * 0.3
        if entry.get("status") == "resolved":
            score *= 0.5
        return score

    @staticmethod
    def _keywords(query: str) -> set[str]:
        return {
            part.lower()
            for part in re.findall(r"[\w\u4e00-\u9fff]+", query)
            if part.strip()
        }
