import asyncio
import math
import weakref
from datetime import UTC, datetime
from pathlib import Path

from voice_robot.agent import routing
from voice_robot.agent.state import AgentResult, AgentState
from voice_robot.agent.tools import generate_math_problem, get_current_time_text
from voice_robot.memory.audit_log_store import AuditLogStore
from voice_robot.memory.context_builder import MemoryContextBuilder
from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.file_store import FileMemoryStore
from voice_robot.memory.file_retriever import FileMemoryRetriever
from voice_robot.memory.profile_extractor import ProfileMemoryExtractor
from voice_robot.memory.summary_store import SummaryStore, build_local_summary
from voice_robot.services.llm import LLMService, LLMServiceError, LocalLLMService


def stream_text_chunks(text: str, *, chunk_size: int = 8) -> list[str]:
    normalized_size = max(1, chunk_size)
    return [
        text[index : index + normalized_size]
        for index in range(0, len(text), normalized_size)
    ]


class VoiceAgent:
    # Agents are rebuilt from runtime configuration for each turn. The lock must
    # therefore be shared across instances, otherwise concurrent connections for
    # the same user can race sequence allocation and JSONL rewrites.
    _user_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
        weakref.WeakValueDictionary()
    )

    def __init__(
        self,
        memory_root: Path | str,
        *,
        llm_service: LLMService | None = None,
    ) -> None:
        self.memory_root = Path(memory_root)
        self.retriever = FileMemoryRetriever(self.memory_root)
        self.llm_service = llm_service or LocalLLMService()
        self.fallback_llm_service = LocalLLMService()
        self.file_store = FileMemoryStore(self.memory_root)
        self.conversation_store = ConversationStore(self.memory_root)
        self.summary_store = SummaryStore(self.memory_root)
        self.audit_log_store = AuditLogStore(self.memory_root)
        self.context_builder = MemoryContextBuilder(self.memory_root)
        self.profile_extractor = ProfileMemoryExtractor()
    def _user_lock(self, user_id: str) -> asyncio.Lock:
        lock_key = f"{self.memory_root.resolve()}\0{user_id}"
        lock = self._user_locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[lock_key] = lock
        return lock

    async def ainvoke(self, text: str, *, user_id: str) -> AgentResult:
        async with self._user_lock(user_id):
            state = AgentState(user_text=text, user_id=user_id)

            if self._is_unsafe(text):
                state.safety_passed = False
                state.reply_source = "local-rule"
                state.response_text = (
                    "这个话题不适合小朋友，我们聊点学习、故事或有趣的问题吧。"
                )
                state.prompt_debug = self._build_prompt_debug(
                    state,
                    memory_block="",
                    route="local-rule:safety",
                )
            else:
                state.response_text = await self._reply(state)

            turn = self.conversation_store.append_turn(
                user_id,
                user_text=state.user_text,
                assistant_text=state.response_text,
                reply_source=state.reply_source,
                fallback_reason=state.fallback_reason,
                safety_passed=state.safety_passed,
            )
            state.turn_id = str(turn.get("turn_id", ""))

            if state.safety_passed:
                state.new_memories = await self._update_profile_and_memories(state, turn)
            await self._maybe_summarize(user_id)
            return self._to_result(state)

    async def _reply(
        self,
        state: AgentState,
        *,
        memory_block: str | None = None,
    ) -> str:
        text = state.user_text
        reply_route = routing.classify_reply_route(text)
        if reply_route == "math":
            state.reply_source = "local-rule"
            state.prompt_debug = self._build_prompt_debug(
                state,
                memory_block="",
                route="local-rule:math",
            )
            return generate_math_problem()
        if reply_route == "time":
            state.reply_source = "local-rule"
            state.prompt_debug = self._build_prompt_debug(
                state,
                memory_block="",
                route="local-rule:time",
            )
            return get_current_time_text()

        if memory_block is None:
            query = self._memory_query(text)
            state.retrieved_memories = self.retriever.search(query, state.user_id, top_k=3)
            memory_block = self.context_builder.build(
                state.user_id,
                query,
                structured_memories=state.retrieved_memories,
            )
        try:
            reply = await self.llm_service.generate_reply(
                user_text=text,
                user_id=state.user_id,
                memory_hints=state.retrieved_memories,
                memory_block=memory_block,
            )
            state.reply_source = "ark"
            state.fallback_reason = None
            state.prompt_debug = self._coerce_prompt_debug(
                getattr(self.llm_service, "_last_debug_prompt", None),
                state,
                memory_block=memory_block,
                route="ark",
            )
            return reply
        except LLMServiceError as exc:
            state.reply_source = "fallback-local"
            state.fallback_reason = exc.detail
            state.prompt_debug = self._coerce_prompt_debug(
                getattr(self.llm_service, "_last_debug_prompt", None),
                state,
                memory_block=memory_block,
                route="fallback-local",
            )
            return await self.fallback_llm_service.generate_reply(
                user_text=text,
                user_id=state.user_id,
                memory_hints=state.retrieved_memories,
                memory_block=memory_block,
            )

    async def _update_profile_and_memories(
        self,
        state: AgentState,
        turn: dict,
    ) -> list[dict]:
        try:
            extraction = await self.profile_extractor.extract(
                user_id=state.user_id,
                turn_id=str(turn.get("turn_id", "")),
                user_text=state.user_text,
                assistant_text=state.response_text,
                current_profile=self.file_store.load_profile(state.user_id),
            )
        except Exception as exc:
            self.audit_log_store.append_event(
                state.user_id,
                event_type="profile_extract_error",
                payload={"error": str(exc)},
                turn_id=str(turn.get("turn_id", "")),
            )
            return []

        if not isinstance(extraction, dict):
            self.audit_log_store.append_event(
                state.user_id,
                event_type="profile_extract_invalid",
                payload={"error": "extractor returned non-dict payload"},
                turn_id=str(turn.get("turn_id", "")),
            )
            return []

        profile_patch = extraction.get("profile_patch", {})
        if isinstance(profile_patch, dict) and profile_patch:
            try:
                self.file_store.patch_profile(state.user_id, profile_patch)
            except Exception as exc:
                self.audit_log_store.append_event(
                    state.user_id,
                    event_type="profile_patch_error",
                    payload={"error": str(exc)},
                    turn_id=str(turn.get("turn_id", "")),
                )

        new_memories: list[dict] = []
        structured_memories = extraction.get("structured_memories", [])
        if not isinstance(structured_memories, list):
            structured_memories = []
        for memory in structured_memories:
            if not isinstance(memory, dict):
                continue
            category = str(memory.get("category", "")).strip() or "preference"
            title = str(memory.get("title", "")).strip() or "用户记忆"
            content = str(memory.get("content", "")).strip()
            if not content:
                continue
            importance = self._coerce_float(memory.get("importance"), default=0.6)
            confidence = self._coerce_float(memory.get("confidence"), default=0.6)

            tags_raw = memory.get("tags")
            tags: list[str] = []
            if isinstance(tags_raw, list):
                tags = [str(tag).strip() for tag in tags_raw if str(tag).strip()]

            try:
                memory_id = self.file_store.save_memory(
                    state.user_id,
                    category=category,
                    title=title,
                    content=content,
                    tags=tags,
                    importance=importance,
                )
                self.file_store.update_memory(
                    state.user_id,
                    memory_id,
                    {
                        "confidence": confidence,
                        "source_turn_id": str(
                            memory.get("source_turn_id", turn.get("turn_id", ""))
                        ),
                    },
                )
                payload = dict(memory)
                payload["id"] = memory_id
                new_memories.append(payload)
            except Exception as exc:
                self.audit_log_store.append_event(
                    state.user_id,
                    event_type="structured_memory_write_error",
                    payload={"error": str(exc)},
                    turn_id=str(turn.get("turn_id", "")),
                )

        return new_memories

    @staticmethod
    def _coerce_float(value: object, *, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return float(default)
        if not math.isfinite(number):
            return float(default)
        return number

    async def _maybe_summarize(self, user_id: str) -> None:
        meta = self.conversation_store.load_meta(user_id)
        latest_sequence = self._coerce_int(meta.get("next_sequence"), default=1) - 1
        last_summarized = self._coerce_int(meta.get("last_summarized_sequence"), default=0)
        if latest_sequence - last_summarized < 40:
            return

        start = last_summarized + 1
        max_end = latest_sequence - 20
        while start + 19 <= max_end:
            end = start + 19
            turns = self.conversation_store.list_turns(
                user_id,
                start_sequence=start,
                end_sequence=end,
            )
            if len(turns) < 20:
                break
            redacted_turns = []
            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                if turn.get("safety_passed") is False:
                    redacted_turns.append(
                        {
                            **turn,
                            "user_text": "[已屏蔽]",
                            "assistant_text": "[已屏蔽]",
                        }
                    )
                else:
                    redacted_turns.append(turn)

            content = build_local_summary(redacted_turns)
            try:
                self.summary_store.save_summary(
                    user_id,
                    start_sequence=start,
                    end_sequence=end,
                    content=content,
                    summary_source="local",
                )
                meta["last_summarized_sequence"] = end
                meta["updated_at"] = datetime.now(UTC).isoformat()
                self.conversation_store.save_meta(user_id, meta)
            except Exception as exc:
                self.audit_log_store.append_event(
                    user_id,
                    event_type="summary_write_error",
                    payload={"error": str(exc), "start_sequence": start, "end_sequence": end},
                )
                break
            start = end + 1

    @staticmethod
    def _coerce_int(value: object, *, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _is_unsafe(text: str) -> bool:
        return routing.is_unsafe(text)

    @staticmethod
    def _memory_query(text: str) -> str:
        return routing.enrich_memory_query(text)

    @staticmethod
    def _to_result(state: AgentState) -> AgentResult:
        return AgentResult(
            response_text=state.response_text,
            safety_passed=state.safety_passed,
            reply_source=state.reply_source,
            turn_id=getattr(state, "turn_id", ""),
            fallback_reason=state.fallback_reason,
            retrieved_memories=state.retrieved_memories,
            new_memories=state.new_memories,
            prompt_debug=state.prompt_debug,
        )

    @staticmethod
    def _coerce_prompt_debug(
        payload: object,
        state: AgentState,
        *,
        memory_block: str,
        route: str,
    ) -> dict:
        if isinstance(payload, dict):
            return {
                **payload,
                "route": route,
                "reply_source": state.reply_source,
                "fallback_reason": state.fallback_reason or "",
            }
        return VoiceAgent._build_prompt_debug(state, memory_block=memory_block, route=route)

    @staticmethod
    def _build_prompt_debug(
        state: AgentState,
        *,
        memory_block: str,
        route: str,
    ) -> dict:
        return {
            "route": route,
            "reply_source": state.reply_source,
            "fallback_reason": state.fallback_reason or "",
            "user_id": state.user_id,
            "user_text": state.user_text,
            "memory_block": memory_block,
            "system_prompt": "",
            "user_prompt_template": "",
            "rendered_user_prompt": "",
        }
