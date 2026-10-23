import json
from pathlib import Path

from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.file_retriever import FileMemoryRetriever
from voice_robot.memory.file_store import FileMemoryStore
from voice_robot.memory.summary_store import SummaryStore


class MemoryContextBuilder:
    def __init__(
        self,
        root: Path | str,
        *,
        max_chars: int = 3200,
        recent_turn_limit: int = 8,
        summary_limit: int = 2,
        summary_item_max_chars: int = 1000,
        turn_item_max_chars: int = 500,
        profile_item_max_chars: int = 300,
        profile_max_items: int = 12,
        profile_section_max_chars: int = 1200,
        structured_item_max_chars: int = 500,
        structured_max_items: int = 2,
        structured_section_max_chars: int = 1600,
    ) -> None:
        self.root = Path(root)
        self.max_chars = max(0, int(max_chars))
        self.recent_turn_limit = max(1, int(recent_turn_limit))
        self.summary_limit = max(1, int(summary_limit))
        self.summary_item_max_chars = max(1, int(summary_item_max_chars))
        self.turn_item_max_chars = max(1, int(turn_item_max_chars))
        self.profile_item_max_chars = max(1, int(profile_item_max_chars))
        self.profile_max_items = max(1, int(profile_max_items))
        self.profile_section_max_chars = max(1, int(profile_section_max_chars))
        self.structured_item_max_chars = max(1, int(structured_item_max_chars))
        self.structured_max_items = max(1, int(structured_max_items))
        self.structured_section_max_chars = max(1, int(structured_section_max_chars))
        self.store = FileMemoryStore(self.root)
        self.retriever = FileMemoryRetriever(self.root)
        self.conversation_store = ConversationStore(self.root)
        self.summary_store = SummaryStore(self.root)

    def build(
        self,
        user_id: str,
        query: str,
        *,
        structured_memories: list[dict] | None = None,
    ) -> str:
        profile = self._profile_section(user_id)
        summaries = self._summaries_section(user_id)
        structured = self._structured_memories_section(
            user_id,
            query,
            memories=structured_memories,
        )
        recent = self._recent_turns_section(user_id)
        sections = [profile, summaries, structured, recent]
        context = "\n\n".join(sections)
        if self.max_chars <= 0:
            return ""
        if len(context) <= self.max_chars:
            return context
        compact_sections = self._compact_sections_for_budget(
            profile=profile,
            summaries=summaries,
            structured=structured,
            recent=recent,
            max_chars=self.max_chars,
        )
        context = "\n\n".join(compact_sections)
        if len(context) <= self.max_chars:
            return context
        return context[: self.max_chars].rstrip()

    def _profile_section(self, user_id: str) -> str:
        lines = ["【用户画像】"]
        try:
            profile = self.store.load_profile(user_id)
        except Exception:
            profile = {}
        if not isinstance(profile, dict):
            profile = {}

        items: list[str] = []
        for key, value in profile.items():
            if key == "user_id":
                continue
            rendered = self._render_value(value)
            if rendered:
                item = f"- {key}: {rendered}"
                items.append(self._clip(item, self.profile_item_max_chars))
            if len(items) >= self.profile_max_items:
                break

        if not items:
            items = ["- 暂无用户画像"]
        lines.extend(items)
        return self._join_with_section_limit(lines, self.profile_section_max_chars)

    def _summaries_section(self, user_id: str) -> str:
        lines = ["【长期摘要】"]
        try:
            recent_summaries = self.summary_store.list_recent_summaries(
                user_id,
                limit=self.summary_limit,
            )
        except Exception:
            recent_summaries = []

        if not isinstance(recent_summaries, list):
            recent_summaries = []
        rendered_any = False
        for summary in recent_summaries:
            if not isinstance(summary, dict):
                continue
            summary_id = summary.get("summary_id")
            if not isinstance(summary_id, str) or not summary_id.strip():
                continue
            try:
                content = self.summary_store.load_summary_content(user_id, summary_id)
            except Exception:
                content = None
            compact = self._clip(content or "", self.summary_item_max_chars)
            if not compact:
                continue
            start_sequence = summary.get("start_sequence", "?")
            end_sequence = summary.get("end_sequence", "?")
            lines.append(f"- {start_sequence}-{end_sequence}: {compact}")
            rendered_any = True

        if not rendered_any:
            lines.append("- 暂无长期摘要")
        return "\n".join(lines)

    def _structured_memories_section(
        self,
        user_id: str,
        query: str,
        *,
        memories: list[dict] | None = None,
    ) -> str:
        lines = ["【结构化记忆】"]
        if memories is None:
            try:
                memories = self.retriever.search(
                    query,
                    user_id,
                    top_k=self.structured_max_items,
                )
            except Exception:
                memories = []
        if not isinstance(memories, list):
            memories = []

        rendered_any = False
        for memory in memories[: self.structured_max_items]:
            if not isinstance(memory, dict):
                continue
            title = str(memory.get("title", "")).strip()
            summary = str(memory.get("summary", "")).strip()
            tags = memory.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            tag_text = "、".join(str(tag).strip() for tag in tags if str(tag).strip())
            pieces = [part for part in [title, summary, tag_text] if part]
            if not pieces:
                continue
            lines.append(
                self._clip(
                    f"- {'；'.join(pieces)}",
                    self.structured_item_max_chars,
                )
            )
            rendered_any = True

        if not rendered_any:
            lines.append("- 暂无结构化记忆")
        return self._join_with_section_limit(lines, self.structured_section_max_chars)

    def _recent_turns_section(self, user_id: str) -> str:
        lines = [f"【最近 {self.recent_turn_limit} 轮对话】"]
        try:
            turns = self.conversation_store.list_recent_turns(
                user_id,
                limit=self.recent_turn_limit,
            )
        except Exception:
            turns = []
        if not isinstance(turns, list):
            turns = []

        rendered_any = False
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            sequence = turn.get("sequence", "?")
            user_text = self._clip(str(turn.get("user_text", "")), self.turn_item_max_chars)
            assistant_text = self._clip(
                str(turn.get("assistant_text", "")),
                self.turn_item_max_chars,
            )
            if user_text:
                lines.append(f"- 第{sequence}轮用户: {user_text}")
                rendered_any = True
            if assistant_text:
                lines.append(f"- 第{sequence}轮助手: {assistant_text}")
                rendered_any = True

        if not rendered_any:
            lines.append("- 暂无最近对话")
        return "\n".join(lines)

    @staticmethod
    def _clip(value: str, limit: int) -> str:
        compact = " ".join(str(value).split())
        if len(compact) <= limit:
            return compact
        return compact[:limit].rstrip()

    @staticmethod
    def _render_value(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            rendered = [MemoryContextBuilder._render_value(item) for item in value]
            compact = [item for item in rendered if item]
            return "、".join(compact)
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value).strip()

    @staticmethod
    def _join_with_section_limit(lines: list[str], max_chars: int) -> str:
        if not lines:
            return ""
        cap = max(1, int(max_chars))
        selected = [str(lines[0])]
        used = len(selected[0])
        for line in lines[1:]:
            text = str(line)
            add_len = 1 + len(text)
            if used + add_len > cap:
                break
            selected.append(text)
            used += add_len
        return "\n".join(selected)

    @staticmethod
    def _clip_section_text(section_text: str, max_chars: int, min_body_lines: int = 0) -> str:
        lines = [line for line in str(section_text).splitlines() if line]
        if not lines:
            return ""
        cap = max(1, int(max_chars))
        selected = [lines[0]]
        used = len(lines[0])
        body_lines = lines[1:]
        kept_body = 0
        for line in body_lines:
            add_len = 1 + len(line)
            if used + add_len > cap:
                break
            selected.append(line)
            used += add_len
            kept_body += 1
        if kept_body < min_body_lines and body_lines:
            remaining = cap - used - 1
            if remaining > 0:
                selected.append(body_lines[0][:remaining].rstrip())
        return "\n".join(selected)

    @staticmethod
    def _compact_recent_section(section_text: str, max_chars: int) -> str:
        """Keep every turn in the selected recent window, clipping each line evenly."""
        lines = [line for line in str(section_text).splitlines() if line]
        if not lines:
            return ""
        cap = max(1, int(max_chars))
        full = "\n".join(lines)
        if len(full) <= cap:
            return full

        header = lines[0][:cap].rstrip()
        body_lines = lines[1:]
        if not body_lines or len(header) >= cap:
            return header

        # Reserve one newline per body line, then divide the remaining characters
        # evenly so a verbose old turn cannot evict the newest turns from the
        # eight-turn window.
        available = cap - len(header) - len(body_lines)
        if available <= 0:
            return header
        per_line = max(1, available // len(body_lines))
        selected = [header, *(line[:per_line].rstrip() for line in body_lines)]
        return "\n".join(selected)[:cap].rstrip()

    def _compact_sections_for_budget(
        self,
        *,
        profile: str,
        summaries: str,
        structured: str,
        recent: str,
        max_chars: int,
    ) -> list[str]:
        separator_chars = 6
        reserve_recent = min(len(recent), max(250, max_chars // 3))
        reserve_summaries = min(len(summaries), max(120, max_chars // 8))
        remaining = max(
            2,
            max_chars - reserve_recent - reserve_summaries - separator_chars,
        )
        profile_budget = max(1, remaining // 2)
        structured_budget = max(1, remaining - profile_budget)
        return [
            self._clip_section_text(profile, profile_budget),
            self._clip_section_text(summaries, reserve_summaries),
            self._clip_section_text(structured, structured_budget),
            self._compact_recent_section(recent, reserve_recent),
        ]
