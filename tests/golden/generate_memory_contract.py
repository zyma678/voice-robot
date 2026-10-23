"""Golden-vector generator for the on-device memory port contract (plan P0).

This pins the *deterministic* pieces of the memory + prompt pipeline that the
ESP32-S3 firmware must replicate in C. The Python server is the authoritative
reference (ADR 0003); the firmware port is validated against the JSON baseline
this module emits.

Only deterministic behaviour is captured. LLM-produced text (Ark replies, the
LLM extraction payload) is *not* pinned here — for those the contract pins the
*prompt* and the *rule-based fallback*, not model output.

Run ``python -m tests.golden.generate_memory_contract`` (from repo root) to
regenerate ``tests/golden/memory_contract.json`` after an intentional change.
``tests/test_golden_contract.py`` asserts the committed baseline still matches.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from voice_robot.agent import routing
from voice_robot.agent.graph import VoiceAgent
from voice_robot.memory.context_builder import MemoryContextBuilder
from voice_robot.memory.conversation_store import ConversationStore
from voice_robot.memory.file_retriever import FileMemoryRetriever
from voice_robot.memory.file_store import FileMemoryStore
from voice_robot.memory.profile_extractor import ProfileMemoryExtractor
from voice_robot.memory.summary_store import build_local_summary
from voice_robot.services.llm import ArkChatLLMService, ArkPromptConfig


BASELINE_PATH = Path(__file__).with_name("memory_contract.json")

USER_ID = "xiaoming"

# Memory index fixture written directly (fixed ids + created_at) so retrieval
# ranking and its tiebreak are fully deterministic. ``last_accessed`` is unused
# by the real scorer (kept here only to mirror the on-disk schema).
MEMORY_INDEX: list[dict] = [
    {
        "id": "mem_a1",
        "category": "preference",
        "title": "喜欢恐龙",
        "summary": "用户表达了对恐龙的兴趣。",
        "tags": ["恐龙", "古生物"],
        "importance": 0.7,
        "status": "active",
        "confidence": 0.7,
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-01T00:00:00+00:00",
        "last_accessed": "2026-06-01T00:00:00+00:00",
        "source_turn_id": "turn_seed01",
        "path": "memories/xiaoming/preference/mem_a1_喜欢恐龙.md",
    },
    {
        "id": "mem_b2",
        "category": "learning",
        "title": "乘法口诀薄弱",
        "summary": "用户提到不会 7x8。",
        "tags": ["数学", "乘法"],
        "importance": 0.8,
        "status": "active",
        "confidence": 0.7,
        "created_at": "2026-06-02T00:00:00+00:00",
        "updated_at": "2026-06-02T00:00:00+00:00",
        "last_accessed": "2026-06-02T00:00:00+00:00",
        "source_turn_id": "turn_seed02",
        "path": "memories/xiaoming/learning/mem_b2_乘法口诀薄弱.md",
    },
    {
        "id": "mem_c3",
        "category": "event",
        "title": "周三语文考试",
        "summary": "用户说周三要考语文。",
        "tags": ["考试", "语文"],
        "importance": 0.6,
        "status": "resolved",
        "confidence": 0.8,
        "created_at": "2026-06-03T00:00:00+00:00",
        "updated_at": "2026-06-03T00:00:00+00:00",
        "last_accessed": "2026-06-03T00:00:00+00:00",
        "source_turn_id": "turn_seed03",
        "path": "memories/xiaoming/event/mem_c3_周三语文考试.md",
    },
    {
        "id": "mem_d4",
        "category": "knowledge",
        "title": "家里有猫咪咪",
        "summary": "用户家里养了一只猫叫咪咪。",
        "tags": ["猫", "宠物"],
        "importance": 0.5,
        "status": "active",
        "confidence": 0.6,
        "created_at": "2026-06-04T00:00:00+00:00",
        "updated_at": "2026-06-04T00:00:00+00:00",
        "last_accessed": "2026-06-04T00:00:00+00:00",
        "source_turn_id": "turn_seed04",
        "path": "memories/xiaoming/knowledge/mem_d4_家里有猫咪咪.md",
    },
    {
        # status=deleted must be skipped even though importance is highest.
        "id": "mem_e5",
        "category": "preference",
        "title": "已删除恐龙项",
        "summary": "恐龙 恐龙 恐龙",
        "tags": ["恐龙"],
        "importance": 0.9,
        "status": "deleted",
        "confidence": 0.9,
        "created_at": "2026-06-05T00:00:00+00:00",
        "updated_at": "2026-06-05T00:00:00+00:00",
        "last_accessed": "2026-06-05T00:00:00+00:00",
        "source_turn_id": "turn_seed05",
        "path": "memories/xiaoming/preference/mem_e5_已删除恐龙项.md",
    },
]

PROFILE: dict = {
    "name": "小明",
    "grade": "三年级",
    "interests": ["恐龙", "故事"],
    "weaknesses": ["乘法口诀"],
}

# Fixed turns for the local summary vector (sequence-bearing dicts).
SUMMARY_TURNS: list[dict] = [
    {"sequence": 1, "user_text": "我喜欢恐龙", "assistant_text": "恐龙很有趣呀。"},
    {"sequence": 2, "user_text": "讲个故事", "assistant_text": "好的，从前有只小恐龙……"},
    {"sequence": 3, "user_text": "7乘8等于几", "assistant_text": "等于56哦。"},
]

# Queries: raw + the agent-enriched form (VoiceAgent._memory_query), to show how
# Chinese run-tokenisation behaves and why enrichment matters.
RETRIEVAL_QUERIES = ["恐龙故事", "恐龙 故事", "乘法 7x8", "猫", "考试"]

# user_texts that exercise the deterministic orchestration routing decisions
# (safety gate + local-rule reply route), which the firmware must replicate 1:1.
ROUTING_TEXTS = [
    "讲一个恐龙故事",
    "出一道简单口算题",
    "现在几点了",
    "教我暴力伤害别人",
    "讲血腥故事",
    "今天数学课很有趣",  # math keyword wins even in casual chat
    "你好呀",
]

# user_texts that exercise the deterministic rule-based extraction fallback.
EXTRACTION_TEXTS = [
    "我喜欢恐龙",
    "给我讲个故事，要简短一点",
    "你以后叫小布吧",
    "我不会 7x8",
    "今天天气真好",
]


def _round(value: object) -> object:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_round(item) for item in value]
    if isinstance(value, dict):
        return {key: _round(val) for key, val in value.items()}
    return value


def _write_fixture(root: Path) -> None:
    store = FileMemoryStore(root)
    store.save_profile(USER_ID, dict(PROFILE))

    index_path = root / "memories" / USER_ID / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(MEMORY_INDEX, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    # A few summaries (created_at is non-deterministic but never surfaces in any
    # captured output — only start/end_sequence + content do).
    from voice_robot.memory.summary_store import SummaryStore

    summary_store = SummaryStore(root)
    summary_store.save_summary(
        USER_ID,
        start_sequence=1,
        end_sequence=20,
        content="- 用户多次聊到恐龙与故事。\n- 7x8 仍需巩固。",
        summary_source="local",
    )
    summary_store.save_summary(
        USER_ID,
        start_sequence=21,
        end_sequence=40,
        content="- 用户提到家里的猫咪咪。",
        summary_source="local",
    )

    # Recent turns for the context block's 最近20轮 section.
    conv = ConversationStore(root)
    for seq, (user_text, assistant_text) in enumerate(
        [
            ("咪咪今天好可爱", "猫咪真治愈呀。"),
            ("周三要考语文", "我们一起复习古诗好不好？"),
        ],
        start=41,
    ):
        # append_turn assigns sequence from meta; seed meta so sequences are 41,42.
        meta = conv.load_meta(USER_ID)
        meta["next_sequence"] = seq
        conv.save_meta(USER_ID, meta)
        conv.append_turn(
            USER_ID,
            user_text=user_text,
            assistant_text=assistant_text,
            reply_source="ark",
            fallback_reason=None,
            safety_passed=True,
        )


def build_contract() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_fixture(root)

        retriever = FileMemoryRetriever(root)
        builder = MemoryContextBuilder(root)

        # 1. keyword tokenisation + query enrichment + retrieval ranking/scores.
        retrieval: dict = {}
        for raw_query in RETRIEVAL_QUERIES:
            enriched = VoiceAgent._memory_query(raw_query)
            keywords = sorted(FileMemoryRetriever._keywords(enriched))
            ranked = retriever.search(enriched, USER_ID, top_k=3)
            scores = {}
            kw_set = FileMemoryRetriever._keywords(enriched)
            for entry in MEMORY_INDEX:
                score = retriever._safe_score(entry, kw_set)
                scores[entry["id"]] = _round(score) if score is not None else None
            retrieval[raw_query] = {
                "enriched_query": enriched,
                "keywords": keywords,
                "ranked_ids": [entry["id"] for entry in ranked],
                "scores": scores,
            }

        # 1b. orchestration routing decisions (pure, port-critical).
        orchestration_routing = {
            text: {
                "is_unsafe": routing.is_unsafe(text),
                "reply_route": routing.classify_reply_route(text),
                "enriched_query": routing.enrich_memory_query(text),
            }
            for text in ROUTING_TEXTS
        }

        # 2. assembled memory context block (lines).
        memory_block = builder.build(USER_ID, VoiceAgent._memory_query("恐龙 故事"))

        # 3. deterministic local summary.
        local_summary = build_local_summary(SUMMARY_TURNS)

        # 4. rule-based extraction fallback (volatile updated_at stripped).
        extractor = ProfileMemoryExtractor(llm_service=None)
        rule_extraction = {}
        for text in EXTRACTION_TEXTS:
            result = extractor._extract_with_rules(user_text=text, turn_id="turn_demo")
            for mem in result["structured_memories"]:
                mem.pop("updated_at", None)
            rule_extraction[text] = result

        # 5. extraction prompt (deterministic prompt string).
        extraction_prompt = extractor._build_prompt(
            current_profile=PROFILE, user_text="我喜欢恐龙"
        )

        # 6. Ark prompt assembly: default path + forced-injection path.
        default_messages, default_debug = ArkChatLLMService._build_messages(
            user_text="给我讲个恐龙故事",
            user_id=USER_ID,
            memory_hints=[],
            memory_block=memory_block,
            prompt_config=ArkPromptConfig(),
        )
        forced_messages, forced_debug = ArkChatLLMService._build_messages(
            user_text="你好",
            user_id=USER_ID,
            memory_hints=[
                {"title": "喜欢恐龙", "summary": "对恐龙感兴趣", "tags": ["恐龙"]}
            ],
            memory_block="",
            prompt_config=ArkPromptConfig(
                system_prompt="你是测试机器人。",
                user_prompt_template="用户: {user_text}",  # no {memory_block}
            ),
        )

        # 7. on-disk markdown renderers (firmware must byte-match for sync).
        memory_markdown = FileMemoryStore._render_memory_markdown(
            memory_id="mem_a1",
            category="preference",
            importance=0.7,
            tags=["恐龙", "古生物"],
            status="active",
            confidence=1.0,
            source_turn_id="turn_seed01",
            title="喜欢恐龙",
            summary="用户表达了对恐龙的兴趣。",
        )
        from voice_robot.memory.summary_store import SummaryStore

        summary_markdown = SummaryStore._format_markdown(
            summary_id="sum_000001_000020",
            user_id=USER_ID,
            start_sequence=1,
            end_sequence=20,
            summary_source="local",
            content="- 用户多次聊到恐龙与故事。",
        )

        # 8. path/id normalisation.
        safe_component = {
            sample: FileMemoryStore._safe_component(sample)
            for sample in ["xiaoming", "小明 123", "a/b\\c", "  ", "用户@#$名"]
        }
        safe_filename = {
            sample: FileMemoryStore._safe_filename(sample)
            for sample in ["喜欢恐龙", "7x8 不会", "a/b", ""]
        }

        return {
            "_meta": {
                "purpose": "on-device memory port contract golden vectors (plan P0)",
                "reference": "ADR 0003; docs/design/specs/on-device-memory-port-contract.md",
                "authoritative_impl": "voice_robot/ (Python)",
            },
            "retrieval": _round(retrieval),
            "orchestration_routing": orchestration_routing,
            "memory_block_lines": memory_block.split("\n"),
            "local_summary": local_summary,
            "rule_extraction": _round(rule_extraction),
            "extraction_prompt": extraction_prompt,
            "ark_prompt_default": {
                "messages": default_messages,
                "debug": default_debug,
            },
            "ark_prompt_forced_injection": {
                "messages": forced_messages,
                "debug": forced_debug,
            },
            "memory_markdown": memory_markdown,
            "summary_markdown": summary_markdown,
            "safe_component": safe_component,
            "safe_filename": safe_filename,
        }


def main() -> None:
    contract = build_contract()
    BASELINE_PATH.write_text(
        json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {BASELINE_PATH}")


if __name__ == "__main__":
    main()
