import inspect
import json
import math
import re
from datetime import UTC, datetime
from json import JSONDecodeError
from typing import Protocol

from voice_robot.services.llm import LLMServiceError


class ExtractorLLM(Protocol):
    async def generate_reply(
        self,
        *,
        user_text: str,
        user_id: str,
        memory_hints: list[dict],
    ) -> str:
        """Generate extraction JSON payload."""


class ProfileMemoryExtractor:
    def __init__(self, llm_service: ExtractorLLM | None = None) -> None:
        self.llm_service = llm_service

    async def extract(
        self,
        *,
        user_id,
        turn_id,
        user_text,
        assistant_text,
        current_profile,
    ) -> dict:
        del assistant_text
        if self.llm_service is not None:
            try:
                prompt = self._build_prompt(
                    current_profile=current_profile,
                    user_text=user_text,
                )
                llm_reply = await self._generate_llm_reply(
                    prompt=prompt,
                    user_id=str(user_id),
                )
                return self._normalize(
                    payload=json.loads(self._clean_llm_reply(llm_reply)),
                    turn_id=str(turn_id),
                )
            except (LLMServiceError, JSONDecodeError, ValueError):
                pass
        return self._extract_with_rules(user_text=str(user_text), turn_id=str(turn_id))

    def _build_prompt(self, *, current_profile, user_text: str) -> str:
        profile_payload = current_profile if isinstance(current_profile, dict) else {}
        return (
            "从儿童语音机器人对话中抽取用户画像和结构化记忆，只返回 JSON。\n"
            "只返回 JSON 对象，不要 Markdown、不要解释。\n"
            f"current_profile: {json.dumps(profile_payload, ensure_ascii=False)}\n"
            f"user_text: {user_text}"
        )

    async def _generate_llm_reply(self, *, prompt: str, user_id: str) -> str:
        assert self.llm_service is not None
        if self._supports_memory_block():
            return await self.llm_service.generate_reply(
                user_text=prompt,
                user_id=user_id,
                memory_hints=[],
                memory_block="",
            )
        return await self.llm_service.generate_reply(
            user_text=prompt,
            user_id=user_id,
            memory_hints=[],
        )

    def _extract_with_rules(self, *, user_text: str, turn_id: str) -> dict:
        profile_patch: dict[str, list[str]] = {}
        interests: list[str] = []
        style_preferences: list[str] = []
        weaknesses: list[str] = []

        if "恐龙" in user_text:
            interests.append("恐龙")
        if "故事" in user_text:
            interests.append("故事")
        if "短" in user_text or "简短" in user_text:
            style_preferences.append("简短")
        assistant_name = self._extract_assistant_name(user_text)

        matches = re.findall(r"不会\s*([0-9xX+\-*/= ]+)", user_text)
        weaknesses.extend(match.strip() for match in matches if match.strip())

        interests = self._dedupe(interests)
        style_preferences = self._dedupe(style_preferences)
        weaknesses = self._dedupe(weaknesses)

        if interests:
            profile_patch["interests"] = interests
        if style_preferences:
            profile_patch["style_preferences"] = style_preferences
        if assistant_name:
            profile_patch["assistant_name"] = [assistant_name]
        if weaknesses:
            profile_patch["weaknesses"] = weaknesses

        memories = []
        if interests:
            joined = "、".join(interests)
            memories.append(
                self._normalize_memory(
                    {
                        "category": "preference",
                        "title": f"喜欢{joined}",
                        "content": f"用户表达了对{joined}的兴趣。",
                        "tags": interests,
                        "importance": 0.7,
                        "confidence": 0.7,
                    },
                    turn_id=turn_id,
                )
            )
        if weaknesses:
            joined = "、".join(weaknesses)
            memories.append(
                self._normalize_memory(
                    {
                        "category": "learning",
                        "title": "学习薄弱点",
                        "content": f"用户提到不会{joined}。",
                        "tags": ["学习", "薄弱点"],
                        "importance": 0.8,
                        "confidence": 0.7,
                    },
                    turn_id=turn_id,
                )
            )
        if assistant_name:
            memories.append(
                self._normalize_memory(
                    {
                        "category": "preference",
                        "title": "机器人称呼",
                        "content": f"用户要求机器人自称或被称为{assistant_name}。",
                        "tags": ["称呼", assistant_name],
                        "importance": 0.9,
                        "confidence": 0.8,
                    },
                    turn_id=turn_id,
                )
            )

        return {
            "profile_patch": profile_patch,
            "structured_memories": [memory for memory in memories if memory is not None],
        }

    def _normalize(self, *, payload: object, turn_id: str) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")

        raw_profile_patch = payload.get("profile_patch")
        raw_structured_memories = payload.get("structured_memories")

        profile_patch = raw_profile_patch if isinstance(raw_profile_patch, dict) else {}
        normalized_profile_patch = {
            key: self._string_list(value)
            for key, value in profile_patch.items()
            if isinstance(key, str)
        }

        structured_memories = (
            raw_structured_memories if isinstance(raw_structured_memories, list) else []
        )
        normalized_memories = []
        for memory in structured_memories:
            try:
                normalized = self._normalize_memory(memory, turn_id=turn_id)
            except (TypeError, ValueError):
                continue
            if normalized is not None:
                normalized_memories.append(normalized)

        return {
            "profile_patch": normalized_profile_patch,
            "structured_memories": normalized_memories,
        }

    def _normalize_memory(self, memory: object, *, turn_id: str) -> dict | None:
        if not isinstance(memory, dict):
            raise ValueError("memory must be a dict")

        category = str(memory.get("category", "")).strip()
        title = str(memory.get("title", "")).strip()
        content = str(memory.get("content", "")).strip()
        if not category or not title or not content:
            raise ValueError("memory missing required fields")

        try:
            importance = float(memory.get("importance"))
            confidence = float(memory.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise ValueError("memory score must be numeric") from exc
        if not math.isfinite(importance) or not math.isfinite(confidence):
            raise ValueError("memory score must be finite")

        return {
            "category": category,
            "title": title,
            "content": content,
            "tags": self._string_list(memory.get("tags")),
            "importance": importance,
            "confidence": confidence,
            "source_turn_id": turn_id,
            "updated_at": self._coerce_timestamp(memory.get("updated_at")),
        }

    def _string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return self._dedupe("" if item is None else str(item).strip() for item in value)

    @staticmethod
    def _dedupe(values) -> list[str]:
        seen = set()
        result = []
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    @staticmethod
    def _extract_assistant_name(user_text: str) -> str:
        patterns = [
            r"你(?:以后)?叫(?:做)?\s*([\u4e00-\u9fffA-Za-z0-9_\-]{1,12})",
            r"叫你\s*([\u4e00-\u9fffA-Za-z0-9_\-]{1,12})",
            r"你的名字(?:是|叫)\s*([\u4e00-\u9fffA-Za-z0-9_\-]{1,12})",
        ]
        for pattern in patterns:
            match = re.search(pattern, user_text)
            if not match:
                continue
            name = match.group(1).strip()
            name = re.sub(r"(吧|呀|哦|了|啦|哈)$", "", name).strip()
            if name:
                return name
        return ""

    def _coerce_timestamp(self, value: object) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return self._now()

    @staticmethod
    def _clean_llm_reply(reply: object) -> str:
        if not isinstance(reply, str):
            raise ValueError("llm reply must be a str")
        text = reply.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
        if fenced:
            return fenced.group(1).strip()
        return text

    def _supports_memory_block(self) -> bool:
        assert self.llm_service is not None
        try:
            signature = inspect.signature(self.llm_service.generate_reply)
        except (TypeError, ValueError):
            return False
        return "memory_block" in signature.parameters

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
