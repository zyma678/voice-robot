import json

from voice_robot.memory import SummaryStore, build_local_summary


def test_save_summary_writes_markdown_and_index(tmp_path) -> None:
    store = SummaryStore(tmp_path)

    summary = store.save_summary(
        "xiaoming",
        start_sequence=1,
        end_sequence=20,
        content="小明喜欢恐龙故事，也想练习乘法。",
        summary_source="local",
    )

    summary_path = tmp_path / "summaries" / "xiaoming" / "summary_000001_000020.md"
    index_path = tmp_path / "summaries" / "xiaoming" / "index.json"

    assert summary["summary_id"] == "sum_000001_000020"
    assert summary["path"] == "summaries/xiaoming/summary_000001_000020.md"
    markdown = summary_path.read_text(encoding="utf-8")
    assert 'summary_id: "sum_000001_000020"' in markdown
    assert "# 对话摘要 1-20" in markdown
    assert "小明喜欢恐龙故事，也想练习乘法。" in markdown

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index == [summary]
    assert store.list_summaries("xiaoming") == [summary]


def test_store_sanitizes_unsafe_user_id_path_component(tmp_path) -> None:
    store = SummaryStore(tmp_path)

    summary = store.save_summary(
        "../xiao/ming?!小明",
        start_sequence=1,
        end_sequence=20,
        content="路径安全摘要",
        summary_source="local",
    )

    summary_path = tmp_path / "summaries" / "xiao_ming_小明" / "summary_000001_000020.md"
    index_path = tmp_path / "summaries" / "xiao_ming_小明" / "index.json"

    assert summary_path.exists()
    assert index_path.exists()
    assert ".." not in summary_path.relative_to(tmp_path).parts
    assert summary["path"] == "summaries/xiao_ming_小明/summary_000001_000020.md"
    assert store.list_summaries("../xiao/ming?!小明") == [summary]


def test_save_summary_replaces_duplicate_summary_id(tmp_path) -> None:
    store = SummaryStore(tmp_path)

    first = store.save_summary(
        "xiaoming",
        start_sequence=1,
        end_sequence=20,
        content="第一次摘要",
        summary_source="local",
    )
    second = store.save_summary(
        "xiaoming",
        start_sequence=1,
        end_sequence=20,
        content="第二次摘要",
        summary_source="manual",
    )

    summaries = store.list_summaries("xiaoming")
    assert len(summaries) == 1
    assert summaries == [second]
    assert summaries[0]["summary_id"] == first["summary_id"]
    assert summaries[0]["summary_source"] == "manual"
    assert store.load_summary_content("xiaoming", second["summary_id"]) == "第二次摘要"


def test_recent_summaries_returns_latest_by_end_sequence(tmp_path) -> None:
    store = SummaryStore(tmp_path)
    for start, end in [(1, 20), (21, 40), (41, 60), (61, 80)]:
        store.save_summary(
            "xiaoming",
            start_sequence=start,
            end_sequence=end,
            content=f"摘要 {start}-{end}",
            summary_source="local",
        )

    recent = store.list_recent_summaries("xiaoming", limit=3)

    assert [(item["start_sequence"], item["end_sequence"]) for item in recent] == [
        (21, 40),
        (41, 60),
        (61, 80),
    ]


def test_recent_summaries_limit_zero_returns_empty_list(tmp_path) -> None:
    store = SummaryStore(tmp_path)
    store.save_summary(
        "xiaoming",
        start_sequence=1,
        end_sequence=20,
        content="摘要",
        summary_source="local",
    )

    assert store.list_recent_summaries("xiaoming", limit=0) == []


def test_update_and_delete_summary_sync_index(tmp_path) -> None:
    store = SummaryStore(tmp_path)
    summary = store.save_summary(
        "xiaoming",
        start_sequence=1,
        end_sequence=20,
        content="旧摘要",
        summary_source="local",
    )

    updated = store.update_summary(
        "xiaoming",
        summary["summary_id"],
        content="新摘要",
        summary_source="manual",
    )

    assert updated is not None
    assert updated["summary_id"] == summary["summary_id"]
    assert updated["summary_source"] == "manual"
    assert store.load_summary_content("xiaoming", summary["summary_id"]) == "新摘要"

    assert store.delete_summary("xiaoming", summary["summary_id"]) is True
    assert store.get_summary("xiaoming", summary["summary_id"]) is None
    assert store.list_summaries("xiaoming") == []
    assert not (tmp_path / "summaries" / "xiaoming" / "summary_000001_000020.md").exists()


def test_update_and_delete_missing_summary_return_empty_results(tmp_path) -> None:
    store = SummaryStore(tmp_path)

    assert (
        store.update_summary(
            "xiaoming",
            "sum_missing",
            content="新摘要",
            summary_source="manual",
        )
        is None
    )
    assert store.delete_summary("xiaoming", "sum_missing") is False


def test_delete_summary_syncs_index_when_markdown_file_is_already_missing(tmp_path) -> None:
    store = SummaryStore(tmp_path)
    summary = store.save_summary(
        "xiaoming",
        start_sequence=1,
        end_sequence=20,
        content="摘要",
        summary_source="local",
    )
    summary_path = tmp_path / summary["path"]
    summary_path.unlink()

    assert store.delete_summary("xiaoming", summary["summary_id"]) is True
    assert store.list_summaries("xiaoming") == []
    assert json.loads((tmp_path / "summaries" / "xiaoming" / "index.json").read_text()) == []


def test_local_summary_extracts_compact_lines_from_turns() -> None:
    turns = [
        {
            "sequence": 1,
            "user_text": "我喜欢恐龙故事，尤其是霸王龙和三角龙。",
            "assistant_text": "可以讲一个恐龙冒险故事。",
        },
        {
            "sequence": 2,
            "user_text": "我不会 7x8，能帮我记住吗？",
            "assistant_text": "7x8 等于 56，可以用 7 个 8 相加来记。",
        },
    ]

    summary = build_local_summary(turns)

    assert "- 对话范围：1-2。" in summary
    assert "喜欢恐龙故事" in summary
    assert "不会 7x8" in summary


def test_local_summary_only_uses_first_twenty_turns_and_truncates_long_text() -> None:
    long_user_text = "喜欢恐龙故事" + "很长" * 60
    turns = [
        {
            "sequence": sequence,
            "user_text": long_user_text if sequence == 1 else f"用户第 {sequence} 轮",
            "assistant_text": f"助手第 {sequence} 轮",
        }
        for sequence in range(1, 22)
    ]

    summary = build_local_summary(turns)

    assert "- 对话范围：1-20。" in summary
    assert "喜欢恐龙故事" in summary
    assert "第 21 轮" not in summary
    assert "用户第 21 轮" not in summary
    assert "助手第 21 轮" not in summary
    first_user_line = next(line for line in summary.splitlines() if line.startswith("- 第 1 轮用户："))
    first_user_text = first_user_line.removeprefix("- 第 1 轮用户：")
    assert len(first_user_text) <= 83
    assert first_user_text.endswith("...")
