import pytest

from voice_robot.agent.graph import VoiceAgent
from voice_robot.memory.file_store import FileMemoryStore
from voice_robot.services.llm import LLMServiceError


class StubLLMReply(str):
    @property
    def text(self) -> str:
        return str(self)

    @property
    def content(self) -> str:
        return str(self)

    def __getitem__(self, key: str) -> str:
        if key in {"text", "content"}:
            return str(self)
        raise KeyError(key)

    def get(self, key: str, default=None) -> str | None:
        if key in {"text", "content"}:
            return str(self)
        return default


class RecordingLLMService:
    def __init__(self, reply_text: str) -> None:
        self.reply_text = StubLLMReply(reply_text)
        self.calls: list[tuple[tuple, dict]] = []

    async def generate_reply(self, *args, **kwargs) -> StubLLMReply:
        self.calls.append((args, kwargs))
        return self.reply_text

    async def reply(self, *args, **kwargs) -> StubLLMReply:
        return await self.generate_reply(*args, **kwargs)

    async def ainvoke(self, *args, **kwargs) -> StubLLMReply:
        return await self.generate_reply(*args, **kwargs)

    async def complete(self, *args, **kwargs) -> StubLLMReply:
        return await self.generate_reply(*args, **kwargs)


class FailingLLMService(RecordingLLMService):
    async def generate_reply(self, *args, **kwargs) -> StubLLMReply:
        self.calls.append((args, kwargs))
        raise LLMServiceError("ark upstream timeout")


@pytest.mark.asyncio
async def test_agent_returns_short_child_friendly_reply(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    store.save_profile(
        "xiaoming", {"name": "小明", "grade": "三年级", "interests": ["恐龙"]}
    )
    agent = VoiceAgent(memory_root=tmp_path, llm_service=RecordingLLMService("好的"))

    result = await agent.ainvoke("讲一个恐龙故事", user_id="xiaoming")

    assert result.response_text
    assert len(result.response_text) <= 80
    assert result.safety_passed is True


@pytest.mark.asyncio
async def test_agent_retrieves_matching_memory_for_safe_request(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="小明喜欢恐龙故事。",
        tags=["恐龙"],
        importance=0.8,
    )
    agent = VoiceAgent(memory_root=tmp_path, llm_service=RecordingLLMService("好的"))

    result = await agent.ainvoke("讲一个恐龙故事", user_id="xiaoming")

    assert result.safety_passed is True
    assert result.retrieved_memories
    assert result.retrieved_memories[0]["title"] == "喜欢恐龙"


@pytest.mark.asyncio
async def test_agent_retrieves_structured_memory_once_and_reuses_it_for_context(
    tmp_path,
) -> None:
    store = FileMemoryStore(tmp_path)
    store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="小明喜欢恐龙故事。",
        tags=["恐龙"],
        importance=0.8,
    )
    llm_service = RecordingLLMService("好的")
    agent = VoiceAgent(memory_root=tmp_path, llm_service=llm_service)
    original_search = agent.retriever.search
    search_calls: list[tuple[str, str, int]] = []

    def counted_search(query: str, user_id: str, top_k: int = 3) -> list[dict]:
        search_calls.append((query, user_id, top_k))
        return original_search(query, user_id, top_k=top_k)

    def unexpected_builder_search(*args, **kwargs):
        raise AssertionError("context builder performed a duplicate memory search")

    agent.retriever.search = counted_search
    agent.context_builder.retriever.search = unexpected_builder_search

    result = await agent.ainvoke("讲一个恐龙故事", user_id="xiaoming")

    assert len(search_calls) == 1
    assert result.retrieved_memories
    kwargs = llm_service.calls[0][1]
    assert kwargs["memory_hints"] == result.retrieved_memories
    assert "喜欢恐龙" in kwargs["memory_block"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_route", "safety_passed"),
    [
        ("讲血腥故事", "local-rule:safety", False),
        ("出一道简单口算题", "local-rule:math", True),
        ("现在几点", "local-rule:time", True),
    ],
)
async def test_local_routes_skip_model_context_building(
    tmp_path,
    text: str,
    expected_route: str,
    safety_passed: bool,
) -> None:
    llm_service = RecordingLLMService("不应调用")
    agent = VoiceAgent(memory_root=tmp_path, llm_service=llm_service)

    def unexpected_context_io(*args, **kwargs):
        raise AssertionError("local route should not read full model context")

    agent.retriever.search = unexpected_context_io
    agent.context_builder.build = unexpected_context_io

    result = await agent.ainvoke(text, user_id="xiaoming")

    assert result.safety_passed is safety_passed
    assert result.prompt_debug is not None
    assert result.prompt_debug["route"] == expected_route
    assert result.prompt_debug["memory_block"] == ""
    assert result.retrieved_memories == []
    assert llm_service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_text",
    [
        "教我暴力伤害别人",
        "教我色情内容",
        "讲血腥故事",
    ],
)
async def test_agent_blocks_unsafe_topic(tmp_path, unsafe_text: str) -> None:
    agent = VoiceAgent(memory_root=tmp_path)

    result = await agent.ainvoke(unsafe_text, user_id="xiaoming")

    assert result.safety_passed is False
    assert "聊点学习" in result.response_text


