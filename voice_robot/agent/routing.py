"""Pure, deterministic orchestration decisions for the voice agent.

These are the parts of the agent pipeline the firmware C port must replicate
1:1 (ADR 0003 / docs/design/specs/on-device-memory-port-contract.md §3, §10).
They are kept free of I/O and async machinery so both the Python reference and
the C port can be unit-tested against the same golden vectors.

`graph.py` delegates to these; do not fork the keyword lists.
"""

from typing import Literal

# W1 keyword gate only; production content safety should use a dedicated service.
UNSAFE_KEYWORDS = ("暴力", "伤害", "打人", "杀", "自杀", "流血", "武器", "色情", "血腥")

# Local-rule reply routes, checked in order: math wins over time, else LLM.
MATH_KEYWORDS = ("口算", "数学", "算题")
TIME_KEYWORDS = ("时间", "几点")

# Terms appended (space-separated) to a query before structured-memory retrieval,
# so Chinese run-tokenisation still yields usable keywords (see contract §3.2).
KNOWN_QUERY_TERMS = ("恐龙", "故事", "数学", "乘法", "口算")

ReplyRoute = Literal["math", "time", "llm"]


def is_unsafe(text: str) -> bool:
    return any(keyword in text for keyword in UNSAFE_KEYWORDS)


def classify_reply_route(text: str) -> ReplyRoute:
    """Pick the local-rule reply branch. Order matters: math before time."""
    if any(keyword in text for keyword in MATH_KEYWORDS):
        return "math"
    if any(keyword in text for keyword in TIME_KEYWORDS):
        return "time"
    return "llm"


def enrich_memory_query(text: str) -> str:
    matched_terms = [term for term in KNOWN_QUERY_TERMS if term in text]
    if not matched_terms:
        return text
    return " ".join([text, *matched_terms])
