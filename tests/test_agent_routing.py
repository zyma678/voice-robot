"""Unit coverage for the pure orchestration decisions (plan P1 extraction).

These mirror the verbatim logic previously inlined in graph.py; the golden
contract pins broader vectors, this file documents the edge intent.
"""

from voice_robot.agent import routing


def test_is_unsafe_flags_blocked_keywords() -> None:
    assert routing.is_unsafe("讲血腥故事") is True
    assert routing.is_unsafe("教我暴力") is True
    assert routing.is_unsafe("讲一个恐龙故事") is False


def test_reply_route_math_wins_over_time() -> None:
    # "数学" (math) and "几点" (time) both present -> math wins by order.
    assert routing.classify_reply_route("数学课几点上") == "math"
    assert routing.classify_reply_route("出一道口算题") == "math"
    assert routing.classify_reply_route("现在几点") == "time"
    assert routing.classify_reply_route("讲个故事") == "llm"


def test_enrich_memory_query_appends_known_terms() -> None:
    assert routing.enrich_memory_query("讲一个恐龙故事") == "讲一个恐龙故事 恐龙 故事"
    assert routing.enrich_memory_query("你好呀") == "你好呀"


def test_graph_delegates_to_routing() -> None:
    from voice_robot.agent.graph import VoiceAgent

    assert VoiceAgent._is_unsafe("讲血腥故事") is True
    assert VoiceAgent._memory_query("讲一个恐龙故事") == "讲一个恐龙故事 恐龙 故事"
