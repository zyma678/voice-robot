import json

from voice_robot.memory.file_retriever import FileMemoryRetriever
from voice_robot.memory.file_store import FileMemoryStore


def test_file_store_creates_profile_and_memory(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)

    store.save_profile(
        "xiaoming",
        {
            "name": "小明",
            "grade": "三年级",
            "interests": ["恐龙"],
        },
    )
    memory_id = store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="小明说他喜欢恐龙故事。",
        tags=["恐龙", "故事"],
        importance=0.8,
    )

    assert memory_id.startswith("mem_")
    assert (tmp_path / "profiles" / "xiaoming.json").exists()

    preference_dir = tmp_path / "memories" / "xiaoming" / "preference"
    markdown_files = list(preference_dir.glob("*.md"))
    assert len(markdown_files) == 1
    markdown_content = markdown_files[0].read_text(encoding="utf-8")
    assert "# 喜欢恐龙" in markdown_content
    assert "小明说他喜欢恐龙故事。" in markdown_content

    index_path = tmp_path / "memories" / "xiaoming" / "index.json"
    assert index_path.exists()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index == [
        {
            "id": memory_id,
            "category": "preference",
            "title": "喜欢恐龙",
            "summary": "小明说他喜欢恐龙故事。",
            "tags": ["恐龙", "故事"],
            "importance": 0.8,
            "status": "active",
            "confidence": 1.0,
            "created_at": index[0]["created_at"],
            "updated_at": index[0]["updated_at"],
            "last_accessed": index[0]["last_accessed"],
            "source_turn_id": "",
            "path": str(markdown_files[0].relative_to(tmp_path)),
        }
    ]
    assert index[0]["path"].startswith("memories/xiaoming/preference/")
    assert index[0]["path"].endswith(".md")


def test_retriever_returns_relevant_top_memory(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    store.save_memory(
        "xiaoming",
        category="learning",
        title="乘法口诀薄弱",
        content="7x8 曾经回答错，需要多练习。",
        tags=["数学", "乘法"],
        importance=0.9,
    )
    store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="喜欢恐龙和古生物故事。",
        tags=["恐龙"],
        importance=0.6,
    )

    retriever = FileMemoryRetriever(tmp_path)
    results = retriever.search("帮我练习 7x8 乘法", "xiaoming", top_k=1)

    assert len(results) == 1
    assert results[0]["title"] == "乘法口诀薄弱"


def test_store_sanitizes_user_id_and_category_path_components(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)

    memory_id = store.save_memory(
        "../xiaoming",
        category="../preference",
        title="喜欢恐龙",
        content="小明说他喜欢恐龙故事。",
        tags=["恐龙"],
        importance=1.0,
    )

    index_files = list(tmp_path.glob("memories/*/index.json"))
    markdown_files = list(tmp_path.glob("memories/*/*/*.md"))
    assert len(index_files) == 1
    assert len(markdown_files) == 1

    for path in [*index_files, *markdown_files]:
        assert path.is_relative_to(tmp_path)
        assert ".." not in path.relative_to(tmp_path).parts

    index = json.loads(index_files[0].read_text(encoding="utf-8"))
    assert index[0]["id"] == memory_id
    assert index[0]["category"] == "preference"
    assert index[0]["path"] == str(markdown_files[0].relative_to(tmp_path))


def test_retriever_ignores_unmatched_high_importance_memory(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="喜欢恐龙和古生物故事。",
        tags=["恐龙"],
        importance=1.0,
    )

    retriever = FileMemoryRetriever(tmp_path)
    results = retriever.search("练习英语单词", "xiaoming")

    assert results == []


def test_file_store_loads_and_patches_profile(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)

    assert store.load_profile("../xiaoming") == {"user_id": "xiaoming"}

    store.save_profile(
        "../xiaoming",
        {
            "user_id": "old-id",
            "name": "小明",
            "interests": ["恐龙"],
            "grade": "二年级",
        },
    )

    profile = store.patch_profile(
        "../xiaoming",
        {
            "interests": [" 恐龙 ", "", "数学", "恐龙", "故事"],
            "grade": "三年级",
        },
    )

    assert profile["user_id"] == "xiaoming"
    assert profile["name"] == "小明"
    assert profile["interests"] == ["恐龙", "数学", "故事"]
    assert profile["grade"] == "三年级"
    assert profile["updated_at"]
    assert store.load_profile("../xiaoming") == profile


