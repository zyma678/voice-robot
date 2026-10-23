import pytest

from voice_robot.memory import ProfileMemoryExtractor
from voice_robot.services.llm import LLMServiceError


class StubLLMService:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def generate_reply(
        self,
        *,
        user_text: str,
        user_id: str,
        memory_hints: list[dict],
        memory_block: str = "",
    ) -> str:
        self.calls.append(
            {
                "user_text": user_text,
                "user_id": user_id,
                "memory_hints": memory_hints,
                "memory_block": memory_block,
            }
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class LegacyStubLLMService:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def generate_reply(
        self,
        *,
        user_text: str,
        user_id: str,
        memory_hints: list[dict],
    ) -> str:
        self.calls.append(
            {
                "user_text": user_text,
                "user_id": user_id,
                "memory_hints": memory_hints,
            }
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class InternalTypeErrorLLMService:
    async def generate_reply(
        self,
        *,
        user_text: str,
        user_id: str,
        memory_hints: list[dict],
        memory_block: str = "",
    ) -> str:
        del user_text, user_id, memory_hints, memory_block
        raise TypeError("service exploded internally")


class MisleadingMemoryBlockTypeErrorLLMService:
    async def generate_reply(
        self,
        *,
        user_text: str,
        user_id: str,
        memory_hints: list[dict],
        memory_block: str = "",
    ) -> str:
        del user_text, user_id, memory_hints, memory_block
        raise TypeError("unexpected keyword argument 'memory_block' while rendering template")


@pytest.mark.asyncio
async def test_extractor_uses_llm_json_when_available() -> None:
    llm_service = StubLLMService(
        """
        {
          "profile_patch": {
            "interests": ["恐龙"]
          },
          "structured_memories": [
            {
              "category": "preference",
              "title": "喜欢恐龙",
              "content": "用户喜欢恐龙。",
              "tags": ["恐龙"],
              "importance": 0.9,
              "confidence": 0.8,
              "source_turn_id": "old_turn",
              "updated_at": "2026-06-03T00:00:00+00:00"
            }
          ]
        }
        """
    )

    extractor = ProfileMemoryExtractor(llm_service=llm_service)
    result = await extractor.extract(
        user_id="user_1",
        turn_id="turn_1",
        user_text="我喜欢恐龙",
        assistant_text="太好了",
        current_profile={"grade": "二年级"},
    )

    assert result["profile_patch"]["interests"] == ["恐龙"]
    assert result["structured_memories"][0]["title"] == "喜欢恐龙"
    assert result["structured_memories"][0]["source_turn_id"] == "turn_1"
    assert llm_service.calls[0]["user_id"] == "user_1"
    assert "从儿童语音机器人对话中抽取用户画像和结构化记忆，只返回 JSON" in llm_service.calls[0]["user_text"]
    assert "只返回 JSON 对象，不要 Markdown、不要解释" in llm_service.calls[0]["user_text"]
    assert "我喜欢恐龙" in llm_service.calls[0]["user_text"]
    assert "二年级" in llm_service.calls[0]["user_text"]


@pytest.mark.asyncio
async def test_extractor_supports_legacy_llm_signature_without_memory_block() -> None:
    llm_service = LegacyStubLLMService(
        """
        {
          "profile_patch": {
            "interests": ["恐龙"]
          },
          "structured_memories": [
            {
              "category": "preference",
              "title": "喜欢恐龙",
              "content": "用户喜欢恐龙。",
              "tags": ["恐龙"],
              "importance": 0.9,
              "confidence": 0.8
            }
          ]
        }
        """
    )

    extractor = ProfileMemoryExtractor(llm_service=llm_service)
    result = await extractor.extract(
        user_id="user_legacy",
        turn_id="turn_legacy",
        user_text="我喜欢恐龙",
        assistant_text="太好了",
        current_profile={},
    )

    assert result["profile_patch"]["interests"] == ["恐龙"]
    assert result["structured_memories"][0]["source_turn_id"] == "turn_legacy"
    assert llm_service.calls == [
        {
            "user_text": llm_service.calls[0]["user_text"],
            "user_id": "user_legacy",
            "memory_hints": [],
        }
    ]


@pytest.mark.asyncio
async def test_extractor_falls_back_to_keyword_rules() -> None:
    extractor = ProfileMemoryExtractor(
        llm_service=StubLLMService(
            LLMServiceError("boom", code="llm_error", detail="boom")
        )
    )

    result = await extractor.extract(
        user_id="user_1",
        turn_id="turn_1",
        user_text="我喜欢恐龙故事，但是不会 7x8，回答短一点",
        assistant_text="好的",
        current_profile={},
    )

    assert "恐龙" in result["profile_patch"]["interests"]
    assert "7x8" in result["profile_patch"]["weaknesses"]
    assert result["structured_memories"][0]["category"] == "preference"


@pytest.mark.asyncio
async def test_rule_extractor_detects_assistant_name_preference() -> None:
    extractor = ProfileMemoryExtractor()

    result = await extractor.extract(
        user_id="xiaoming",
        turn_id="turn_1",
        user_text="现在记住，你叫元宝，不要忘记了",
        assistant_text="好的",
        current_profile={},
    )

    assert result["profile_patch"]["assistant_name"] == ["元宝"]
    assert result["structured_memories"][0]["title"] == "机器人称呼"
    assert "元宝" in result["structured_memories"][0]["content"]


@pytest.mark.asyncio
async def test_extractor_falls_back_when_llm_returns_invalid_json() -> None:
    extractor = ProfileMemoryExtractor(llm_service=StubLLMService("{not-json"))

    result = await extractor.extract(
        user_id="user_1",
        turn_id="turn_2",
        user_text="我喜欢恐龙",
        assistant_text="好的",
        current_profile={},
    )

    assert result["profile_patch"]["interests"] == ["恐龙"]
    assert result["structured_memories"][0]["source_turn_id"] == "turn_2"


@pytest.mark.asyncio
async def test_extractor_parses_fenced_json_reply() -> None:
    extractor = ProfileMemoryExtractor(
        llm_service=StubLLMService(
            """```json
            {
              "profile_patch": {
                "interests": ["恐龙"]
              },
              "structured_memories": [
                {
                  "category": "preference",
                  "title": "喜欢恐龙",
                  "content": "用户喜欢恐龙。",
                  "tags": ["恐龙"],
                  "importance": 0.7,
                  "confidence": 0.7
                }
              ]
            }
            ```"""
        )
    )

    result = await extractor.extract(
        user_id="user_1",
        turn_id="turn_fenced",
        user_text="我喜欢恐龙",
        assistant_text="好的",
        current_profile={},
    )

    assert result["profile_patch"]["interests"] == ["恐龙"]
    assert result["structured_memories"][0]["source_turn_id"] == "turn_fenced"


@pytest.mark.asyncio
async def test_extractor_skips_bad_structured_memory_items() -> None:
    extractor = ProfileMemoryExtractor(
        llm_service=StubLLMService(
            """
            {
              "profile_patch": {
                "interests": ["恐龙"]
              },
              "structured_memories": [
                {
                  "category": "preference",
                  "title": "喜欢恐龙",
                  "content": "用户喜欢恐龙。",
                  "tags": ["恐龙", "", "恐龙"],
                  "importance": 0.7,
                  "confidence": 0.7
                },
                {
                  "category": "learning",
                  "title": "坏数据",
                  "content": "会被跳过",
                  "tags": ["数学"],
                  "importance": "bad",
                  "confidence": 0.7
                }
              ]
            }
            """
        )
    )

    result = await extractor.extract(
        user_id="user_1",
        turn_id="turn_3",
        user_text="我喜欢恐龙",
        assistant_text="好的",
        current_profile={},
    )

    assert result["structured_memories"] == [
        {
            "category": "preference",
            "title": "喜欢恐龙",
            "content": "用户喜欢恐龙。",
            "tags": ["恐龙"],
            "importance": 0.7,
            "confidence": 0.7,
            "source_turn_id": "turn_3",
            "updated_at": result["structured_memories"][0]["updated_at"],
        }
    ]


@pytest.mark.asyncio
async def test_extractor_skips_non_finite_memory_scores() -> None:
    extractor = ProfileMemoryExtractor(
        llm_service=StubLLMService(
            """
            {
              "profile_patch": {
                "interests": ["恐龙"]
              },
              "structured_memories": [
                {
                  "category": "preference",
                  "title": "喜欢恐龙",
                  "content": "用户喜欢恐龙。",
                  "tags": ["恐龙"],
                  "importance": 0.7,
                  "confidence": 0.7
                },
                {
                  "category": "learning",
                  "title": "学习薄弱点",
                  "content": "用户提到不会7x8。",
                  "tags": ["学习", "薄弱点"],
                  "importance": "NaN",
                  "confidence": 0.7
                },
                {
                  "category": "learning",
                  "title": "另一个薄弱点",
                  "content": "用户提到不会8x9。",
                  "tags": ["学习", "薄弱点"],
                  "importance": 0.8,
                  "confidence": "Infinity"
                }
              ]
            }
            """
        )
    )

    result = await extractor.extract(
        user_id="user_1",
        turn_id="turn_4",
        user_text="我喜欢恐龙",
        assistant_text="好的",
        current_profile={},
    )

    assert len(result["structured_memories"]) == 1
    assert result["structured_memories"][0]["title"] == "喜欢恐龙"


@pytest.mark.asyncio
async def test_extractor_skips_null_memory_scores_and_keeps_valid_items() -> None:
    extractor = ProfileMemoryExtractor(
        llm_service=StubLLMService(
            """
            {
              "profile_patch": {
                "interests": ["恐龙"]
              },
              "structured_memories": [
                {
                  "category": "preference",
                  "title": "喜欢恐龙",
                  "content": "用户喜欢恐龙。",
                  "tags": ["恐龙"],
                  "importance": 0.7,
                  "confidence": 0.7
                },
                {
                  "category": "learning",
                  "title": "学习薄弱点",
                  "content": "用户提到不会7x8。",
                  "tags": ["学习", "薄弱点"],
                  "importance": null,
                  "confidence": 0.7
                },
                {
                  "category": "learning",
                  "title": "另一个薄弱点",
                  "content": "用户提到不会8x9。",
                  "tags": ["学习", "薄弱点"],
                  "importance": 0.8,
                  "confidence": null
                }
              ]
            }
            """
        )
    )

    result = await extractor.extract(
        user_id="user_1",
        turn_id="turn_null_scores",
        user_text="我喜欢恐龙",
        assistant_text="好的",
        current_profile={},
    )

    assert len(result["structured_memories"]) == 1
    assert result["structured_memories"][0]["title"] == "喜欢恐龙"


@pytest.mark.asyncio
async def test_extractor_raises_real_type_error_not_caused_by_signature() -> None:
    extractor = ProfileMemoryExtractor(llm_service=InternalTypeErrorLLMService())

    with pytest.raises(TypeError, match="service exploded internally"):
        await extractor.extract(
            user_id="user_1",
            turn_id="turn_5",
            user_text="我喜欢恐龙",
            assistant_text="好的",
            current_profile={},
        )


@pytest.mark.asyncio
async def test_extractor_raises_misleading_memory_block_type_error_from_supported_service() -> None:
    extractor = ProfileMemoryExtractor(
        llm_service=MisleadingMemoryBlockTypeErrorLLMService()
    )

    with pytest.raises(
        TypeError,
        match="unexpected keyword argument 'memory_block' while rendering template",
    ):
        await extractor.extract(
            user_id="user_1",
            turn_id="turn_misleading",
            user_text="我喜欢恐龙",
            assistant_text="好的",
            current_profile={},
        )


def test_string_list_normalizes_values() -> None:
    extractor = ProfileMemoryExtractor()

    assert extractor._string_list([" 恐龙 ", "", None, "故事", "恐龙", 7]) == [
        "恐龙",
        "故事",
        "7",
    ]
    assert extractor._string_list("not-a-list") == []
