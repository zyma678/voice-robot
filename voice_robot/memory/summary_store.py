import json
import re
from datetime import UTC, datetime
from pathlib import Path


class SummaryStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def save_summary(
        self,
        user_id: str,
        *,
        start_sequence: int,
        end_sequence: int,
        content: str,
        summary_source: str,
    ) -> dict:
        summary_id = self._summary_id(start_sequence, end_sequence)
        path = self._summary_path(user_id, start_sequence, end_sequence)
        path.parent.mkdir(parents=True, exist_ok=True)

        path.write_text(
            self._format_markdown(
                summary_id=summary_id,
                user_id=user_id,
                start_sequence=start_sequence,
                end_sequence=end_sequence,
                summary_source=summary_source,
                content=content,
            ),
            encoding="utf-8",
        )

        entry = {
            "summary_id": summary_id,
            "user_id": user_id,
            "start_sequence": start_sequence,
            "end_sequence": end_sequence,
            "created_at": self._now(),
            "summary_source": summary_source,
            "path": path.relative_to(self.root).as_posix(),
        }
        summaries = [
            summary
            for summary in self._read_index(user_id)
            if summary.get("summary_id") != summary_id
        ]
        summaries.append(entry)
        self._write_index(user_id, summaries)
        return entry

    def list_summaries(self, user_id: str) -> list[dict]:
        return sorted(
            self._read_index(user_id),
            key=lambda summary: summary.get("start_sequence", 0),
        )

    def list_recent_summaries(self, user_id: str, *, limit: int = 3) -> list[dict]:
        if limit <= 0:
            return []
        summaries = sorted(
            self._read_index(user_id),
            key=lambda summary: summary.get("end_sequence", 0),
        )
        return summaries[-limit:]

    def apply_snapshot(
        self, user_id: str, entries: list[dict], contents: dict
    ) -> list[dict]:
        """Replace the summary index with ``entries`` and rewrite each .md body
        from ``contents`` (keyed by summary_id). Preserves entry fields (notably
        created_at / start/end) so merge identity stays stable across syncs."""
        if not isinstance(contents, dict):
            contents = {}
        safe_user_id = self._safe_component(user_id)
        out: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            summary_id = str(entry.get("summary_id", "")).strip()
            if not summary_id:
                continue
            try:
                start = int(entry.get("start_sequence"))
                end = int(entry.get("end_sequence"))
            except (TypeError, ValueError):
                continue
            source = str(entry.get("summary_source", "local") or "local")
            content = str(contents.get(summary_id, "") or "")
            path = self._summary_path(user_id, start, end)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                self._format_markdown(
                    summary_id=summary_id,
                    user_id=safe_user_id,
                    start_sequence=start,
                    end_sequence=end,
                    summary_source=source,
                    content=content,
                ),
                encoding="utf-8",
            )
            merged_entry = dict(entry)
            merged_entry["user_id"] = safe_user_id
            merged_entry["path"] = path.relative_to(self.root).as_posix()
            out.append(merged_entry)
        self._write_index(user_id, out)
        return out

    def load_summary_content(self, user_id: str, summary_id: str) -> str | None:
        summary = self.get_summary(user_id, summary_id)
        if summary is None:
            return None
        path = self.root / summary["path"]
        if not path.exists():
            return None
        return self._extract_content(path.read_text(encoding="utf-8"))

    def get_summary(self, user_id: str, summary_id: str) -> dict | None:
        for summary in self._read_index(user_id):
            if summary.get("summary_id") == summary_id:
                return summary
        return None

    def update_summary(
        self,
        user_id: str,
        summary_id: str,
        *,
        content: str,
        summary_source: str,
    ) -> dict | None:
        summaries = self._read_index(user_id)
        updated = None
        for index, summary in enumerate(summaries):
            if summary.get("summary_id") != summary_id:
                continue
            updated = {
                **summary,
                "summary_source": summary_source,
            }
            path = self.root / updated["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                self._format_markdown(
                    summary_id=summary_id,
                    user_id=updated["user_id"],
                    start_sequence=updated["start_sequence"],
                    end_sequence=updated["end_sequence"],
                    summary_source=summary_source,
                    content=content,
                ),
                encoding="utf-8",
            )
            summaries[index] = updated
            break

        if updated is None:
            return None

        self._write_index(user_id, summaries)
        return updated

    def delete_summary(self, user_id: str, summary_id: str) -> bool:
        summaries = self._read_index(user_id)
        deleted = None
        remaining = []
        for summary in summaries:
            if summary.get("summary_id") == summary_id:
                deleted = summary
                continue
            remaining.append(summary)

        if deleted is None:
            return False

        path = self.root / deleted["path"]
        if path.exists():
            path.unlink()
        self._write_index(user_id, remaining)
        return True

    def _summary_dir(self, user_id: str) -> Path:
        return self.root / "summaries" / self._safe_component(user_id)

    def _summary_path(self, user_id: str, start_sequence: int, end_sequence: int) -> Path:
        return (
            self._summary_dir(user_id)
            / f"summary_{start_sequence:06d}_{end_sequence:06d}.md"
        )

    def _index_path(self, user_id: str) -> Path:
        return self._summary_dir(user_id) / "index.json"

    def _read_index(self, user_id: str) -> list[dict]:
        path = self._index_path(user_id)
        if not path.exists():
            return []
        try:
            summaries = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        if not isinstance(summaries, list):
            return []
        return [summary for summary in summaries if isinstance(summary, dict)]

    def _write_index(self, user_id: str, summaries: list[dict]) -> None:
        path = self._index_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = sorted(
            summaries,
            key=lambda summary: summary.get("start_sequence", 0),
        )
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(path)

    @staticmethod
    def _format_markdown(
        *,
        summary_id: str,
        user_id: str,
        start_sequence: int,
        end_sequence: int,
        summary_source: str,
        content: str,
    ) -> str:
        return (
            "---\n"
            f'summary_id: "{summary_id}"\n'
            f'user_id: "{user_id}"\n'
            f"start_sequence: {start_sequence}\n"
            f"end_sequence: {end_sequence}\n"
            f'summary_source: "{summary_source}"\n'
            "---\n\n"
            f"# 对话摘要 {start_sequence}-{end_sequence}\n\n"
            f"{content}\n"
        )

    @staticmethod
    def _extract_content(markdown: str) -> str:
        marker = "\n\n"
        body = markdown.split(marker, 1)[1] if marker in markdown else markdown
        lines = body.splitlines()
        if lines and lines[0].startswith("# "):
            lines = lines[1:]
        return "\n".join(lines).strip()

    @staticmethod
    def _summary_id(start_sequence: int, end_sequence: int) -> str:
        return f"sum_{start_sequence:06d}_{end_sequence:06d}"

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", value).strip("_") or "item"


def build_local_summary(turns: list[dict]) -> str:
    if not turns:
        return "- 暂无可摘要对话。"

    selected_turns = turns[:20]
    sequences = [
        turn.get("sequence")
        for turn in selected_turns
        if type(turn.get("sequence")) is int
    ]
    start_sequence = min(sequences) if sequences else 1
    end_sequence = max(sequences) if sequences else len(selected_turns)
    lines = [f"- 对话范围：{start_sequence}-{end_sequence}。"]

    for turn in selected_turns:
        sequence = turn.get("sequence", "?")
        user_text = _compact_text(str(turn.get("user_text", "")))
        assistant_text = _compact_text(str(turn.get("assistant_text", "")))
        if user_text:
            lines.append(f"- 第 {sequence} 轮用户：{user_text}")
        if assistant_text:
            lines.append(f"- 第 {sequence} 轮助手：{assistant_text}")

    return "\n".join(lines)


def _compact_text(value: str, limit: int = 80) -> str:
    compacted = re.sub(r"\s+", " ", value).strip()
    if len(compacted) <= limit:
        return compacted
    return compacted[:limit].rstrip() + "..."
