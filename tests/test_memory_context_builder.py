from voice_robot.memory.context_builder import MemoryContextBuilder
from voice_robot.memory.file_store import FileMemoryStore


def _append_turns(builder: MemoryContextBuilder, user_id: str, count: int) -> None:
    for sequence in range(1, count + 1):
        builder.conversation_store.append_turn(
            user_id,
            user_text=f"用户第{sequence}轮 " + ("甲" * 20),
            assistant_text=f"助手第{sequence}轮 " + ("乙" * 20),
            reply_source="ark",
            fallback_reason=None,
            safety_passed=True,
        )


def test_build_context_contains_all_sections(tmp_path) -> None:
    user_id = "xiaoming"
    query = "恐龙故事"
    builder = MemoryContextBuilder(tmp_path)
    store = FileMemoryStore(tmp_path)
    store.save_profile(
        user_id,
        {
            "name": "小明",
            "grade": "三年级",
            "interests": ["恐龙", "故事"],
        },
    )
    for index in range(1, 5):
        builder.summary_store.save_summary(
            user_id,
            start_sequence=index,
            end_sequence=index,
            content=f"长期摘要{index}",
            summary_source="local",
        )
    for index in range(1, 4):
        store.save_memory(
            user_id,
            category="preference",
            title=f"结构化记忆{index}",
            content=f"提及恐龙故事{index}",
            tags=["恐龙", "故事"],
            importance=0.9 - index * 0.1,
        )
    _append_turns(builder, user_id, 5)

    context = builder.build(user_id, query)

    assert "【用户画像】" in context
    assert "【长期摘要】" in context
    assert "【结构化记忆】" in context
    assert "【最近 8 轮对话】" in context
    assert "小明" in context
    assert "长期摘要3" in context
    assert "长期摘要4" in context
    assert "结构化记忆1" in context
    assert "结构化记忆3" not in context
    assert "用户第1轮" in context
    assert "助手第5轮" in context


def test_build_context_keeps_recent_8_turns(tmp_path) -> None:
    user_id = "xiaoming"
    builder = MemoryContextBuilder(tmp_path)
    _append_turns(builder, user_id, 10)

    context = builder.build(user_id, "任意问题")

    assert "用户第1轮" not in context
    assert "助手第1轮" not in context
    assert "用户第2轮" not in context
    assert "助手第2轮" not in context
    assert "用户第3轮" in context
    assert "助手第10轮" in context
    assert "【最近 8 轮对话】" in context


def test_default_context_budget_and_item_limits(tmp_path) -> None:
    builder = MemoryContextBuilder(tmp_path)

    assert builder.max_chars == 3200
    assert builder.recent_turn_limit == 8
    assert builder.summary_limit == 2
    assert builder.structured_max_items == 2

    store = FileMemoryStore(tmp_path)
    store.save_profile("xiaoming", {"bio": "画像" * 2000})
    for sequence in range(1, 13):
        builder.conversation_store.append_turn(
            "xiaoming",
            user_text=f"用户第{sequence}轮 " + ("甲" * 600),
            assistant_text=f"助手第{sequence}轮 " + ("乙" * 600),
            reply_source="ark",
            fallback_reason=None,
            safety_passed=True,
        )

    context = builder.build("xiaoming", "任意问题", structured_memories=[])

    assert len(context) <= 3200
    for sequence in range(5, 13):
        assert f"第{sequence}轮用户" in context
        assert f"第{sequence}轮助手" in context


def test_build_reuses_supplied_structured_memories_without_search(tmp_path) -> None:
    builder = MemoryContextBuilder(tmp_path)

    def unexpected_search(*args, **kwargs):
        raise AssertionError("builder should reuse the supplied retrieval result")

    builder.retriever.search = unexpected_search
    context = builder.build(
        "xiaoming",
        "恐龙故事",
        structured_memories=[
            {"title": "喜欢恐龙", "summary": "喜欢恐龙故事", "tags": ["恐龙"]},
            {"title": "喜欢数学", "summary": "正在练乘法", "tags": ["数学"]},
            {"title": "不应进入上下文", "summary": "超出默认上限", "tags": []},
        ],
    )

    assert "喜欢恐龙" in context
    assert "喜欢数学" in context
    assert "不应进入上下文" not in context


def test_build_context_respects_max_length_and_has_no_audit_field(tmp_path) -> None:
    user_id = "xiaoming"
    builder = MemoryContextBuilder(
        tmp_path,
        max_chars=1200,
        summary_item_max_chars=1000,
        turn_item_max_chars=500,
    )
    store = FileMemoryStore(tmp_path)
    store.save_profile(user_id, {"name": "小明", "bio": "丙" * 2000})
    for index in range(1, 4):
        builder.summary_store.save_summary(
            user_id,
            start_sequence=index,
            end_sequence=index,
            content=f"摘要{index}-" + ("丁" * 1500),
            summary_source="local",
        )
        store.save_memory(
            user_id,
            category="preference",
            title=f"记忆{index}",
            content="戊" * 1200,
            tags=["恐龙"],
            importance=1.0,
        )
    _append_turns(builder, user_id, 20)

    context = builder.build(user_id, "恐龙")

    assert len(context) <= 1200
    assert "审计" not in context


def test_build_context_keeps_recent_turns_when_profile_and_structured_are_very_long(
    tmp_path,
) -> None:
    user_id = "xiaoming"
    builder = MemoryContextBuilder(
        tmp_path,
        max_chars=2000,
        summary_item_max_chars=300,
        turn_item_max_chars=120,
    )
    store = FileMemoryStore(tmp_path)

    very_long_profile = {
        f"field_{index}": ("甲乙丙丁" * 300)
        for index in range(1, 40)
    }
    store.save_profile(user_id, very_long_profile)
    builder.summary_store.save_summary(
        user_id,
        start_sequence=1,
        end_sequence=1,
        content="长期摘要内容",
        summary_source="local",
    )
    for index in range(1, 4):
        store.save_memory(
            user_id,
            category="preference",
            title=f"超长结构化记忆{index}",
            content=("内容" * 1000),
            tags=["恐龙", "故事", "学习"] * 50,
            importance=0.9,
        )
    _append_turns(builder, user_id, 5)

    context = builder.build(user_id, "恐龙")

    assert "【用户画像】" in context
    assert "【长期摘要】" in context
    assert "【结构化记忆】" in context
    assert "【最近 8 轮对话】" in context
    assert "用户第1轮" in context
    assert "助手第5轮" in context