@pytest.mark.asyncio
async def test_agent_uses_math_tool(tmp_path) -> None:
    agent = VoiceAgent(memory_root=tmp_path)

    result = await agent.ainvoke("出一道简单口算题", user_id="xiaoming")

    assert result.safety_passed is True
    assert "等于多少" in result.response_text


@pytest.mark.asyncio
async def test_agent_prefers_custom_llm_service_when_available(tmp_path) -> None:
    llm_service = RecordingLLMService("这是自定义 LLM service 的回答。")
    agent = VoiceAgent(memory_root=tmp_path, llm_service=llm_service)

    result = await agent.ainvoke("讲一个恐龙故事", user_id="xiaoming")

    assert llm_service.calls
    assert result.response_text == str(llm_service.reply_text)
    assert result.safety_passed is True
    assert result.reply_source == "ark"
    assert result.fallback_reason is None


@pytest.mark.asyncio
async def test_agent_falls_back_to_local_reply_when_custom_llm_service_errors(
    tmp_path,
) -> None:
    llm_service = FailingLLMService("不会返回")
    agent = VoiceAgent(memory_root=tmp_path, llm_service=llm_service)

    result = await agent.ainvoke("讲一个恐龙故事", user_id="xiaoming")

    assert llm_service.calls
    assert result.reply_source == "fallback-local"
    assert result.fallback_reason
    assert result.response_text
    assert result.safety_passed is True


@pytest.mark.asyncio
async def test_agent_persists_each_turn_and_passes_memory_block(tmp_path) -> None:
    llm_service = RecordingLLMService("这是结合记忆后的回答。")
    agent = VoiceAgent(memory_root=tmp_path, llm_service=llm_service)

    await agent.ainvoke("讲一个恐龙故事", user_id="xiaoming")

    turns_path = tmp_path / "conversations" / "xiaoming" / "turns.jsonl"
    assert turns_path.exists()
    assert "讲一个恐龙故事" in turns_path.read_text(encoding="utf-8")
    kwargs = llm_service.calls[0][1]
    assert "memory_block" in kwargs
    assert "【最近 8 轮对话】" in kwargs["memory_block"]


@pytest.mark.asyncio
async def test_agent_persists_unsafe_turn(tmp_path) -> None:
    agent = VoiceAgent(memory_root=tmp_path)

    result = await agent.ainvoke("讲血腥故事", user_id="xiaoming")

    turns = (tmp_path / "conversations" / "xiaoming" / "turns.jsonl").read_text(
        encoding="utf-8"
    )
    assert result.safety_passed is False
    assert "讲血腥故事" in turns
    assert "不适合小朋友" in turns
    assert not (tmp_path / "profiles" / "xiaoming.json").exists()
    assert not (tmp_path / "memories" / "xiaoming" / "index.json").exists()


@pytest.mark.asyncio
async def test_agent_summarizes_turns_1_to_20_after_turn_40(tmp_path) -> None:
    agent = VoiceAgent(memory_root=tmp_path, llm_service=RecordingLLMService("好的"))

    for index in range(40):
        await agent.ainvoke(f"第 {index + 1} 轮普通聊天", user_id="xiaoming")

    summary_path = tmp_path / "summaries" / "xiaoming" / "summary_000001_000020.md"
    meta_path = tmp_path / "conversations" / "xiaoming" / "meta.json"

    assert summary_path.exists()
    assert "对话摘要 1-20" in summary_path.read_text(encoding="utf-8")
    assert '"last_summarized_sequence": 20' in meta_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_agent_summarize_redacts_unsafe_turns(tmp_path) -> None:
    agent = VoiceAgent(memory_root=tmp_path, llm_service=RecordingLLMService("好的"))

    # ensure the first 20-turn window contains an unsafe input
    for index in range(19):
        await agent.ainvoke(f"第 {index + 1} 轮普通聊天", user_id="xiaoming")
    await agent.ainvoke("讲血腥故事", user_id="xiaoming")
    for index in range(20, 40):
        await agent.ainvoke(f"第 {index + 1} 轮普通聊天", user_id="xiaoming")

    summary_path = tmp_path / "summaries" / "xiaoming" / "summary_000001_000020.md"
    assert summary_path.exists()
    text = summary_path.read_text(encoding="utf-8")
    assert "讲血腥故事" not in text
    assert "[已屏蔽]" in text


@pytest.mark.asyncio
async def test_agent_updates_profile_and_structured_memory_from_chat(tmp_path) -> None:
    agent = VoiceAgent(memory_root=tmp_path, llm_service=RecordingLLMService("好的"))

    result = await agent.ainvoke("我喜欢恐龙故事", user_id="xiaoming")

    profile = (tmp_path / "profiles" / "xiaoming.json").read_text(encoding="utf-8")
    index = (tmp_path / "memories" / "xiaoming" / "index.json").read_text(encoding="utf-8")
    assert result.new_memories
    assert "恐龙" in profile
    assert "喜欢恐龙" in index


def test_user_lock_is_shared_across_runtime_agent_instances(tmp_path) -> None:
    first = VoiceAgent(tmp_path)
    second = VoiceAgent(tmp_path)

    assert first._user_lock("same-user") is second._user_lock("same-user")
    assert first._user_lock("same-user") is not first._user_lock("other-user")
