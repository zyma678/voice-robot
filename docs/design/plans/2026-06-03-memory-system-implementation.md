# Memory System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为语音机器人补齐按用户持久化的短期记忆、长期摘要、画像抽取和 Web 记忆管理能力。

**Architecture:** 采用已确认的 C 方案：聊天主记录、长期摘要、审计日志、画像和结构化记忆分层存储在 `runtime/memory/` 下，并通过 `MemoryContextBuilder` 统一拼接上下文。`VoiceAgent` 在每轮回复后串行落盘、抽取画像、触发摘要压缩；Web 控制台通过统一管理 API 查看、编辑和删除所有记忆对象。

**Tech Stack:** Python 3.11、FastAPI、pytest、JSONL/JSON/Markdown 文件存储、原生浏览器 JS/CSS、Ark/Doubao Chat Completions 兼容接口。

---

## 需求来源

- 原始 PRD：`docs/prd/esp32-s3-technical-solution.md`
- 记忆系统设计：`docs/design/specs/memory-system-design.md`
- 当前进展：`docs/progress/WORK-IN-PROGRESS.md`

本计划只进入开发准备阶段，不包含实际业务代码改动。执行本计划前，不再读取飞书文档，需求以 `docs/prd/` 为准。

## 文件结构

新增文件：

- `voice_robot/memory/conversation_store.py`：维护主聊天轮次 `conversations/<user_id>/turns.jsonl` 和 `meta.json`，提供追加、查询最近轮次、按范围读取、编辑和删除能力。
- `voice_robot/memory/summary_store.py`：维护长期摘要 `summaries/<user_id>/summary_<start>_<end>.md` 和 `index.json`，提供保存、读取、编辑、删除、最近摘要查询能力。
- `voice_robot/memory/audit_log_store.py`：维护审计日志 `audit/<user_id>/events.jsonl`，提供追加、查询、编辑和删除事件能力。
- `voice_robot/memory/context_builder.py`：把用户画像、最近长期摘要、结构化记忆和最近 20 轮对话拼接为 `memory_block`。
- `voice_robot/memory/profile_extractor.py`：从每轮聊天中抽取用户画像补丁和结构化记忆，Ark/Doubao 优先，本地规则兜底。
- `tests/test_conversation_store.py`：覆盖主聊天存储。
- `tests/test_summary_store.py`：覆盖长期摘要存储和本地摘要器。
- `tests/test_audit_log_store.py`：覆盖审计日志存储。
- `tests/test_memory_context_builder.py`：覆盖上下文拼接和长度控制。
- `tests/test_profile_extractor.py`：覆盖画像抽取和结构化记忆生成。

修改文件：

- `voice_robot/memory/file_store.py`：增加读取、合并、编辑、删除用户画像和结构化记忆条目的方法。
- `voice_robot/memory/file_retriever.py`：保持检索接口不变，必要时过滤删除或非 active 条目。
- `voice_robot/memory/__init__.py`：导出新增存储和上下文构建类。
- `voice_robot/services/llm.py`：`generate_reply()` 增加可选 `memory_block` 参数；新增摘要和抽取调用所需的轻量方法。
- `voice_robot/agent/graph.py`：接入 per-user `asyncio.Lock`、上下文构建、聊天落盘、摘要压缩、画像抽取和审计记录。
- `voice_robot/admin_api.py`：补齐记忆管理 API。
- `voice_robot/server.py`：把 WebSocket 关键事件写入审计日志，保持现有流式消息协议兼容。
- `web_console/index.html`：把记忆管理 Tab 扩展为二级工作区。
- `web_console/app.js`：接入 turns、summaries、profile、entries、audit 的查看、编辑和删除。
- `web_console/styles.css`：补齐记忆管理表格、编辑器、操作栏样式。
- `tests/test_memory.py`：补充 `FileMemoryStore` 的编辑和删除测试。
- `tests/test_llm_service.py`：补充 `memory_block` 兼容测试。
- `tests/test_agent.py`：补充每轮落盘、短期窗口、长期摘要、画像更新、安全拒答落盘测试。
- `tests/test_admin_api.py`：补齐管理 API 测试。
- `tests/test_server.py`：补充 WebSocket 审计事件回归测试。
- `tests/test_web_console_assets.py`：补充前端资产包含新管理入口的静态断言。
- `docs/prd/esp32-s3-technical-solution.md`：确认实现完成后追加开发结果和验收状态。
- `docs/progress/WORK-IN-PROGRESS.md`：执行过程中更新当前进展、剩余事项和验证结果。

---

## 数据模型约定

### Conversation Turn

```python
{
    "turn_id": "turn_20260603_120000_ab12",
    "sequence": 41,
    "created_at": "2026-06-03T12:00:00+00:00",
    "user_text": "讲一个恐龙故事",
    "assistant_text": "小恐龙找到一片星星叶，送给迷路的小兔。",
    "reply_source": "ark",
    "fallback_reason": "",
    "safety_passed": True,
}
```

### Conversation Meta

```python
{
    "user_id": "xiaoming",
    "next_sequence": 42,
    "last_summarized_sequence": 20,
    "updated_at": "2026-06-03T12:00:00+00:00",
}
```

### Summary Index Entry

```python
{
    "summary_id": "sum_000001_000020",
    "user_id": "xiaoming",
    "start_sequence": 1,
    "end_sequence": 20,
    "created_at": "2026-06-03T12:00:00+00:00",
    "summary_source": "local",
    "path": "summaries/xiaoming/summary_000001_000020.md",
}
```

### Audit Event

```python
{
    "event_id": "evt_20260603_120000_ab12",
    "created_at": "2026-06-03T12:00:00+00:00",
    "event_type": "llm_reply_delta",
    "turn_id": "turn_20260603_120000_ab12",
    "payload": {"text": "小恐龙找到"},
}
```

---

## Task 1: ConversationStore

**Files:**

- Create: `voice_robot/memory/conversation_store.py`
- Create: `tests/test_conversation_store.py`
- Modify: `voice_robot/memory/__init__.py`

- [ ] **Step 1: Write failing tests for append, sequence and recent window**

Add `tests/test_conversation_store.py`:

```python
import json

from voice_robot.memory.conversation_store import ConversationStore


def test_append_turn_assigns_sequence_and_updates_meta(tmp_path) -> None:
    store = ConversationStore(tmp_path)

    first = store.append_turn(
        "xiaoming",
        user_text="讲一个恐龙故事",
        assistant_text="小恐龙把星星叶送给小兔。",
        reply_source="ark",
        fallback_reason="",
        safety_passed=True,
    )
    second = store.append_turn(
        "xiaoming",
        user_text="再讲一个",
        assistant_text="小恐龙学会了分享。",
        reply_source="fallback-local",
        fallback_reason="timeout",
        safety_passed=True,
    )

    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["turn_id"].startswith("turn_")
    assert second["turn_id"] != first["turn_id"]

    meta = json.loads(
        (tmp_path / "conversations" / "xiaoming" / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["user_id"] == "xiaoming"
    assert meta["next_sequence"] == 3
    assert meta["last_summarized_sequence"] == 0


def test_recent_turns_returns_latest_window_in_ascending_order(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    for index in range(25):
        store.append_turn(
            "xiaoming",
            user_text=f"用户第 {index + 1} 轮",
            assistant_text=f"助手第 {index + 1} 轮",
            reply_source="local-rule",
            fallback_reason="",
            safety_passed=True,
        )

    recent = store.list_recent_turns("xiaoming", limit=20)

    assert [turn["sequence"] for turn in recent] == list(range(6, 26))
    assert recent[0]["user_text"] == "用户第 6 轮"
    assert recent[-1]["assistant_text"] == "助手第 25 轮"


def test_turns_can_be_read_by_sequence_range(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    for index in range(5):
        store.append_turn(
            "xiaoming",
            user_text=f"u{index + 1}",
            assistant_text=f"a{index + 1}",
            reply_source="ark",
            fallback_reason="",
            safety_passed=True,
        )

    turns = store.list_turns("xiaoming", start_sequence=2, end_sequence=4)

    assert [turn["sequence"] for turn in turns] == [2, 3, 4]
```

