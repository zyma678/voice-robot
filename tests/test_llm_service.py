import pytest

from voice_robot.services.llm import (
    ArkChatConfig,
    ArkChatLLMService,
    ArkPromptConfig,
    LLMConfigurationError,
    LLMServiceError,
    LocalLLMService,
    render_user_prompt,
    reply_max_tokens,
)


@pytest.mark.asyncio
async def test_ark_generate_reply_builds_expected_request_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ArkChatLLMService(
        config=ArkChatConfig(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            model="ep-20260602123456-test",
        ),
        api_key_provider=lambda: "sk-test",
    )

    def fake_post_json(payload: dict, api_key: str) -> dict:
        captured["payload"] = payload
        captured["api_key"] = api_key
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "小恐龙今天学会了分享树叶，还帮助朋友找到了回家的路。",
                    }
                }
            ]
        }

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    text = await service.generate_reply(
        user_text="讲一个恐龙故事",
        user_id="xiaoming",
        memory_hints=[
            {"title": "喜欢恐龙", "summary": "偏爱恐龙故事", "tags": ["恐龙", "故事"]}
        ],
    )

    payload = captured["payload"]
    assert captured["api_key"] == "sk-test"
    assert text == "小恐龙今天学会了分享树叶，还帮助朋友找到了回家的路。"
    assert payload["model"] == "ep-20260602123456-test"
    assert payload["temperature"] == 0.7
    assert payload["max_tokens"] == 110
    assert payload["messages"][0]["role"] == "system"
    assert "面向小朋友" in payload["messages"][0]["content"]
    assert payload["messages"][-1]["role"] == "user"
    assert "当前用户: xiaoming" in payload["messages"][-1]["content"]
    assert "用户输入: 讲一个恐龙故事" in payload["messages"][-1]["content"]
    assert "喜欢恐龙" in payload["messages"][-1]["content"]


def test_reply_max_tokens_keeps_stories_longer_than_routine_turns() -> None:
    assert reply_max_tokens("今天天气怎么样") == 64
    assert reply_max_tokens("讲一个恐龙故事") == 110
    assert reply_max_tokens("编一段童话") == 110
    assert reply_max_tokens("说一个寓言") == 110
    assert reply_max_tokens("讲一个恐龙") == 110