def test_file_store_update_and_delete_memory_entry(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    memory_id = store.save_memory(
        "xiaoming",
        category="learning",
        title="旧标题",
        content="旧内容",
        tags=["数学"],
        importance=0.4,
    )

    updated = store.update_memory(
        "xiaoming",
        memory_id,
        {
            "title": "乘法口诀薄弱",
            "content": "7x8 曾经回答错，需要多练习。",
            "tags": ["数学", " 乘法 ", "数学", ""],
            "importance": 0.95,
            "status": "resolved",
            "confidence": 0.8,
            "source_turn_id": "turn-123",
        },
    )

    assert updated is not None
    assert updated["id"] == memory_id
    assert updated["title"] == "乘法口诀薄弱"
    assert updated["summary"] == "7x8 曾经回答错，需要多练习。"
    assert updated["tags"] == ["数学", "乘法"]
    assert updated["importance"] == 0.95
    assert updated["status"] == "resolved"
    assert updated["confidence"] == 0.8
    assert updated["source_turn_id"] == "turn-123"
    assert updated["created_at"]
    assert updated["updated_at"]

    index = store.load_index("xiaoming")
    assert index == [updated]

    markdown_path = tmp_path / updated["path"]
    markdown_content = markdown_path.read_text(encoding="utf-8")
    assert f'id: "{memory_id}"' in markdown_content
    assert 'category: "learning"' in markdown_content
    assert "importance: 0.95" in markdown_content
    assert 'status: "resolved"' in markdown_content
    assert "confidence: 0.8" in markdown_content
    assert 'source_turn_id: "turn-123"' in markdown_content
    assert 'tags: ["数学", "乘法"]' in markdown_content
    assert "# 乘法口诀薄弱" in markdown_content
    assert "7x8 曾经回答错，需要多练习。" in markdown_content

    assert store.delete_memory("xiaoming", memory_id) is True
    assert store.load_index("xiaoming") == []
    assert not markdown_path.exists()


def test_retriever_ignores_deleted_memory_entries(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    deleted_id = store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="喜欢恐龙和古生物故事。",
        tags=["恐龙"],
        importance=1.0,
    )
    store.save_memory(
        "xiaoming",
        category="learning",
        title="恐龙英文词汇",
        content="可以练习 dinosaur 的拼写。",
        tags=["恐龙", "英语"],
        importance=0.4,
    )
    assert store.update_memory("xiaoming", deleted_id, {"status": "deleted"}) is not None

    retriever = FileMemoryRetriever(tmp_path)
    results = retriever.search("恐龙", "xiaoming", top_k=5)

    assert [entry["id"] for entry in results] != [deleted_id]
    assert all(entry["id"] != deleted_id for entry in results)
    assert [entry["title"] for entry in results] == ["恐龙英文词汇"]


def test_file_store_update_and_delete_missing_memory(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)

    assert store.update_memory("xiaoming", "missing", {"title": "新标题"}) is None
    assert store.delete_memory("xiaoming", "missing") is False


def test_file_store_delete_memory_with_missing_markdown_file(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    memory_id = store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="喜欢恐龙和古生物故事。",
        tags=["恐龙"],
        importance=1.0,
    )
    entry = store.load_index("xiaoming")[0]
    (tmp_path / entry["path"]).unlink()

    assert store.delete_memory("xiaoming", memory_id) is True
    assert store.load_index("xiaoming") == []


def test_file_store_rejects_paths_outside_current_user_memory_subtree(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    memory_id = store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="喜欢恐龙和古生物故事。",
        tags=["恐龙"],
        importance=1.0,
    )
    index_path = tmp_path / "memories" / "xiaoming" / "index.json"
    entries = json.loads(index_path.read_text(encoding="utf-8"))
    entries[0]["path"] = "profiles/xiaoming.json"
    index_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    assert store.update_memory("xiaoming", memory_id, {"title": "新标题"}) is None
    assert store.delete_memory("xiaoming", memory_id) is False
    assert store.load_index("xiaoming")[0]["path"] == "profiles/xiaoming.json"


def test_file_store_rejects_paths_in_other_user_memory_subtree(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    memory_id = store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="喜欢恐龙和古生物故事。",
        tags=["恐龙"],
        importance=1.0,
    )
    index_path = tmp_path / "memories" / "xiaoming" / "index.json"
    entries = json.loads(index_path.read_text(encoding="utf-8"))
    entries[0]["path"] = "memories/other_user/preference/other.md"
    index_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    assert store.update_memory("xiaoming", memory_id, {"title": "新标题"}) is None
    assert store.delete_memory("xiaoming", memory_id) is False
    assert store.load_index("xiaoming")[0]["path"] == "memories/other_user/preference/other.md"


def test_file_store_escapes_frontmatter_values(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    memory_id = store.save_memory(
        "xiaoming",
        category="learning",
        title="旧标题",
        content="旧内容",
        tags=["数学"],
        importance=0.4,
    )

    updated = store.update_memory(
        "xiaoming",
        memory_id,
        {
            "status": 'resolved\\done "done"\nnow',
            "source_turn_id": 'turn\\42 "42"\nline2',
        },
    )

    assert updated is not None
    markdown_path = tmp_path / updated["path"]
    markdown_content = markdown_path.read_text(encoding="utf-8")
    assert 'status: "resolved\\\\done \\"done\\" now"' in markdown_content
    assert 'source_turn_id: "turn\\\\42 \\"42\\" line2"' in markdown_content


def test_file_store_update_and_delete_ignore_invalid_index_entries(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    index_path = tmp_path / "memories" / "xiaoming" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            [
                {"path": "memories/xiaoming/preference/a.md"},
                {"id": "mem_missing_path"},
            ],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    assert (
        store.update_memory("xiaoming", "mem_missing_path", {"title": "新标题"}) is None
    )
    assert store.delete_memory("xiaoming", "mem_missing_path") is False


def test_file_store_accepts_zero_importance_for_update_and_delete(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    memory_id = store.save_memory(
        "xiaoming",
        category="learning",
        title="需要鼓励",
        content="先从简单题开始。",
        tags=["数学"],
        importance=0.0,
    )

    updated = store.update_memory(
        "xiaoming",
        memory_id,
        {"status": "resolved", "source_turn_id": "turn-zero"},
    )

    assert updated is not None
    assert updated["importance"] == 0.0
    assert updated["status"] == "resolved"
    assert updated["source_turn_id"] == "turn-zero"
    assert store.delete_memory("xiaoming", memory_id) is True
    assert store.load_index("xiaoming") == []


def test_retriever_skips_invalid_index_entries_without_crashing(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    valid_id = store.save_memory(
        "xiaoming",
        category="learning",
        title="恐龙英文词汇",
        content="可以练习 dinosaur 的拼写。",
        tags=["恐龙", "英语"],
        importance=0.4,
    )
    index_path = tmp_path / "memories" / "xiaoming" / "index.json"
    entries = json.loads(index_path.read_text(encoding="utf-8"))
    entries.extend(
        [
            {
                "id": "bad_none_title",
                "category": "learning",
                "title": None,
                "summary": "坏数据",
                "tags": ["恐龙"],
                "importance": 0.5,
                "status": "active",
                "path": "memories/xiaoming/learning/bad1.md",
            },
            {
                "id": "bad_tags_type",
                "category": "learning",
                "title": "坏标签类型",
                "summary": "坏数据",
                "tags": [1, 2],
                "importance": 0.5,
                "status": "active",
                "path": "memories/xiaoming/learning/bad2.md",
            },
            {
                "id": "bad_importance_type",
                "category": "learning",
                "title": "坏重要度类型",
                "summary": "坏数据",
                "tags": ["恐龙"],
                "importance": "bad",
                "status": "active",
                "path": "memories/xiaoming/learning/bad3.md",
            },
        ]
    )
    index_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    retriever = FileMemoryRetriever(tmp_path)
    results = retriever.search("恐龙", "xiaoming", top_k=5)

    assert [entry["id"] for entry in results] == [valid_id]