- [ ] **Step 2: Run tests and verify import failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_conversation_store.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'voice_robot.memory.conversation_store'`.

- [ ] **Step 3: Add minimal ConversationStore implementation**

Create `voice_robot/memory/conversation_store.py`:

```python
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class ConversationStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def append_turn(
        self,
        user_id: str,
        *,
        user_text: str,
        assistant_text: str,
        reply_source: str,
        fallback_reason: str,
        safety_passed: bool,
    ) -> dict:
        safe_user_id = self._safe_component(user_id)
        meta = self.load_meta(safe_user_id)
        sequence = int(meta["next_sequence"])
        now = self._now()
        turn = {
            "turn_id": f"turn_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:4]}",
            "sequence": sequence,
            "created_at": now,
            "user_text": user_text,
            "assistant_text": assistant_text,
            "reply_source": reply_source,
            "fallback_reason": fallback_reason,
            "safety_passed": safety_passed,
        }
        turns_path = self._turns_path(safe_user_id)
        turns_path.parent.mkdir(parents=True, exist_ok=True)
        with turns_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(turn, ensure_ascii=False, sort_keys=True) + "\n")
        meta["next_sequence"] = sequence + 1
        meta["updated_at"] = now
        self.save_meta(safe_user_id, meta)
        return turn

    def list_recent_turns(self, user_id: str, *, limit: int = 20) -> list[dict]:
        turns = self.list_turns(user_id)
        return turns[-limit:]

    def list_turns(
        self,
        user_id: str,
        *,
        start_sequence: int | None = None,
        end_sequence: int | None = None,
    ) -> list[dict]:
        safe_user_id = self._safe_component(user_id)
        turns = self._read_turns(safe_user_id)
        if start_sequence is not None:
            turns = [turn for turn in turns if int(turn["sequence"]) >= start_sequence]
        if end_sequence is not None:
            turns = [turn for turn in turns if int(turn["sequence"]) <= end_sequence]
        return sorted(turns, key=lambda turn: int(turn["sequence"]))

    def load_meta(self, user_id: str) -> dict:
        safe_user_id = self._safe_component(user_id)
        path = self._meta_path(safe_user_id)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {
            "user_id": safe_user_id,
            "next_sequence": 1,
            "last_summarized_sequence": 0,
            "updated_at": self._now(),
        }

    def save_meta(self, user_id: str, meta: dict) -> None:
        safe_user_id = self._safe_component(user_id)
        path = self._meta_path(safe_user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _read_turns(self, safe_user_id: str) -> list[dict]:
        path = self._turns_path(safe_user_id)
        if not path.exists():
            return []
        turns: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                turns.append(payload)
        return turns

    def _turns_path(self, safe_user_id: str) -> Path:
        return self.root / "conversations" / safe_user_id / "turns.jsonl"

    def _meta_path(self, safe_user_id: str) -> Path:
        return self.root / "conversations" / safe_user_id / "meta.json"

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", value).strip("_") or "item"
```

Modify `voice_robot/memory/__init__.py`:

```python
from voice_robot.memory.conversation_store import ConversationStore

__all__ = ["ConversationStore"]
```

- [ ] **Step 4: Run ConversationStore tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_conversation_store.py -q
```

Expected: PASS.

- [ ] **Step 5: Add edit and delete tests**

Append to `tests/test_conversation_store.py`:

```python
def test_update_turn_rewrites_turn_and_keeps_backup(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    turn = store.append_turn(
        "xiaoming",
        user_text="原始输入",
        assistant_text="原始回复",
        reply_source="ark",
        fallback_reason="",
        safety_passed=True,
    )

    updated = store.update_turn(
        "xiaoming",
        turn["turn_id"],
        {"user_text": "编辑后的输入", "assistant_text": "编辑后的回复"},
    )

    assert updated["user_text"] == "编辑后的输入"
    assert updated["assistant_text"] == "编辑后的回复"
    turns = store.list_turns("xiaoming")
    assert turns[0]["turn_id"] == turn["turn_id"]
    assert turns[0]["user_text"] == "编辑后的输入"
    backups = list((tmp_path / "conversations" / "xiaoming").glob("turns.jsonl.*.bak"))
    assert len(backups) == 1


def test_delete_turn_removes_only_matching_turn(tmp_path) -> None:
    store = ConversationStore(tmp_path)
    first = store.append_turn(
        "xiaoming",
        user_text="第一轮",
        assistant_text="回复一",
        reply_source="ark",
        fallback_reason="",
        safety_passed=True,
    )
    second = store.append_turn(
        "xiaoming",
        user_text="第二轮",
        assistant_text="回复二",
        reply_source="ark",
        fallback_reason="",
        safety_passed=True,
    )

    deleted = store.delete_turn("xiaoming", first["turn_id"])

    assert deleted is True
    assert [turn["turn_id"] for turn in store.list_turns("xiaoming")] == [
        second["turn_id"]
    ]


def test_update_missing_turn_returns_none(tmp_path) -> None:
    store = ConversationStore(tmp_path)

    assert store.update_turn("xiaoming", "turn_missing", {"user_text": "x"}) is None
```

- [ ] **Step 6: Run tests and verify missing methods fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_conversation_store.py -q
```

Expected: FAIL with `AttributeError` for `update_turn` or `delete_turn`.

- [ ] **Step 7: Implement edit/delete rewrite with backup**

Add these methods to `ConversationStore`:

```python
    def update_turn(self, user_id: str, turn_id: str, patch: dict) -> dict | None:
        safe_user_id = self._safe_component(user_id)
        turns = self._read_turns(safe_user_id)
        allowed_fields = {
            "user_text",
            "assistant_text",
            "reply_source",
            "fallback_reason",
            "safety_passed",
        }
        updated_turn: dict | None = None
        for turn in turns:
            if turn.get("turn_id") == turn_id:
                for field in allowed_fields:
                    if field in patch:
                        turn[field] = patch[field]
                updated_turn = turn
                break
        if updated_turn is None:
            return None
        self._rewrite_turns(safe_user_id, turns)
        return updated_turn

    def delete_turn(self, user_id: str, turn_id: str) -> bool:
        safe_user_id = self._safe_component(user_id)
        turns = self._read_turns(safe_user_id)
        remaining = [turn for turn in turns if turn.get("turn_id") != turn_id]
        if len(remaining) == len(turns):
            return False
        self._rewrite_turns(safe_user_id, remaining)
        return True

    def _rewrite_turns(self, safe_user_id: str, turns: list[dict]) -> None:
        path = self._turns_path(safe_user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = path.with_name(
                f"{path.name}.{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.bak"
            )
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        tmp_path = path.with_suffix(".jsonl.tmp")
        tmp_path.write_text(
            "".join(
                json.dumps(turn, ensure_ascii=False, sort_keys=True) + "\n"
                for turn in sorted(turns, key=lambda item: int(item["sequence"]))
            ),
            encoding="utf-8",
        )
        tmp_path.replace(path)
```

- [ ] **Step 8: Run ConversationStore tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_conversation_store.py -q
```

Expected: PASS.

- [ ] **Step 9: Checkpoint**

Run:

```bash
.venv/bin/python -m pytest tests/test_conversation_store.py tests/test_memory.py -q
```

Expected: PASS.

Commit note: 当前目录若仍不是 git 仓库，记录“commit 不适用”；如果执行环境已有 git 仓库，提交信息使用 `feat: add conversation memory store`。

---

## Task 2: SummaryStore and Local Summarizer

**Files:**

- Create: `voice_robot/memory/summary_store.py`
- Create: `tests/test_summary_store.py`
- Modify: `voice_robot/memory/__init__.py`

- [ ] **Step 1: Write failing tests for save/list/update/delete**

Add `tests/test_summary_store.py`:

```python
from voice_robot.memory.summary_store import SummaryStore, build_local_summary


def test_save_summary_writes_markdown_and_index(tmp_path) -> None:
    store = SummaryStore(tmp_path)

    summary = store.save_summary(
        "xiaoming",
        start_sequence=1,
        end_sequence=20,
        content="- 用户喜欢恐龙故事。\n- 用户偏好短句。",
        summary_source="local",
    )

    assert summary["summary_id"] == "sum_000001_000020"
    assert summary["path"] == "summaries/xiaoming/summary_000001_000020.md"
    markdown = (tmp_path / summary["path"]).read_text(encoding="utf-8")
    assert 'summary_id: "sum_000001_000020"' in markdown
    assert "# 对话摘要 1-20" in markdown
    assert "用户喜欢恐龙故事" in markdown
    assert store.list_summaries("xiaoming")[0]["summary_id"] == summary["summary_id"]


def test_recent_summaries_returns_latest_by_end_sequence(tmp_path) -> None:
    store = SummaryStore(tmp_path)
    for start, end in [(1, 20), (21, 40), (41, 60), (61, 80)]:
        store.save_summary(
            "xiaoming",
            start_sequence=start,
            end_sequence=end,
            content=f"- 摘要 {start}-{end}",
            summary_source="local",
        )

    recent = store.list_recent_summaries("xiaoming", limit=3)

    assert [item["summary_id"] for item in recent] == [
        "sum_000021_000040",
        "sum_000041_000060",
        "sum_000061_000080",
    ]


def test_update_and_delete_summary_sync_index(tmp_path) -> None:
    store = SummaryStore(tmp_path)
    summary = store.save_summary(
        "xiaoming",
        start_sequence=1,
        end_sequence=20,
        content="- 旧摘要",
        summary_source="local",
    )

    updated = store.update_summary(
        "xiaoming",
        summary["summary_id"],
        content="- 新摘要",
        summary_source="manual",
    )
    deleted = store.delete_summary("xiaoming", summary["summary_id"])

    assert updated is not None
    assert updated["summary_source"] == "manual"
    assert deleted is True
    assert store.list_summaries("xiaoming") == []
    assert not (tmp_path / summary["path"]).exists()


def test_local_summary_extracts_compact_lines_from_turns() -> None:
    turns = [
        {
            "sequence": 1,
            "user_text": "我喜欢恐龙故事",
            "assistant_text": "小恐龙找到星星叶。",
        },
        {
            "sequence": 2,
            "user_text": "我不会 7x8",
            "assistant_text": "我们一起练习乘法。",
        },
    ]

    summary = build_local_summary(turns)

    assert "1-2" in summary
    assert "喜欢恐龙故事" in summary
    assert "不会 7x8" in summary
```

- [ ] **Step 2: Run tests and verify import failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_summary_store.py -q
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement SummaryStore and local summarizer**

Create `voice_robot/memory/summary_store.py`:

```python
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
        safe_user_id = self._safe_component(user_id)
        summary_id = self._summary_id(start_sequence, end_sequence)
        now = self._now()
        relative_path = (
            Path("summaries")
            / safe_user_id
            / f"summary_{start_sequence:06d}_{end_sequence:06d}.md"
        )
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self._render_markdown(
                summary_id=summary_id,
                user_id=safe_user_id,
                start_sequence=start_sequence,
                end_sequence=end_sequence,
                created_at=now,
                summary_source=summary_source,
                content=content,
            ),
            encoding="utf-8",
        )
        entry = {
            "summary_id": summary_id,
            "user_id": safe_user_id,
            "start_sequence": start_sequence,
            "end_sequence": end_sequence,
            "created_at": now,
            "summary_source": summary_source,
            "path": str(relative_path),
        }
        entries = [
            item
            for item in self.list_summaries(safe_user_id)
            if item.get("summary_id") != summary_id
        ]
        entries.append(entry)
        self._write_index(safe_user_id, entries)
        return entry

    def list_summaries(self, user_id: str) -> list[dict]:
        safe_user_id = self._safe_component(user_id)
        path = self._index_path(safe_user_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return sorted(data, key=lambda item: int(item.get("start_sequence", 0)))

    def list_recent_summaries(self, user_id: str, *, limit: int = 3) -> list[dict]:
        entries = self.list_summaries(user_id)
        return sorted(entries, key=lambda item: int(item.get("end_sequence", 0)))[-limit:]

    def load_summary_content(self, user_id: str, summary_id: str) -> str | None:
        entry = self.get_summary(user_id, summary_id)
        if entry is None:
            return None
        path = self.root / str(entry["path"])
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def get_summary(self, user_id: str, summary_id: str) -> dict | None:
        for entry in self.list_summaries(user_id):
            if entry.get("summary_id") == summary_id:
                return entry
        return None

    def update_summary(
        self,
        user_id: str,
        summary_id: str,
        *,
        content: str,
        summary_source: str,
    ) -> dict | None:
        safe_user_id = self._safe_component(user_id)
        entries = self.list_summaries(safe_user_id)
        match = next((item for item in entries if item.get("summary_id") == summary_id), None)
        if match is None:
            return None
        match["summary_source"] = summary_source
        path = self.root / str(match["path"])
        path.write_text(
            self._render_markdown(
                summary_id=summary_id,
                user_id=safe_user_id,
                start_sequence=int(match["start_sequence"]),
                end_sequence=int(match["end_sequence"]),
                created_at=str(match["created_at"]),
                summary_source=summary_source,
                content=content,
            ),
            encoding="utf-8",
        )
        self._write_index(safe_user_id, entries)
        return match

    def delete_summary(self, user_id: str, summary_id: str) -> bool:
        safe_user_id = self._safe_component(user_id)
        entries = self.list_summaries(safe_user_id)
        match = next((item for item in entries if item.get("summary_id") == summary_id), None)
        if match is None:
            return False
        path = self.root / str(match["path"])
        if path.exists():
            path.unlink()
        self._write_index(
            safe_user_id,
            [item for item in entries if item.get("summary_id") != summary_id],
        )
        return True

    def _write_index(self, safe_user_id: str, entries: list[dict]) -> None:
        path = self._index_path(safe_user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(
                sorted(entries, key=lambda item: int(item.get("start_sequence", 0))),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _index_path(self, safe_user_id: str) -> Path:
        return self.root / "summaries" / safe_user_id / "index.json"

    @staticmethod
    def _summary_id(start_sequence: int, end_sequence: int) -> str:
        return f"sum_{start_sequence:06d}_{end_sequence:06d}"

    @staticmethod
    def _render_markdown(
        *,
        summary_id: str,
        user_id: str,
        start_sequence: int,
        end_sequence: int,
        created_at: str,
        summary_source: str,
        content: str,
    ) -> str:
        return (
            "---\n"
            f'summary_id: "{summary_id}"\n'
            f'user_id: "{user_id}"\n'
            f"start_sequence: {start_sequence}\n"
            f"end_sequence: {end_sequence}\n"
            f'created_at: "{created_at}"\n'
            f'summary_source: "{summary_source}"\n'
            "---\n\n"
            f"# 对话摘要 {start_sequence}-{end_sequence}\n\n"
            f"{content.strip()}\n"
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", value).strip("_") or "item"


def build_local_summary(turns: list[dict]) -> str:
    if not turns:
        return "- 暂无可摘要对话。"
    start = turns[0].get("sequence", 0)
    end = turns[-1].get("sequence", 0)
    lines = [f"- 对话范围：{start}-{end}。"]
    for turn in turns[:20]:
        user_text = str(turn.get("user_text", "")).strip()
        assistant_text = str(turn.get("assistant_text", "")).strip()
        if user_text:
            lines.append(f"- 用户第 {turn.get('sequence')} 轮提到：{_clip(user_text, 80)}")
        if assistant_text:
            lines.append(f"- 助手回应重点：{_clip(assistant_text, 80)}")
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."
```

Modify `voice_robot/memory/__init__.py`:

```python
from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.summary_store import SummaryStore, build_local_summary

__all__ = ["ConversationStore", "SummaryStore", "build_local_summary"]
```

- [ ] **Step 4: Run SummaryStore tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_summary_store.py -q
```

Expected: PASS.

- [ ] **Step 5: Checkpoint**

Run:

```bash
.venv/bin/python -m pytest tests/test_conversation_store.py tests/test_summary_store.py -q
```

Expected: PASS.

Commit note: 当前目录若仍不是 git 仓库，记录“commit 不适用”；如果执行环境已有 git 仓库，提交信息使用 `feat: add long term summary store`。

---

## Task 3: AuditLogStore

**Files:**

- Create: `voice_robot/memory/audit_log_store.py`
- Create: `tests/test_audit_log_store.py`
- Modify: `voice_robot/memory/__init__.py`

- [ ] **Step 1: Write failing tests**

Add `tests/test_audit_log_store.py`:

```python
from voice_robot.memory.audit_log_store import AuditLogStore


def test_append_and_list_audit_events(tmp_path) -> None:
    store = AuditLogStore(tmp_path)

    first = store.append_event(
        "xiaoming",
        event_type="asr_result",
        payload={"text": "讲一个故事"},
        turn_id="turn_1",
    )
    second = store.append_event(
        "xiaoming",
        event_type="llm_reply_delta",
        payload={"text": "小恐龙"},
        turn_id="turn_1",
    )

    events = store.list_events("xiaoming", limit=10)

    assert first["event_id"].startswith("evt_")
    assert [event["event_id"] for event in events] == [
        first["event_id"],
        second["event_id"],
    ]
    assert events[0]["payload"] == {"text": "讲一个故事"}


def test_update_and_delete_audit_event(tmp_path) -> None:
    store = AuditLogStore(tmp_path)
    event = store.append_event(
        "xiaoming",
        event_type="websocket_error",
        payload={"message": "old"},
        turn_id="",
    )

    updated = store.update_event(
        "xiaoming",
        event["event_id"],
        {"payload": {"message": "new"}, "event_type": "manual_edit"},
    )
    deleted = store.delete_event("xiaoming", event["event_id"])

    assert updated is not None
    assert updated["payload"] == {"message": "new"}
    assert updated["event_type"] == "manual_edit"
    assert deleted is True
    assert store.list_events("xiaoming") == []


def test_corrupt_audit_line_is_skipped(tmp_path) -> None:
    path = tmp_path / "audit" / "xiaoming" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"event_id":"evt_ok","event_type":"x","payload":{}}\nnot-json\n', encoding="utf-8")
    store = AuditLogStore(tmp_path)

    events = store.list_events("xiaoming")

    assert [event["event_id"] for event in events] == ["evt_ok"]
```

- [ ] **Step 2: Run tests and verify import failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_log_store.py -q
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement AuditLogStore**

Create `voice_robot/memory/audit_log_store.py`:

```python
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class AuditLogStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def append_event(
        self,
        user_id: str,
        *,
        event_type: str,
        payload: dict,
        turn_id: str = "",
    ) -> dict:
        safe_user_id = self._safe_component(user_id)
        event = {
            "event_id": f"evt_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:4]}",
            "created_at": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "turn_id": turn_id,
            "payload": payload,
        }
        path = self._path(safe_user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return event

    def list_events(self, user_id: str, *, limit: int = 100) -> list[dict]:
        safe_user_id = self._safe_component(user_id)
        events = self._read_events(safe_user_id)
        return events[-limit:]

    def update_event(self, user_id: str, event_id: str, patch: dict) -> dict | None:
        safe_user_id = self._safe_component(user_id)
        events = self._read_events(safe_user_id)
        allowed_fields = {"event_type", "turn_id", "payload"}
        updated: dict | None = None
        for event in events:
            if event.get("event_id") == event_id:
                for field in allowed_fields:
                    if field in patch:
                        event[field] = patch[field]
                updated = event
                break
        if updated is None:
            return None
        self._rewrite_events(safe_user_id, events)
        return updated

    def delete_event(self, user_id: str, event_id: str) -> bool:
        safe_user_id = self._safe_component(user_id)
        events = self._read_events(safe_user_id)
        remaining = [event for event in events if event.get("event_id") != event_id]
        if len(remaining) == len(events):
            return False
        self._rewrite_events(safe_user_id, remaining)
        return True

    def _read_events(self, safe_user_id: str) -> list[dict]:
        path = self._path(safe_user_id)
        if not path.exists():
            return []
        events: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def _rewrite_events(self, safe_user_id: str, events: list[dict]) -> None:
        path = self._path(safe_user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = path.with_name(
                f"{path.name}.{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.bak"
            )
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        tmp_path = path.with_suffix(".jsonl.tmp")
        tmp_path.write_text(
            "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in events),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _path(self, safe_user_id: str) -> Path:
        return self.root / "audit" / safe_user_id / "events.jsonl"

    @staticmethod
    def _safe_component(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", value).strip("_") or "item"
```

Modify `voice_robot/memory/__init__.py`:

```python
from voice_robot.memory.audit_log_store import AuditLogStore
from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.summary_store import SummaryStore, build_local_summary

__all__ = [
    "AuditLogStore",
    "ConversationStore",
    "SummaryStore",
    "build_local_summary",
]
```

- [ ] **Step 4: Run AuditLogStore tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_log_store.py -q
```

Expected: PASS.

- [ ] **Step 5: Checkpoint**

Run:

```bash
.venv/bin/python -m pytest tests/test_conversation_store.py tests/test_summary_store.py tests/test_audit_log_store.py -q
```

Expected: PASS.

Commit note: 当前目录若仍不是 git 仓库，记录“commit 不适用”；如果执行环境已有 git 仓库，提交信息使用 `feat: add audit memory log store`。

---

## Task 4: FileMemoryStore Management Methods

**Files:**

- Modify: `voice_robot/memory/file_store.py`
- Modify: `voice_robot/memory/file_retriever.py`
- Modify: `tests/test_memory.py`

- [ ] **Step 1: Write failing tests for profile and structured memory management**

Append to `tests/test_memory.py`:

```python
def test_file_store_loads_and_patches_profile(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    store.save_profile("xiaoming", {"user_id": "xiaoming", "interests": ["恐龙"]})

    profile = store.patch_profile(
        "xiaoming",
        {"learning_preferences": ["短句"], "interests": ["恐龙", "童话"]},
    )

    assert profile["user_id"] == "xiaoming"
    assert profile["interests"] == ["恐龙", "童话"]
    assert profile["learning_preferences"] == ["短句"]
    assert store.load_profile("xiaoming") == profile


def test_file_store_update_and_delete_memory_entry(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    memory_id = store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="喜欢恐龙故事。",
        tags=["恐龙"],
        importance=0.8,
    )

    updated = store.update_memory(
        "xiaoming",
        memory_id,
        {
            "title": "喜欢太空恐龙",
            "content": "喜欢太空和恐龙结合的故事。",
            "tags": ["太空", "恐龙"],
            "importance": 0.9,
            "status": "active",
            "confidence": 0.95,
            "source_turn_id": "turn_1",
        },
    )
    deleted = store.delete_memory("xiaoming", memory_id)

    assert updated is not None
    assert updated["title"] == "喜欢太空恐龙"
    assert updated["summary"] == "喜欢太空和恐龙结合的故事。"
    assert updated["tags"] == ["太空", "恐龙"]
    assert updated["confidence"] == 0.95
    assert deleted is True
    assert store.load_index("xiaoming") == []


def test_retriever_ignores_deleted_memory_entries(tmp_path) -> None:
    store = FileMemoryStore(tmp_path)
    memory_id = store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="喜欢恐龙故事。",
        tags=["恐龙"],
        importance=1.0,
    )
    store.update_memory("xiaoming", memory_id, {"status": "deleted"})

    retriever = FileMemoryRetriever(tmp_path)

    assert retriever.search("恐龙", "xiaoming") == []
```

- [ ] **Step 2: Run tests and verify missing methods fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory.py -q
```

Expected: FAIL with `AttributeError` for `load_profile`, `patch_profile`, `update_memory` or `delete_memory`.

- [ ] **Step 3: Implement management methods in FileMemoryStore**

Add methods to `voice_robot/memory/file_store.py`:

```python
    def load_profile(self, user_id: str) -> dict:
        safe_user_id = self._safe_component(user_id)
        path = self.root / "profiles" / f"{safe_user_id}.json"
        if not path.exists():
            return {"user_id": safe_user_id}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"user_id": safe_user_id}

    def patch_profile(self, user_id: str, patch: dict) -> dict:
        profile = self.load_profile(user_id)
        for key, value in patch.items():
            if isinstance(value, list):
                profile[key] = self._dedupe_list(value)
            else:
                profile[key] = value
        profile.setdefault("user_id", self._safe_component(user_id))
        profile["updated_at"] = datetime.now(UTC).isoformat()
        self.save_profile(user_id, profile)
        return profile

    def update_memory(self, user_id: str, memory_id: str, patch: dict) -> dict | None:
        safe_user_id = self._safe_component(user_id)
        index_path = self.root / "memories" / safe_user_id / "index.json"
        entries = self._load_json_list(index_path)
        match = next((entry for entry in entries if entry.get("id") == memory_id), None)
        if match is None:
            return None
        if "title" in patch:
            match["title"] = str(patch["title"])
        if "content" in patch:
            match["summary"] = str(patch["content"])
        if "tags" in patch and isinstance(patch["tags"], list):
            match["tags"] = self._dedupe_list(patch["tags"])
        if "importance" in patch:
            match["importance"] = float(patch["importance"])
        if "status" in patch:
            match["status"] = str(patch["status"])
        if "confidence" in patch:
            match["confidence"] = float(patch["confidence"])
        if "source_turn_id" in patch:
            match["source_turn_id"] = str(patch["source_turn_id"])
        match["updated_at"] = datetime.now(UTC).isoformat()
        path_value = match.get("path")
        if isinstance(path_value, str):
            memory_path = self.root / path_value
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            memory_path.write_text(
                (
                    "---\n"
                    f'id: "{memory_id}"\n'
                    f'category: "{match.get("category", "preference")}"\n'
                    f"importance: {match.get('importance', 0.0)}\n"
                    f"tags: {json.dumps(match.get('tags', []), ensure_ascii=False)}\n"
                    f'status: "{match.get("status", "active")}"\n'
                    f"confidence: {match.get('confidence', 0.0)}\n"
                    f'source_turn_id: "{match.get("source_turn_id", "")}"\n'
                    "---\n"
                    f"# {match.get('title', '')}\n\n"
                    f"{match.get('summary', '')}\n"
                ),
                encoding="utf-8",
            )
        self._write_json_list(index_path, entries)
        return match

    def delete_memory(self, user_id: str, memory_id: str) -> bool:
        safe_user_id = self._safe_component(user_id)
        index_path = self.root / "memories" / safe_user_id / "index.json"
        entries = self._load_json_list(index_path)
        match = next((entry for entry in entries if entry.get("id") == memory_id), None)
        if match is None:
            return False
        path_value = match.get("path")
        if isinstance(path_value, str):
            memory_path = self.root / path_value
            if memory_path.exists():
                memory_path.unlink()
        self._write_json_list(
            index_path,
            [entry for entry in entries if entry.get("id") != memory_id],
        )
        return True

    @staticmethod
    def _write_json_list(path: Path, entries: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    @staticmethod
    def _dedupe_list(values: list) -> list:
        result = []
        seen = set()
        for value in values:
            text = str(value).strip()
            if text and text not in seen:
                result.append(text)
                seen.add(text)
        return result
```

In `save_memory()`, add metadata fields to the index entry without changing existing callers:

```python
                "confidence": 1.0,
                "source_turn_id": "",
                "updated_at": now,
```

- [ ] **Step 4: Filter deleted entries in retriever**

Modify `voice_robot/memory/file_retriever.py` inside `search()` before scoring:

```python
        active_entries = [
            entry
            for entry in self.store.load_index(user_id)
            if entry.get("status", "active") != "deleted"
        ]
        scored = [
            (self._score(entry, keywords), entry)
            for entry in active_entries
        ]
```

- [ ] **Step 5: Run memory tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory.py -q
```

Expected: PASS.

Commit note: 当前目录若仍不是 git 仓库，记录“commit 不适用”；如果执行环境已有 git 仓库，提交信息使用 `feat: add memory management operations`。

---

## Task 5: ProfileMemoryExtractor

**Files:**

- Create: `voice_robot/memory/profile_extractor.py`
- Create: `tests/test_profile_extractor.py`
- Modify: `voice_robot/memory/__init__.py`

- [ ] **Step 1: Write failing tests for local extraction and Ark fallback**

Add `tests/test_profile_extractor.py`:

```python
import pytest

from voice_robot.memory.profile_extractor import ProfileMemoryExtractor
from voice_robot.services.llm import LLMServiceError


class JsonExtractorLLM:
    async def generate_reply(self, **kwargs) -> str:
        return (
            '{"profile_patch":{"interests":["恐龙"],"style_preferences":["简短"]},'
            '"structured_memories":[{"category":"preference","title":"喜欢恐龙",'
            '"content":"用户喜欢恐龙故事。","tags":["恐龙"],"importance":0.8,'
            '"confidence":0.9}]}'
        )


class FailingExtractorLLM:
    async def generate_reply(self, **kwargs) -> str:
        raise LLMServiceError("extractor unavailable")


@pytest.mark.asyncio
async def test_extractor_uses_llm_json_when_available() -> None:
    extractor = ProfileMemoryExtractor(llm_service=JsonExtractorLLM())

    result = await extractor.extract(
        user_id="xiaoming",
        turn_id="turn_1",
        user_text="我喜欢恐龙故事",
        assistant_text="好呀。",
        current_profile={},
    )

    assert result["profile_patch"]["interests"] == ["恐龙"]
    assert result["structured_memories"][0]["title"] == "喜欢恐龙"
    assert result["structured_memories"][0]["source_turn_id"] == "turn_1"


@pytest.mark.asyncio
async def test_extractor_falls_back_to_keyword_rules() -> None:
    extractor = ProfileMemoryExtractor(llm_service=FailingExtractorLLM())

    result = await extractor.extract(
        user_id="xiaoming",
        turn_id="turn_1",
        user_text="我喜欢恐龙，但是不会 7x8",
        assistant_text="我们慢慢练。",
        current_profile={},
    )

    assert "恐龙" in result["profile_patch"]["interests"]
    assert "7x8" in result["profile_patch"]["weaknesses"]
    assert result["structured_memories"][0]["category"] == "preference"
```

- [ ] **Step 2: Run tests and verify import failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_profile_extractor.py -q
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement extractor**

Create `voice_robot/memory/profile_extractor.py`:

```python
import json
import re
from datetime import UTC, datetime
from typing import Protocol

from voice_robot.services.llm import LLMServiceError


class ExtractorLLM(Protocol):
    async def generate_reply(
        self,
        *,
        user_text: str,
        user_id: str,
        memory_hints: list[dict],
        memory_block: str = "",
    ) -> str:
        ...


class ProfileMemoryExtractor:
    def __init__(self, llm_service: ExtractorLLM | None = None) -> None:
        self.llm_service = llm_service

    async def extract(
        self,
        *,
        user_id: str,
        turn_id: str,
        user_text: str,
        assistant_text: str,
        current_profile: dict,
    ) -> dict:
        del assistant_text
        if self.llm_service is not None:
            try:
                parsed = json.loads(
                    await self.llm_service.generate_reply(
                        user_text=self._extract_prompt(user_text, current_profile),
                        user_id=user_id,
                        memory_hints=[],
                        memory_block="",
                    )
                )
                return self._normalize(parsed, turn_id)
            except (json.JSONDecodeError, LLMServiceError, TypeError, ValueError):
                pass
        return self._local_extract(user_text, turn_id)

    def _normalize(self, payload: dict, turn_id: str) -> dict:
        profile_patch = payload.get("profile_patch")
        memories = payload.get("structured_memories")
        if not isinstance(profile_patch, dict):
            profile_patch = {}
        if not isinstance(memories, list):
            memories = []
        normalized = []
        now = datetime.now(UTC).isoformat()
        for memory in memories:
            if not isinstance(memory, dict):
                continue
            normalized.append(
                {
                    "category": str(memory.get("category", "preference")),
                    "title": str(memory.get("title", "用户偏好")),
                    "content": str(memory.get("content", "")),
                    "tags": self._string_list(memory.get("tags", [])),
                    "importance": float(memory.get("importance", 0.6)),
                    "confidence": float(memory.get("confidence", 0.6)),
                    "source_turn_id": turn_id,
                    "updated_at": now,
                }
            )
        return {"profile_patch": profile_patch, "structured_memories": normalized}

    def _local_extract(self, user_text: str, turn_id: str) -> dict:
        interests = []
        weaknesses = []
        style_preferences = []
        if "恐龙" in user_text:
            interests.append("恐龙")
        if "故事" in user_text:
            interests.append("故事")
        if "短" in user_text or "简短" in user_text:
            style_preferences.append("简短")
        for match in re.findall(r"不会\s*([0-9xX+\-*/= ]+)", user_text):
            text = match.strip()
            if text:
                weaknesses.append(text)
        patch: dict[str, list[str]] = {}
        if interests:
            patch["interests"] = self._dedupe(interests)
        if weaknesses:
            patch["weaknesses"] = self._dedupe(weaknesses)
        if style_preferences:
            patch["style_preferences"] = self._dedupe(style_preferences)
        memories = []
        now = datetime.now(UTC).isoformat()
        if interests:
            title = "喜欢" + "、".join(self._dedupe(interests))
            memories.append(
                {
                    "category": "preference",
                    "title": title,
                    "content": f"用户表达了对{'、'.join(self._dedupe(interests))}的兴趣。",
                    "tags": self._dedupe(interests),
                    "importance": 0.7,
                    "confidence": 0.7,
                    "source_turn_id": turn_id,
                    "updated_at": now,
                }
            )
        if weaknesses:
            memories.append(
                {
                    "category": "learning",
                    "title": "学习薄弱点",
                    "content": f"用户提到不会{'、'.join(self._dedupe(weaknesses))}。",
                    "tags": ["学习", "薄弱点"],
                    "importance": 0.8,
                    "confidence": 0.7,
                    "source_turn_id": turn_id,
                    "updated_at": now,
                }
            )
        return {"profile_patch": patch, "structured_memories": memories}

    @staticmethod
    def _extract_prompt(user_text: str, current_profile: dict) -> str:
        return (
            "请从儿童语音机器人对话中抽取用户画像和结构化记忆，只返回 JSON。"
            f"\n当前画像：{json.dumps(current_profile, ensure_ascii=False)}"
            f"\n用户输入：{user_text}"
        )

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return ProfileMemoryExtractor._dedupe(str(item).strip() for item in value)

    @staticmethod
    def _dedupe(values) -> list[str]:
        result = []
        seen = set()
        for value in values:
            text = str(value).strip()
            if text and text not in seen:
                result.append(text)
                seen.add(text)
        return result
```

Modify `voice_robot/memory/__init__.py`:

```python
from voice_robot.memory.audit_log_store import AuditLogStore
from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.profile_extractor import ProfileMemoryExtractor
from voice_robot.memory.summary_store import SummaryStore, build_local_summary

__all__ = [
    "AuditLogStore",
    "ConversationStore",
    "ProfileMemoryExtractor",
    "SummaryStore",
    "build_local_summary",
]
```

- [ ] **Step 4: Run extractor tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_profile_extractor.py -q
```

Expected: PASS.

Commit note: 当前目录若仍不是 git 仓库，记录“commit 不适用”；如果执行环境已有 git 仓库，提交信息使用 `feat: add profile memory extractor`。

---

## Task 6: MemoryContextBuilder and LLM memory_block compatibility

**Files:**

- Create: `voice_robot/memory/context_builder.py`
- Create: `tests/test_memory_context_builder.py`
- Modify: `voice_robot/services/llm.py`
- Modify: `tests/test_llm_service.py`
- Modify: `voice_robot/memory/__init__.py`

- [ ] **Step 1: Write failing context builder tests**

Add `tests/test_memory_context_builder.py`:

```python
from voice_robot.memory.context_builder import MemoryContextBuilder
from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.file_store import FileMemoryStore
from voice_robot.memory.summary_store import SummaryStore


def test_context_builder_includes_profile_summaries_structured_and_recent_turns(tmp_path) -> None:
    file_store = FileMemoryStore(tmp_path)
    file_store.save_profile(
        "xiaoming",
        {
            "user_id": "xiaoming",
            "interests": ["恐龙"],
            "learning_preferences": ["短句"],
        },
    )
    file_store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙故事",
        content="用户喜欢恐龙主题。",
        tags=["恐龙", "故事"],
        importance=0.9,
    )
    summary_store = SummaryStore(tmp_path)
    summary_store.save_summary(
        "xiaoming",
        start_sequence=1,
        end_sequence=20,
        content="- 用户偏好温和短故事。",
        summary_source="local",
    )
    conversation_store = ConversationStore(tmp_path)
    for index in range(22):
        conversation_store.append_turn(
            "xiaoming",
            user_text=f"用户第 {index + 1} 轮",
            assistant_text=f"助手第 {index + 1} 轮",
            reply_source="ark",
            fallback_reason="",
            safety_passed=True,
        )

    builder = MemoryContextBuilder(tmp_path)
    block = builder.build(user_id="xiaoming", query="恐龙故事")

    assert "【用户画像】" in block
    assert "兴趣：恐龙" in block
    assert "【长期摘要】" in block
    assert "用户偏好温和短故事" in block
    assert "【结构化记忆】" in block
    assert "喜欢恐龙故事" in block
    assert "【最近 20 轮对话】" in block
    assert "用户第 1 轮" not in block
    assert "用户第 3 轮" in block
    assert "助手第 22 轮" in block


def test_context_builder_excludes_audit_and_limits_total_length(tmp_path) -> None:
    conversation_store = ConversationStore(tmp_path)
    conversation_store.append_turn(
        "xiaoming",
        user_text="用户输入" * 300,
        assistant_text="助手回复" * 300,
        reply_source="ark",
        fallback_reason="",
        safety_passed=True,
    )

    builder = MemoryContextBuilder(tmp_path, max_chars=1000)
    block = builder.build(user_id="xiaoming", query="测试")

    assert len(block) <= 1000
    assert "审计" not in block
```

- [ ] **Step 2: Write failing LLM compatibility test**

Append to `tests/test_llm_service.py`:

```python
@pytest.mark.asyncio
async def test_ark_generate_reply_accepts_prebuilt_memory_block(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ArkChatLLMService(
        config=ArkChatConfig(base_url="https://ark.example/api/v3", model="ep-test"),
        api_key_provider=lambda: "sk-test",
    )

    def fake_post_json(payload: dict, api_key: str) -> dict:
        captured["payload"] = payload
        return {"choices": [{"message": {"content": "好的"}}]}

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    await service.generate_reply(
        user_text="继续",
        user_id="xiaoming",
        memory_hints=[{"title": "旧 hint"}],
        memory_block="【最近 20 轮对话】\n1. 用户：你好",
    )

    prompt = captured["payload"]["messages"][1]["content"]
    assert "【最近 20 轮对话】" in prompt
    assert "旧 hint" not in prompt
```

- [ ] **Step 3: Run tests and verify failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_context_builder.py tests/test_llm_service.py -q
```

Expected: FAIL because `MemoryContextBuilder` does not exist and `generate_reply()` does not accept `memory_block`.

- [ ] **Step 4: Implement MemoryContextBuilder**

Create `voice_robot/memory/context_builder.py`:

```python
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
        max_chars: int = 6000,
        recent_turn_limit: int = 20,
        summary_limit: int = 3,
        structured_limit: int = 3,
    ) -> None:
        self.root = Path(root)
        self.max_chars = max_chars
        self.recent_turn_limit = recent_turn_limit
        self.summary_limit = summary_limit
        self.structured_limit = structured_limit
        self.file_store = FileMemoryStore(self.root)
        self.retriever = FileMemoryRetriever(self.root)
        self.conversation_store = ConversationStore(self.root)
        self.summary_store = SummaryStore(self.root)

    def build(self, *, user_id: str, query: str) -> str:
        sections = [
            self._profile_section(user_id),
            self._summary_section(user_id),
            self._structured_section(user_id, query),
            self._recent_turn_section(user_id),
        ]
        block = "\n\n".join(section for section in sections if section.strip())
        return self._clip(block, self.max_chars)

    def _profile_section(self, user_id: str) -> str:
        profile = self.file_store.load_profile(user_id)
        lines = ["【用户画像】"]
        mapping = {
            "interests": "兴趣",
            "learning_preferences": "学习偏好",
            "weaknesses": "薄弱点",
            "style_preferences": "风格偏好",
        }
        has_content = False
        for key, label in mapping.items():
            values = profile.get(key, [])
            if isinstance(values, list) and values:
                lines.append(f"- {label}：{'、'.join(str(value) for value in values)}")
                has_content = True
        if not has_content:
            lines.append("- 暂无稳定画像。")
        return "\n".join(lines)

    def _summary_section(self, user_id: str) -> str:
        lines = ["【长期摘要】"]
        summaries = self.summary_store.list_recent_summaries(
            user_id, limit=self.summary_limit
        )
        if not summaries:
            lines.append("- 暂无长期摘要。")
            return "\n".join(lines)
        for summary in summaries:
            content = self.summary_store.load_summary_content(
                user_id, str(summary["summary_id"])
            )
            if not content:
                continue
            body = content.split("---", 2)[-1]
            body = body.replace(f"# 对话摘要 {summary['start_sequence']}-{summary['end_sequence']}", "")
            lines.append(
                f"- {summary['start_sequence']}-{summary['end_sequence']}：{self._clip(body.strip(), 1000)}"
            )
        return "\n".join(lines)

    def _structured_section(self, user_id: str, query: str) -> str:
        lines = ["【结构化记忆】"]
        memories = self.retriever.search(query, user_id, top_k=self.structured_limit)
        if not memories:
            lines.append("- 暂无相关结构化记忆。")
            return "\n".join(lines)
        for memory in memories:
            tags = "、".join(memory.get("tags", []))
            lines.append(
                f"- {memory.get('title', '')}；{memory.get('summary', '')}；{tags}".strip("；")
            )
        return "\n".join(lines)

    def _recent_turn_section(self, user_id: str) -> str:
        lines = ["【最近 20 轮对话】"]
        turns = self.conversation_store.list_recent_turns(
            user_id, limit=self.recent_turn_limit
        )
        if not turns:
            lines.append("- 暂无最近对话。")
            return "\n".join(lines)
        for turn in turns:
            lines.append(
                f"{turn['sequence']}. 用户：{self._clip(str(turn.get('user_text', '')), 500)}\n"
                f"   助手：{self._clip(str(turn.get('assistant_text', '')), 500)}"
            )
        return "\n".join(lines)

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."
```

- [ ] **Step 5: Extend LLMService signatures**

Modify `voice_robot/services/llm.py`:

```python
class LLMService(Protocol):
    async def generate_reply(
        self,
        *,
        user_text: str,
        user_id: str,
        memory_hints: list[dict],
        memory_block: str = "",
    ) -> str:
        """Generate a text reply."""
```

Update `LocalLLMService.generate_reply()` and `ArkChatLLMService.generate_reply()` to accept `memory_block: str = ""`. In `ArkChatLLMService.generate_reply()`, pass `memory_block=memory_block` into `_build_messages()`.

Replace `_build_messages()` memory block construction with:

```python
        if not memory_block:
            memory_lines = []
            for memory in memory_hints[:3]:
                parts = [
                    str(memory.get("title", "")).strip(),
                    str(memory.get("summary", "")).strip(),
                    " ".join(memory.get("tags", [])),
                ]
                line = "；".join(part for part in parts if part)
                if line:
                    memory_lines.append(f"- {line}")
            memory_block = "\n".join(memory_lines) if memory_lines else "- 暂无相关记忆"
```

Modify `_build_messages()` signature:

```python
    def _build_messages(
        *,
        user_text: str,
        user_id: str,
        memory_hints: list[dict],
        prompt_config: ArkPromptConfig,
        memory_block: str = "",
    ) -> list[dict[str, str]]:
```

Modify all existing direct `_build_messages()` calls in tests to pass nothing for `memory_block`, relying on the default.

- [ ] **Step 6: Export builder**

Modify `voice_robot/memory/__init__.py`:

```python
from voice_robot.memory.audit_log_store import AuditLogStore
from voice_robot.memory.context_builder import MemoryContextBuilder
from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.profile_extractor import ProfileMemoryExtractor
from voice_robot.memory.summary_store import SummaryStore, build_local_summary

__all__ = [
    "AuditLogStore",
    "ConversationStore",
    "MemoryContextBuilder",
    "ProfileMemoryExtractor",
    "SummaryStore",
    "build_local_summary",
]
```

- [ ] **Step 7: Run context and LLM tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_context_builder.py tests/test_llm_service.py -q
```

Expected: PASS.

Commit note: 当前目录若仍不是 git 仓库，记录“commit 不适用”；如果执行环境已有 git 仓库，提交信息使用 `feat: build memory context block`。

---

## Task 7: VoiceAgent Integration and Summary Trigger

**Files:**

- Modify: `voice_robot/agent/graph.py`
- Modify: `tests/test_agent.py`

- [ ] **Step 1: Write failing tests for persistence and memory_block**

Append to `tests/test_agent.py`:

```python
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
    assert "【最近 20 轮对话】" in kwargs["memory_block"]


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
```

- [ ] **Step 2: Write failing tests for summary trigger and profile update**

Append to `tests/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_agent_summarizes_turns_1_to_20_after_turn_40(tmp_path) -> None:
    agent = VoiceAgent(memory_root=tmp_path)

    for index in range(40):
        await agent.ainvoke(f"第 {index + 1} 轮普通聊天", user_id="xiaoming")

    summary_path = tmp_path / "summaries" / "xiaoming" / "summary_000001_000020.md"
    meta_path = tmp_path / "conversations" / "xiaoming" / "meta.json"

    assert summary_path.exists()
    assert "对话摘要 1-20" in summary_path.read_text(encoding="utf-8")
    assert '"last_summarized_sequence": 20' in meta_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_agent_updates_profile_and_structured_memory_from_chat(tmp_path) -> None:
    agent = VoiceAgent(memory_root=tmp_path)

    result = await agent.ainvoke("我喜欢恐龙故事", user_id="xiaoming")

    profile = (tmp_path / "profiles" / "xiaoming.json").read_text(encoding="utf-8")
    index = (tmp_path / "memories" / "xiaoming" / "index.json").read_text(encoding="utf-8")
    assert result.new_memories
    assert "恐龙" in profile
    assert "喜欢恐龙" in index
```

- [ ] **Step 3: Run tests and verify failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -q
```

Expected: FAIL because `VoiceAgent` does not persist turns, does not pass `memory_block`, and does not summarize or update profile.

- [ ] **Step 4: Modify VoiceAgent constructor**

In `voice_robot/agent/graph.py`, add imports:

```python
import asyncio
from collections import defaultdict
```

Add memory imports:

```python
from voice_robot.memory.audit_log_store import AuditLogStore
from voice_robot.memory.context_builder import MemoryContextBuilder
from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.file_store import FileMemoryStore
from voice_robot.memory.profile_extractor import ProfileMemoryExtractor
from voice_robot.memory.summary_store import SummaryStore, build_local_summary
```

Extend `__init__()`:

```python
        self.file_store = FileMemoryStore(self.memory_root)
        self.conversation_store = ConversationStore(self.memory_root)
        self.summary_store = SummaryStore(self.memory_root)
        self.audit_log_store = AuditLogStore(self.memory_root)
        self.context_builder = MemoryContextBuilder(self.memory_root)
        self.profile_extractor = ProfileMemoryExtractor()
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
```

- [ ] **Step 5: Make ainvoke persist every result under per-user lock**

Replace `ainvoke()` with this shape:

```python
    async def ainvoke(self, text: str, *, user_id: str) -> AgentResult:
        async with self._locks[user_id]:
            state = AgentState(user_text=text, user_id=user_id)
            memory_block = self.context_builder.build(
                user_id=user_id,
                query=self._memory_query(text),
            )
            state.retrieved_memories = self.retriever.search(
                self._memory_query(text), user_id, top_k=3
            )

            if self._is_unsafe(text):
                state.safety_passed = False
                state.reply_source = "local-rule"
                state.response_text = "这个话题不适合小朋友，我们聊点学习、故事或有趣的问题吧。"
            else:
                state.response_text = await self._reply(state, memory_block=memory_block)

            turn = self.conversation_store.append_turn(
                user_id,
                user_text=state.user_text,
                assistant_text=state.response_text,
                reply_source=state.reply_source,
                fallback_reason=state.fallback_reason or "",
                safety_passed=state.safety_passed,
            )
            state.new_memories = await self._update_profile_and_memories(state, turn)
            await self._maybe_summarize(user_id)
            return self._to_result(state)
```

Modify `_reply()` signature and LLM calls:

```python
    async def _reply(self, state: AgentState, *, memory_block: str) -> str:
```

Pass `memory_block=memory_block` into both primary and fallback `generate_reply()` calls.

- [ ] **Step 6: Add profile update and summary helper methods**

Add to `VoiceAgent`:

```python
    async def _update_profile_and_memories(
        self,
        state: AgentState,
        turn: dict,
    ) -> list[dict]:
        try:
            extraction = await self.profile_extractor.extract(
                user_id=state.user_id,
                turn_id=str(turn["turn_id"]),
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
        profile_patch = extraction.get("profile_patch", {})
        if isinstance(profile_patch, dict) and profile_patch:
            self.file_store.patch_profile(state.user_id, profile_patch)
        new_memories = []
        for memory in extraction.get("structured_memories", []):
            memory_id = self.file_store.save_memory(
                state.user_id,
                category=str(memory.get("category", "preference")),
                title=str(memory.get("title", "用户记忆")),
                content=str(memory.get("content", "")),
                tags=list(memory.get("tags", [])),
                importance=float(memory.get("importance", 0.6)),
            )
            self.file_store.update_memory(
                state.user_id,
                memory_id,
                {
                    "confidence": float(memory.get("confidence", 0.6)),
                    "source_turn_id": str(memory.get("source_turn_id", turn["turn_id"])),
                },
            )
            new_memories.append({"id": memory_id, **memory})
        return new_memories

    async def _maybe_summarize(self, user_id: str) -> None:
        meta = self.conversation_store.load_meta(user_id)
        latest_sequence = int(meta["next_sequence"]) - 1
        last_summarized = int(meta.get("last_summarized_sequence", 0))
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
            content = build_local_summary(turns)
            self.summary_store.save_summary(
                user_id,
                start_sequence=start,
                end_sequence=end,
                content=content,
                summary_source="local",
            )
            meta["last_summarized_sequence"] = end
            meta["updated_at"] = self.conversation_store._now()
            self.conversation_store.save_meta(user_id, meta)
            start = end + 1
```

- [ ] **Step 7: Run agent tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -q
```

Expected: PASS.

- [ ] **Step 8: Checkpoint**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py tests/test_memory_context_builder.py tests/test_memory.py -q
```

Expected: PASS.

Commit note: 当前目录若仍不是 git 仓库，记录“commit 不适用”；如果执行环境已有 git 仓库，提交信息使用 `feat: integrate persistent memory into agent`。

---

## Task 8: Admin API Memory Management

**Files:**

- Modify: `voice_robot/admin_api.py`
- Modify: `tests/test_admin_api.py`

- [ ] **Step 1: Write failing API tests**

Append to `tests/test_admin_api.py`:

```python
def test_memory_turns_api_supports_list_patch_delete(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)
    client.post("/api/chat", json={"user_id": "xiaoming", "text": "讲一个恐龙故事"})

    turns_response = client.get("/api/memory/users/xiaoming/turns?limit=20")
    assert turns_response.status_code == 200
    turn = turns_response.json()["turns"][0]

    patch_response = client.patch(
        f"/api/memory/users/xiaoming/turns/{turn['turn_id']}",
        json={"assistant_text": "编辑后的回复"},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["turn"]["assistant_text"] == "编辑后的回复"

    delete_response = client.delete(
        f"/api/memory/users/xiaoming/turns/{turn['turn_id']}"
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True


def test_memory_summaries_api_supports_list_patch_delete(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    from voice_robot.memory.summary_store import SummaryStore

    store = SummaryStore(tmp_path)
    summary = store.save_summary(
        "xiaoming",
        start_sequence=1,
        end_sequence=20,
        content="- 旧摘要",
        summary_source="local",
    )
    client = TestClient(app)

    list_response = client.get("/api/memory/users/xiaoming/summaries")
    assert list_response.status_code == 200
    assert list_response.json()["summaries"][0]["summary_id"] == summary["summary_id"]

    patch_response = client.patch(
        f"/api/memory/users/xiaoming/summaries/{summary['summary_id']}",
        json={"content": "- 新摘要", "summary_source": "manual"},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["summary"]["summary_source"] == "manual"

    delete_response = client.delete(
        f"/api/memory/users/xiaoming/summaries/{summary['summary_id']}"
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True


def test_profile_entries_and_audit_management_api(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    store = FileMemoryStore(tmp_path)
    store.save_profile("xiaoming", {"user_id": "xiaoming", "interests": ["恐龙"]})
    memory_id = store.save_memory(
        "xiaoming",
        category="preference",
        title="喜欢恐龙",
        content="喜欢恐龙故事。",
        tags=["恐龙"],
        importance=0.8,
    )
    from voice_robot.memory.audit_log_store import AuditLogStore

    event = AuditLogStore(tmp_path).append_event(
        "xiaoming",
        event_type="asr_result",
        payload={"text": "hello"},
    )
    client = TestClient(app)

    profile_response = client.patch(
        "/api/memory/users/xiaoming/profile",
        json={"style_preferences": ["简短"]},
    )
    assert profile_response.status_code == 200
    assert profile_response.json()["profile"]["style_preferences"] == ["简短"]

    entry_response = client.patch(
        f"/api/memory/users/xiaoming/entries/{memory_id}",
        json={"title": "喜欢太空恐龙", "content": "喜欢太空恐龙故事。"},
    )
    assert entry_response.status_code == 200
    assert entry_response.json()["entry"]["title"] == "喜欢太空恐龙"

    audit_response = client.get("/api/memory/users/xiaoming/audit?limit=10")
    assert audit_response.status_code == 200
    assert audit_response.json()["events"][0]["event_id"] == event["event_id"]

    delete_event = client.delete(
        f"/api/memory/users/xiaoming/audit/{event['event_id']}"
    )
    assert delete_event.status_code == 200
    assert delete_event.json()["deleted"] is True
```

- [ ] **Step 2: Run tests and verify 404/405 failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_admin_api.py -q
```

Expected: FAIL with missing route status codes for new endpoints.

- [ ] **Step 3: Add request models and store imports**

Modify `voice_robot/admin_api.py` imports:

```python
from voice_robot.memory.audit_log_store import AuditLogStore
from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.summary_store import SummaryStore
```

Add models:

```python
class TurnPatch(BaseModel):
    user_text: str | None = Field(default=None, max_length=4000)
    assistant_text: str | None = Field(default=None, max_length=4000)
    reply_source: str | None = Field(default=None, max_length=128)
    fallback_reason: str | None = Field(default=None, max_length=1000)
    safety_passed: bool | None = None


class SummaryPatch(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    summary_source: str = Field(default="manual", max_length=128)


class MemoryEntryPatch(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    content: str | None = Field(default=None, max_length=8000)
    tags: list[str] | None = None
    importance: float | None = None
    status: str | None = Field(default=None, max_length=64)
    confidence: float | None = None
    source_turn_id: str | None = Field(default=None, max_length=128)


class AuditEventPatch(BaseModel):
    event_type: str | None = Field(default=None, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    payload: dict | None = None
```

- [ ] **Step 4: Add helper constructors**

Add to `voice_robot/admin_api.py`:

```python
def _conversation_store() -> ConversationStore:
    return ConversationStore(_memory_root())


def _summary_store() -> SummaryStore:
    return SummaryStore(_memory_root())


def _audit_store() -> AuditLogStore:
    return AuditLogStore(_memory_root())


def _file_memory_store() -> FileMemoryStore:
    return FileMemoryStore(_memory_root())
```

- [ ] **Step 5: Add turns endpoints**

Add routes:

```python
@router.get("/memory/users/{user_id}/turns")
async def list_turns(user_id: str, limit: int = Query(default=20, ge=1, le=200)) -> dict:
    turns = _conversation_store().list_recent_turns(user_id, limit=limit)
    return {"turns": turns}


@router.patch("/memory/users/{user_id}/turns/{turn_id}")
async def patch_turn(user_id: str, turn_id: str, payload: TurnPatch) -> dict:
    patch = payload.model_dump(exclude_none=True)
    updated = _conversation_store().update_turn(user_id, turn_id, patch)
    if updated is None:
        raise HTTPException(status_code=404, detail="turn not found")
    return {"turn": updated}


@router.delete("/memory/users/{user_id}/turns/{turn_id}")
async def delete_turn(user_id: str, turn_id: str) -> dict[str, bool]:
    deleted = _conversation_store().delete_turn(user_id, turn_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="turn not found")
    return {"deleted": True}
```

- [ ] **Step 6: Add summaries endpoints**

Add routes:

```python
@router.get("/memory/users/{user_id}/summaries")
async def list_summaries(user_id: str) -> dict:
    store = _summary_store()
    summaries = []
    for summary in store.list_summaries(user_id):
        summaries.append(
            {
                **summary,
                "content": store.load_summary_content(user_id, str(summary["summary_id"])) or "",
            }
        )
    return {"summaries": summaries}


@router.patch("/memory/users/{user_id}/summaries/{summary_id}")
async def patch_summary(user_id: str, summary_id: str, payload: SummaryPatch) -> dict:
    updated = _summary_store().update_summary(
        user_id,
        summary_id,
        content=payload.content,
        summary_source=payload.summary_source,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="summary not found")
    return {"summary": updated}


@router.delete("/memory/users/{user_id}/summaries/{summary_id}")
async def delete_summary(user_id: str, summary_id: str) -> dict[str, bool]:
    deleted = _summary_store().delete_summary(user_id, summary_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="summary not found")
    return {"deleted": True}
```

- [ ] **Step 7: Add profile, entries and audit endpoints**

Add routes:

```python
@router.patch("/memory/users/{user_id}/profile")
async def patch_profile(user_id: str, payload: dict) -> dict:
    return {"profile": _file_memory_store().patch_profile(user_id, payload)}


@router.patch("/memory/users/{user_id}/entries/{memory_id}")
async def patch_memory_entry(
    user_id: str,
    memory_id: str,
    payload: MemoryEntryPatch,
) -> dict:
    updated = _file_memory_store().update_memory(
        user_id,
        memory_id,
        payload.model_dump(exclude_none=True),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="memory entry not found")
    return {"entry": updated}


@router.delete("/memory/users/{user_id}/entries/{memory_id}")
async def delete_memory_entry(user_id: str, memory_id: str) -> dict[str, bool]:
    deleted = _file_memory_store().delete_memory(user_id, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory entry not found")
    return {"deleted": True}


@router.get("/memory/users/{user_id}/audit")
async def list_audit_events(
    user_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    return {"events": _audit_store().list_events(user_id, limit=limit)}


@router.patch("/memory/users/{user_id}/audit/{event_id}")
async def patch_audit_event(user_id: str, event_id: str, payload: AuditEventPatch) -> dict:
    updated = _audit_store().update_event(
        user_id,
        event_id,
        payload.model_dump(exclude_none=True),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="audit event not found")
    return {"event": updated}


@router.delete("/memory/users/{user_id}/audit/{event_id}")
async def delete_audit_event(user_id: str, event_id: str) -> dict[str, bool]:
    deleted = _audit_store().delete_event(user_id, event_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="audit event not found")
    return {"deleted": True}
```

- [ ] **Step 8: Include conversation, summary and audit users in list endpoint**

Modify `list_memory_users()` to scan:

```python
    conversations_dir = root / "conversations"
    summaries_dir = root / "summaries"
    audit_dir = root / "audit"
```

Then include directories:

```python
    for directory in [memories_dir, conversations_dir, summaries_dir, audit_dir]:
        if directory.exists():
            users.update(path.name for path in directory.iterdir() if path.is_dir())
```

- [ ] **Step 9: Run admin API tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_admin_api.py -q
```

Expected: PASS.

Commit note: 当前目录若仍不是 git 仓库，记录“commit 不适用”；如果执行环境已有 git 仓库，提交信息使用 `feat: expose memory management api`。

---

## Task 9: Web Console Memory Management

**Files:**

- Modify: `web_console/index.html`
- Modify: `web_console/app.js`
- Modify: `web_console/styles.css`
- Modify: `tests/test_web_console_assets.py`

- [ ] **Step 1: Add failing static asset tests**

Append to `tests/test_web_console_assets.py`:

```python
def test_console_memory_management_assets_are_present() -> None:
    client = TestClient(app)

    page = client.get("/console")
    script = client.get("/console/assets/app.js")

    assert page.status_code == 200
    assert 'data-memory-section="profile"' in page.text
    assert 'data-memory-section="turns"' in page.text
    assert 'data-memory-section="summaries"' in page.text
    assert 'data-memory-section="entries"' in page.text
    assert 'data-memory-section="audit"' in page.text
    assert "saveMemoryProfile" in script.text
    assert "deleteSelectedMemoryItem" in script.text
    assert "/api/memory/users/" in script.text
```

- [ ] **Step 2: Run asset tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_web_console_assets.py -q
```

Expected: FAIL because new memory section markers and JS functions do not exist.

- [ ] **Step 3: Replace memory panel HTML**

In `web_console/index.html`, replace the existing `#tab-memory` inner layout with:

```html
          <div class="panel">
            <div class="panel-header">
              <div>
                <h2>记忆管理</h2>
                <p>查看、编辑和删除用户画像、对话轮次、长期摘要、结构化记忆与审计日志。</p>
              </div>
              <div class="button-row compact">
                <button id="refresh-memory" class="button secondary">刷新</button>
                <button id="save-memory-item" class="button primary">保存</button>
                <button id="delete-memory-item" class="button secondary">删除</button>
              </div>
            </div>
            <div id="memory-banner" class="banner" hidden></div>
            <div class="memory-toolbar">
              <label>
                <span class="label">用户</span>
                <select id="memory-user-select"></select>
              </label>
              <div class="memory-section-tabs" aria-label="Memory sections">
                <button class="memory-section-button active" data-memory-section="profile">用户画像</button>
                <button class="memory-section-button" data-memory-section="turns">对话轮次</button>
                <button class="memory-section-button" data-memory-section="summaries">长期摘要</button>
                <button class="memory-section-button" data-memory-section="entries">结构化记忆</button>
                <button class="memory-section-button" data-memory-section="audit">审计日志</button>
              </div>
            </div>
            <div class="memory-manager">
              <aside class="memory-list-pane">
                <ul id="memory-object-list" class="list"></ul>
              </aside>
              <section class="memory-editor-pane">
                <div class="editor-meta" id="memory-editor-meta">请选择记忆对象</div>
                <textarea id="memory-json-editor" class="json-editor" spellcheck="false"></textarea>
              </section>
            </div>
          </div>
```

- [ ] **Step 4: Extend JS state and render helpers**

In `web_console/app.js`, add state fields:

```javascript
  memorySection: "profile",
  selectedMemoryItemId: null,
  memoryPayloads: {
    profile: null,
    turns: [],
    summaries: [],
    entries: [],
    audit: [],
  },
```

Add helpers:

```javascript
function activeMemorySection() {
  return state.memorySection || "profile";
}

function setMemoryEditorMeta(value) {
  setText("#memory-editor-meta", value || "请选择记忆对象");
}

function setMemoryEditorValue(value) {
  qs("#memory-json-editor").value =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function getMemoryEditorJson() {
  const raw = qs("#memory-json-editor").value.trim();
  if (!raw) {
    return {};
  }
  return JSON.parse(raw);
}
```

- [ ] **Step 5: Replace old memory loading logic with section loader**

Replace `loadMemoryForUser()` and `selectMemoryEntry()` with:

```javascript
async function loadMemoryForUser(userId) {
  if (!userId) {
    state.selectedUser = null;
    qs("#memory-object-list").innerHTML = "";
    setMemoryEditorMeta("请选择一位用户");
    setMemoryEditorValue("");
    return;
  }
  state.selectedUser = userId;
  state.selectedMemoryItemId = null;
  setBanner("#memory-banner", "");
  await loadMemorySection();
}

async function loadMemorySection() {
  const userId = state.selectedUser;
  const section = activeMemorySection();
  if (!userId) {
    return;
  }
  try {
    if (section === "profile") {
      const profile = await fetchJson(`/api/memory/users/${encodeURIComponent(userId)}/profile`);
      state.memoryPayloads.profile = profile;
      renderMemoryList([{ id: "profile", label: "用户画像", payload: profile }]);
      selectMemoryObject("profile", profile);
      return;
    }
    if (section === "turns") {
      const data = await fetchJson(`/api/memory/users/${encodeURIComponent(userId)}/turns?limit=100`);
      state.memoryPayloads.turns = Array.isArray(data.turns) ? data.turns : [];
      renderMemoryList(state.memoryPayloads.turns.map((turn) => ({
        id: turn.turn_id,
        label: `${turn.sequence}. ${turn.user_text || turn.turn_id}`,
        payload: turn,
      })));
      return;
    }
    if (section === "summaries") {
      const data = await fetchJson(`/api/memory/users/${encodeURIComponent(userId)}/summaries`);
      state.memoryPayloads.summaries = Array.isArray(data.summaries) ? data.summaries : [];
      renderMemoryList(state.memoryPayloads.summaries.map((summary) => ({
        id: summary.summary_id,
        label: `${summary.start_sequence}-${summary.end_sequence}`,
        payload: summary,
      })));
      return;
    }
    if (section === "entries") {
      const data = await fetchJson(`/api/memory/users/${encodeURIComponent(userId)}/index`);
      state.memoryPayloads.entries = Array.isArray(data.entries) ? data.entries : [];
      renderMemoryList(state.memoryPayloads.entries.map((entry) => ({
        id: entry.id,
        label: entry.title || entry.id,
        payload: entry,
      })));
      return;
    }
    if (section === "audit") {
      const data = await fetchJson(`/api/memory/users/${encodeURIComponent(userId)}/audit?limit=100`);
      state.memoryPayloads.audit = Array.isArray(data.events) ? data.events : [];
      renderMemoryList(state.memoryPayloads.audit.map((event) => ({
        id: event.event_id,
        label: `${event.event_type || "event"} ${event.created_at || ""}`,
        payload: event,
      })));
    }
  } catch (error) {
    qs("#memory-object-list").innerHTML = "";
    setMemoryEditorMeta("记忆数据加载失败");
    setMemoryEditorValue("");
    setBanner("#memory-banner", `记忆数据加载失败：${error.message}`);
    appendLog("error", `memory section load failed: ${error.message}`);
  }
}

function renderMemoryList(items) {
  const list = qs("#memory-object-list");
  list.innerHTML = "";
  if (!items.length) {
    setMemoryEditorMeta("当前分类暂无数据");
    setMemoryEditorValue("");
    return;
  }
  items.forEach((item, index) => {
    const node = document.createElement("li");
    node.className = "list-item";
    node.textContent = item.label;
    node.addEventListener("click", () => selectMemoryObject(item.id, item.payload, node));
    list.append(node);
    if (index === 0) {
      selectMemoryObject(item.id, item.payload, node);
    }
  });
}

function selectMemoryObject(id, payload, itemNode = null) {
  state.selectedMemoryItemId = id;
  qs("#memory-object-list").querySelectorAll(".list-item").forEach((node) => {
    node.classList.toggle("active", node === itemNode);
  });
  setMemoryEditorMeta(`${activeMemorySection()} / ${id}`);
  setMemoryEditorValue(payload);
}
```

- [ ] **Step 6: Add save and delete functions**

Add:

```javascript
async function saveMemoryProfile() {
  const userId = state.selectedUser;
  if (!userId) {
    return;
  }
  const section = activeMemorySection();
  const id = state.selectedMemoryItemId;
  const payload = getMemoryEditorJson();
  if (section === "profile") {
    await sendJson(`/api/memory/users/${encodeURIComponent(userId)}/profile`, "PATCH", payload);
  } else if (section === "turns") {
    await sendJson(`/api/memory/users/${encodeURIComponent(userId)}/turns/${encodeURIComponent(id)}`, "PATCH", payload);
  } else if (section === "summaries") {
    await sendJson(`/api/memory/users/${encodeURIComponent(userId)}/summaries/${encodeURIComponent(id)}`, "PATCH", {
      content: payload.content || "",
      summary_source: payload.summary_source || "manual",
    });
  } else if (section === "entries") {
    await sendJson(`/api/memory/users/${encodeURIComponent(userId)}/entries/${encodeURIComponent(id)}`, "PATCH", payload);
  } else if (section === "audit") {
    await sendJson(`/api/memory/users/${encodeURIComponent(userId)}/audit/${encodeURIComponent(id)}`, "PATCH", payload);
  }
  setBanner("#memory-banner", "记忆对象已保存。");
  await loadMemorySection();
}

async function deleteSelectedMemoryItem() {
  const userId = state.selectedUser;
  const section = activeMemorySection();
  const id = state.selectedMemoryItemId;
  if (!userId || !id || section === "profile") {
    setBanner("#memory-banner", "用户画像不支持整体删除，请编辑字段后保存。");
    return;
  }
  const base = `/api/memory/users/${encodeURIComponent(userId)}`;
  const pathBySection = {
    turns: `${base}/turns/${encodeURIComponent(id)}`,
    summaries: `${base}/summaries/${encodeURIComponent(id)}`,
    entries: `${base}/entries/${encodeURIComponent(id)}`,
    audit: `${base}/audit/${encodeURIComponent(id)}`,
  };
  const response = await fetch(pathBySection[section], { method: "DELETE" });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${pathBySection[section]} -> ${response.status}${detail ? ` ${detail}` : ""}`);
  }
  setBanner("#memory-banner", "记忆对象已删除。");
  await loadMemorySection();
}
```

- [ ] **Step 7: Update memory event bindings**

Modify `bindEvents()`:

```javascript
  document.querySelectorAll("[data-memory-section]").forEach((button) => {
    button.addEventListener("click", () => {
      state.memorySection = button.dataset.memorySection;
      state.selectedMemoryItemId = null;
      document.querySelectorAll("[data-memory-section]").forEach((node) => {
        node.classList.toggle("active", node === button);
      });
      loadMemorySection();
    });
  });

  qs("#save-memory-item").addEventListener("click", () => {
    saveMemoryProfile().catch((error) => {
      setBanner("#memory-banner", `保存失败：${error.message}`);
      appendLog("error", `memory save failed: ${error.message}`);
    });
  });

  qs("#delete-memory-item").addEventListener("click", () => {
    deleteSelectedMemoryItem().catch((error) => {
      setBanner("#memory-banner", `删除失败：${error.message}`);
      appendLog("error", `memory delete failed: ${error.message}`);
    });
  });
```

Remove stale references to `#memory-profile`, `#memory-index`, and `#memory-entry`.

- [ ] **Step 8: Add CSS**

Append to `web_console/styles.css`:

```css
.memory-toolbar {
  display: grid;
  grid-template-columns: minmax(160px, 220px) 1fr;
  gap: 16px;
  align-items: end;
}

.memory-section-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.memory-section-button {
  border: 1px solid #d0d7de;
  background: #ffffff;
  color: #24292f;
  border-radius: 6px;
  padding: 8px 10px;
  cursor: pointer;
}

.memory-section-button.active {
  border-color: #0969da;
  color: #0969da;
  background: #ddf4ff;
}

.memory-manager {
  display: grid;
  grid-template-columns: minmax(220px, 320px) 1fr;
  gap: 16px;
  margin-top: 16px;
}

.memory-list-pane,
.memory-editor-pane {
  min-width: 0;
}

.editor-meta {
  color: #57606a;
  font-size: 12px;
  margin-bottom: 8px;
}

.json-editor {
  width: 100%;
  min-height: 460px;
  resize: vertical;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

@media (max-width: 860px) {
  .memory-toolbar,
  .memory-manager {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 9: Run Web asset tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_web_console_assets.py -q
```

Expected: PASS.

Commit note: 当前目录若仍不是 git 仓库，记录“commit 不适用”；如果执行环境已有 git 仓库，提交信息使用 `feat: add web memory management workspace`。

---

## Task 10: WebSocket Audit Events

**Files:**

- Modify: `voice_robot/server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write failing WebSocket audit test**

Append to `tests/test_server.py`:

```python
import json


def test_websocket_records_audit_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client = TestClient(app)

    with client.websocket_connect("/ws") as websocket:
        websocket.send_json(
            {"type": "auth", "device_id": "web-console", "token": "<your-token>"}
        )
        assert websocket.receive_json()["type"] == "auth_ok"
        websocket.send_json({"type": "audio_start", "user_id": "xiaoming"})
        websocket.send_bytes("讲一个恐龙故事".encode("utf-8"))
        websocket.send_json({"type": "audio_end"})
        seen_end = False
        for _ in range(20):
            message = websocket.receive()
            if message.get("text"):
                payload = json.loads(message["text"])
                if payload.get("type") == "llm_reply_end":
                    seen_end = True
                if payload.get("type") == "tts_end":
                    break
        assert seen_end is True

    events_path = tmp_path / "audit" / "xiaoming" / "events.jsonl"
    assert events_path.exists()
    events_text = events_path.read_text(encoding="utf-8")
    assert "asr_result" in events_text
    assert "llm_reply_delta" in events_text
    assert "tts_end" in events_text
```

Ensure `tests/test_server.py` imports:

```python
from fastapi.testclient import TestClient

from voice_robot.config import get_settings
from voice_robot.server import app
```

- [ ] **Step 2: Run server tests and verify audit assertion failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_server.py -q
```

Expected: FAIL because audit events are not written by WebSocket path.

- [ ] **Step 3: Add audit store in WebSocket endpoint**

Modify `voice_robot/server.py` imports:

```python
from voice_robot.memory.audit_log_store import AuditLogStore
```

Inside `websocket_endpoint()` after settings:

```python
    audit_log = AuditLogStore(settings.memory_root)
```

After ASR result:

```python
                audit_log.append_event(
                    user_id,
                    event_type="asr_result",
                    payload={"text": user_text},
                )
```

Inside `llm_reply_start`, `llm_reply_delta`, `llm_reply_end`, `tts_start`, `tts_end` send points, append matching events:

```python
                audit_log.append_event(
                    user_id,
                    event_type="llm_reply_start",
                    payload={
                        "source": agent_result.reply_source,
                        "detail": agent_result.fallback_reason,
                    },
                )
                for chunk in stream_text_chunks(agent_result.response_text):
                    await _send_control(
                        websocket,
                        ServerControlMessage(type="llm_reply_delta", text=chunk),
                    )
                    audit_log.append_event(
                        user_id,
                        event_type="llm_reply_delta",
                        payload={"text": chunk},
                    )
```

After `llm_reply_end`:

```python
                audit_log.append_event(
                    user_id,
                    event_type="llm_reply_end",
                    payload={
                        "text": agent_result.response_text,
                        "source": agent_result.reply_source,
                        "detail": agent_result.fallback_reason,
                    },
                )
```

Around TTS:

```python
                audit_log.append_event(user_id, event_type="tts_start", payload={})
                ...
                audit_log.append_event(user_id, event_type="tts_end", payload={})
```

- [ ] **Step 4: Run server tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_server.py -q
```

Expected: PASS.

Commit note: 当前目录若仍不是 git 仓库，记录“commit 不适用”；如果执行环境已有 git 仓库，提交信息使用 `feat: record websocket audit events`。

---

## Task 11: Regression, Docs, and Acceptance

**Files:**

- Modify: `docs/prd/esp32-s3-technical-solution.md`
- Modify: `docs/progress/WORK-IN-PROGRESS.md`

- [ ] **Step 1: Run focused memory suite**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_conversation_store.py \
  tests/test_summary_store.py \
  tests/test_audit_log_store.py \
  tests/test_profile_extractor.py \
  tests/test_memory_context_builder.py \
  tests/test_memory.py \
  tests/test_agent.py \
  tests/test_admin_api.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run full backend and asset regression**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Verify no stale front-end selectors**

Run:

```bash
rg -n "memory-profile|memory-index|memory-entry" web_console voice_robot tests docs/design/plans/2026-06-03-memory-system-implementation.md
```

Expected: no matches for stale memory selectors.

- [ ] **Step 4: Update PRD implementation status**

In `docs/prd/esp32-s3-technical-solution.md`, append to the memory-system section:

```markdown
#### 开发落地状态（2026-06-03）

- 已实现按用户维度的主聊天 JSONL 持久化。
- 已实现最近 20 轮短期记忆窗口，并拼接到 LLM `memory_block`。
- 已实现第 40 轮后压缩 1-20 轮、之后每 20 轮滚动压缩的长期摘要机制。
- 已实现基于聊天内容的画像抽取和结构化记忆写入，Ark/Doubao 失败时使用本地规则兜底。
- 已实现 Web 端用户画像、对话轮次、长期摘要、结构化记忆、审计日志的查看、编辑和删除。
- 已确认审计日志不默认进入 Agent 上下文。
```

- [ ] **Step 5: Update work progress**

In `docs/progress/WORK-IN-PROGRESS.md`, update “剩余工作” and “质量验证” with:

```markdown
## 最新进展（2026-06-03）

- 记忆系统开发已完成：聊天落盘、短期窗口、长期摘要、画像抽取、结构化记忆和 Web 管理工具均已接入。
- 验证结果：`.venv/bin/python -m pytest -q` 通过。

## 剩余工作

1. 端到端人工联调
- 启动服务并打开 `/console`
- 使用文本交流连续发送 40 轮，确认 `summary_000001_000020.md` 生成
- 在记忆管理 Tab 编辑一条 turn、summary、entry 和 audit event，确认刷新后仍然生效

2. 真实 Ark/Doubao 摘要器增强
- 当前摘要触发链路已可用，本阶段默认本地确定性摘要
- 下一阶段可把摘要生成替换为专用 Ark/Doubao 摘要 prompt
```

- [ ] **Step 6: Optional local server smoke test**

If sandbox permits binding a local port, run:

```bash
.venv/bin/uvicorn voice_robot.server:app --host 127.0.0.1 --port 8091
```

Expected: server starts and `/console` can be opened. If binding fails with `operation not permitted`, record the limitation in `docs/progress/WORK-IN-PROGRESS.md` and rely on TestClient verification.

- [ ] **Step 7: Final acceptance checklist**

Confirm each acceptance item from `docs/design/specs/memory-system-design.md`:

```text
1. 任意用户完成一轮对话后，文件系统中出现对应 turns.jsonl 记录。
2. 第 21 轮之后，短期上下文只包含最近 20 轮。
3. 第 40 轮之后，summary_000001_000020.md 被生成，并进入当前上下文。
4. 第 60 轮之后，summary_000021_000040.md 被生成，并进入当前上下文。
5. 用户画像能根据聊天内容自动更新。
6. Web 端可查看、编辑、删除用户画像、结构化记忆、聊天轮次、长期摘要和审计日志。
7. 审计日志不默认进入 Agent 上下文。
8. 全量测试通过。
```

Commit note: 当前目录若仍不是 git 仓库，记录“commit 不适用”；如果执行环境已有 git 仓库，提交信息使用 `docs: update memory system status`。

---

## Self-Review

### Spec Coverage

- 用户维度聊天持久化：Task 1、Task 7、Task 8 覆盖。
- 最近 20 轮短期记忆窗口：Task 1、Task 6、Task 7 覆盖。
- 窗口外每 20 轮长期摘要：Task 2、Task 7 覆盖。
- 长期摘要进入上下文：Task 6 覆盖。
- 用户画像和结构化记忆抽取：Task 4、Task 5、Task 7 覆盖。
- Web 端查看、编辑、删除：Task 8、Task 9 覆盖。
- 审计日志与主聊天分离：Task 3、Task 6、Task 10 覆盖。
- 不再读取飞书文档、文档沉淀到 `docs/prd/`：Task 11 覆盖。

### Placeholder Scan

本计划没有保留占位式实现步骤，所有代码任务都给出了目标文件、测试代码、实现代码、执行命令和预期结果。

### Type Consistency

- 主聊天统一使用 `turn_id`、`sequence`、`user_text`、`assistant_text`、`reply_source`、`fallback_reason`、`safety_passed`。
- 长期摘要统一使用 `summary_id`、`start_sequence`、`end_sequence`、`summary_source`、`path`。
- 审计日志统一使用 `event_id`、`event_type`、`turn_id`、`payload`。
- Web 端 API 路径与 PRD 保持一致：`turns`、`summaries`、`profile`、`entries`、`audit`。