@pytest.mark.asyncio
async def test_ark_generate_reply_passes_memory_block_through_top_level(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ArkChatLLMService(
        config=ArkChatConfig(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            model="ep-20260602123456-test",
        ),
        api_key_provider=lambda: "sk-test",
    )

    def fake_post_json(payload: dict, api_key: str) -> dict:
        captured["payload"] = payload
        captured["api_key"] = api_key
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "ok",
                    }
                }
            ]
        }

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    memory_block = "【用户画像】\n- 喜欢恐龙"
    text = await service.generate_reply(
        user_text="讲一个恐龙故事",
        user_id="xiaoming",
        memory_hints=[
            {"title": "旧线索", "summary": "不应被使用", "tags": ["恐龙"]}
        ],
        memory_block=memory_block,
    )

    payload = captured["payload"]
    assert captured["api_key"] == "sk-test"
    assert text == "ok"
    assert payload["messages"][-1]["role"] == "user"
    assert "【用户画像】\n- 喜欢恐龙" in payload["messages"][-1]["content"]
    assert "旧线索" not in payload["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_ark_generate_reply_records_success_metrics(monkeypatch) -> None:
    events: list[dict] = []
    captured: dict[str, object] = {}
    service = ArkChatLLMService(
        config=ArkChatConfig(base_url="https://example.com/api/v3", model="m1"),
        api_key_provider=lambda: "sk-test",
        usage_recorder=events.append,
    )

    def fake_post_json(payload: dict, api_key: str) -> dict:
        captured["payload"] = payload
        return {
            "choices": [{"message": {"content": "成功回复"}}],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 3,
                "total_tokens": 15,
            },
        }

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    assert (
        await service.generate_reply(
            user_text="你好",
            user_id="u1",
            memory_hints=[],
        )
        == "成功回复"
    )

    assert len(events) == 1
    event = events[0]
    assert event["success"] is True
    assert event["request_kind"] == "generate_reply"
    assert event["request_max_tokens"] == 64
    assert event["prompt_tokens"] == 12
    assert event["completion_tokens"] == 3
    assert event["prompt_chars"] == sum(
        len(message["content"]) for message in captured["payload"]["messages"]
    )
    assert event["completion_chars"] == len("成功回复")
    assert type(event["latency_ms"]) is int
    assert event["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_ark_generate_reply_records_failure_metrics(monkeypatch) -> None:
    events: list[dict] = []
    service = ArkChatLLMService(
        config=ArkChatConfig(base_url="https://example.com/api/v3", model="m1"),
        api_key_provider=lambda: "sk-test",
        usage_recorder=events.append,
    )

    def fake_post_json(payload: dict, api_key: str) -> dict:
        raise ArkChatLLMService._http_error(429, "rate limited")

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    with pytest.raises(LLMServiceError):
        await service.generate_reply(
            user_text="你好",
            user_id="u1",
            memory_hints=[],
        )

    assert len(events) == 1
    event = events[0]
    assert event["success"] is False
    assert event["request_kind"] == "generate_reply"
    assert event["error_code"] == "llm_rate_limited"
    assert event["prompt_tokens"] == 0
    assert event["completion_chars"] == 0
    assert event["prompt_chars"] > len("你好")


@pytest.mark.asyncio
async def test_ark_generate_freeform_records_kind_and_character_counts(monkeypatch) -> None:
    events: list[dict] = []
    service = ArkChatLLMService(
        config=ArkChatConfig(base_url="https://example.com/api/v3", model="m1"),
        api_key_provider=lambda: "sk-test",
        usage_recorder=events.append,
    )

    def fake_post_json(
        payload: dict, api_key: str, timeout_seconds: float | None = None
    ) -> dict:
        return {"choices": [{"message": {"content": "答案"}}]}

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    assert (
        await service.generate_freeform(
            system_prompt="SYS",
            user_prompt="USER",
            usage_user_id="console-idiom",
            request_kind="idiom_generation",
        )
        == "答案"
    )
    assert events[0]["success"] is True
    assert events[0]["request_kind"] == "idiom_generation"
    assert events[0]["user_id"] == "console-idiom"
    assert events[0]["request_max_tokens"] == 320
    assert events[0]["prompt_chars"] == len("SYSUSER")
    assert events[0]["completion_chars"] == len("答案")
    assert events[0]["total_tokens"] == 0


@pytest.mark.asyncio
async def test_ark_truncated_reply_is_failure_and_never_treated_as_complete(
    monkeypatch,
) -> None:
    events: list[dict] = []
    service = ArkChatLLMService(
        config=ArkChatConfig(base_url="https://example.com/api/v3", model="m1"),
        api_key_provider=lambda: "sk-test",
        usage_recorder=events.append,
    )

    monkeypatch.setattr(
        service,
        "_post_json",
        lambda payload, api_key: {
            "choices": [
                {"message": {"content": "小恐龙走到了"}, "finish_reason": "length"}
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 64,
                "total_tokens": 74,
            },
        },
    )

    with pytest.raises(LLMServiceError) as exc_info:
        await service.generate_reply(
            user_text="讲一个恐龙",
            user_id="u1",
            memory_hints=[],
        )

    assert exc_info.value.code == "llm_output_truncated"
    assert events[0]["success"] is False
    assert events[0]["error_code"] == "llm_output_truncated"
    assert events[0]["total_tokens"] == 74


def test_ark_extract_text_prefers_first_choice_message_content() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "小恐龙今天学会了分享树叶，还帮助朋友找到了回家的路。",
                }
            }
        ]
    }

    text = ArkChatLLMService._extract_text(payload)

    assert text == "小恐龙今天学会了分享树叶，还帮助朋友找到了回家的路。"


def test_build_messages_debug_includes_effective_system_prompt_with_bot_profile() -> None:
    messages, debug_prompt = ArkChatLLMService._build_messages(
        user_text="讲一个故事",
        user_id="xiaoming",
        memory_hints=[],
        memory_block="【用户画像】\n- 喜欢恐龙",
        prompt_config=ArkPromptConfig(
            system_prompt="基础系统提示\n\n【Bot 画像】\n自定义补充: 多用鼓励语气。",
            user_prompt_template="输入: {user_text}\n{memory_block}",
        ),
    )

    assert messages[0]["role"] == "system"
    assert "【Bot 画像】" in messages[0]["content"]
    assert "多用鼓励语气" in messages[0]["content"]
    assert debug_prompt["system_prompt"] == messages[0]["content"]


def test_ark_extract_text_supports_structured_content_blocks() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "小恐龙找到一片星星叶。"},
                        {"type": "tool_call", "text": "ignored"},
                        {"type": "text", "text": "它把叶子送给迷路的小兔。"},
                    ],
                }
            }
        ]
    }

    text = ArkChatLLMService._extract_text(payload)

    assert text == "小恐龙找到一片星星叶。\n它把叶子送给迷路的小兔。"


def test_ark_http_error_is_classified_for_401() -> None:
    error = ArkChatLLMService._http_error(401, "invalid api key")

    assert error.code == "llm_auth_failed"
    assert error.status_code == 401
    assert "API key" in error.detail


def test_ark_http_error_is_classified_for_404() -> None:
    error = ArkChatLLMService._http_error(404, "model not found")

    assert error.code == "llm_model_not_found"
    assert error.status_code == 404
    assert "模型" in error.detail


def test_ark_http_error_is_classified_for_429() -> None:
    error = ArkChatLLMService._http_error(429, "rate limited")

    assert error.code == "llm_rate_limited"
    assert error.status_code == 429
    assert "限流" in error.detail


def test_render_user_prompt_supports_expected_placeholders() -> None:
    text = render_user_prompt(
        "uid={user_id}|text={user_text}|mem={memory_block}",
        user_id="xiaoming",
        user_text="你好",
        memory_block="- 恐龙",
    )

    assert text == "uid=xiaoming|text=你好|mem=- 恐龙"


def test_render_user_prompt_rejects_unknown_placeholder() -> None:
    with pytest.raises(LLMConfigurationError):
        render_user_prompt(
            "uid={user_id}|x={unknown}",
            user_id="xiaoming",
            user_text="你好",
            memory_block="- 恐龙",
        )


def test_ark_build_messages_uses_prompt_config() -> None:
    messages, _ = ArkChatLLMService._build_messages(
        user_text="讲一个恐龙故事",
        user_id="xiaoming",
        memory_hints=[],
        prompt_config=ArkPromptConfig(
            system_prompt="SYS",
            user_prompt_template="USER={user_text}|MEM={memory_block}",
        ),
    )

    assert messages[0]["content"] == "SYS"
    assert "USER=讲一个恐龙故事" in messages[1]["content"]


def test_ark_build_messages_prefers_prebuilt_memory_block_over_hints() -> None:
    messages, _ = ArkChatLLMService._build_messages(
        user_text="讲一个恐龙故事",
        user_id="xiaoming",
        memory_hints=[
            {"title": "旧线索", "summary": "不应出现在 prompt 中", "tags": ["恐龙"]}
        ],
        memory_block="【用户画像】\n- 喜欢恐龙",
        prompt_config=ArkPromptConfig(
            system_prompt="SYS",
            user_prompt_template="USER={user_text}\nMEM={memory_block}",
        ),
    )

    assert "MEM=【用户画像】\n- 喜欢恐龙" in messages[1]["content"]
    assert "旧线索" not in messages[1]["content"]


def test_ark_build_messages_forces_memory_block_into_custom_template() -> None:
    messages, debug_prompt = ArkChatLLMService._build_messages(
        user_text="你叫什么？",
        user_id="xiaoming",
        memory_hints=[],
        memory_block="【最近 20 轮对话】\n- 第1轮用户: 你叫元宝",
        prompt_config=ArkPromptConfig(
            system_prompt="SYS",
            user_prompt_template="u={user_text}",
        ),
    )

    assert "u=你叫什么？" in messages[1]["content"]
    assert "【最近 20 轮对话】" in messages[1]["content"]
    assert debug_prompt["forced_memory_injection"] is True


@pytest.mark.asyncio
async def test_local_fallback_differentiates_by_input() -> None:
    """离线兜底不再对任何输入都回同一句（历史「永远同一句」回归守护）。"""
    service = LocalLLMService()

    replies = {}
    for q in ("天空为什么是蓝色的", "你叫什么名字", "大象怎么生活的"):
        replies[q] = await service.generate_reply(
            user_text=q,
            user_id="xiaoming",
            memory_hints=[],
            memory_block="",
        )

    # 三个不同问题必须得到三种不同回复，且都回显了各自的主题。
    assert len(set(replies.values())) == 3
    assert "天空" in replies["天空为什么是蓝色的"]
    assert "名字" in replies["你叫什么名字"]
    assert "大象" in replies["大象怎么生活的"]


@pytest.mark.asyncio
async def test_local_fallback_story_and_write_intents() -> None:
    service = LocalLLMService()

    story = await service.generate_reply(
        user_text="给我讲个故事",
        user_id="xiaoming",
        memory_hints=[{"title": "喜欢恐龙", "summary": "", "tags": ["恐龙"]}],
        memory_block="",
    )
    assert "小恐龙" in story

    write = await service.generate_reply(
        user_text="象字怎么写",
        user_id="xiaoming",
        memory_hints=[],
        memory_block="",
    )
    assert "象字怎么写" in write
    assert write != story


@pytest.mark.asyncio
async def test_local_fallback_reads_persona_and_memory() -> None:
    """兜底也能带上一点人设/最近记忆，而不是完全无视上下文。"""
    service = LocalLLMService()

    with_hint = await service.generate_reply(
        user_text="今天做什么呀",
        user_id="xiaoming",
        memory_hints=[{"title": "喜欢画画的小朋友", "summary": "", "tags": []}],
        memory_block="",
    )
    assert "喜欢画画的小朋友" in with_hint

    from_block = await service.generate_reply(
        user_text="今天做什么呀",
        user_id="xiaoming",
        memory_hints=[],
        memory_block="【用户画像】\n- 喜欢拼乐高",
    )
    assert "喜欢拼乐高" in from_block
