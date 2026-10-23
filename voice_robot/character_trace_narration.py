from typing import Any


_TRACE_NARRATION_TEMPLATES: dict[str, str] = {
    "十": (
        "正在写“十”字。先写一横，再写一竖，横平竖直，像一个小路口。"
        "记住：一横一竖交在中间，就是十。"
    ),
    "象": (
        "正在写“象”字。按从上到下写：上面先写小脑袋，中间写方方的身子，"
        "下面几笔像大象的腿和长鼻子。记住：头、身子、腿鼻子排好，就是象。"
    ),
}


def build_trace_narration(character: str, trace_payload: dict[str, Any] | None) -> str:
    """Return a short, child-friendly narration for synchronized tracing."""
    normalized_character = str(character or "").strip()
    if normalized_character in _TRACE_NARRATION_TEMPLATES:
        return _TRACE_NARRATION_TEMPLATES[normalized_character]

    stroke_count = _stroke_count(trace_payload)
    if stroke_count is None:
        stroke_hint = "跟着红线"
    else:
        stroke_hint = f"一共有{stroke_count}笔，跟着红线"
    return (
        f"正在写“{normalized_character}”字。{stroke_hint}从上到下、从左到右，"
        "一笔一笔慢慢写。记住：先看清结构，再把每一笔描准。"
    )


def _stroke_count(trace_payload: dict[str, Any] | None) -> int | None:
    if not isinstance(trace_payload, dict):
        return None
    strokes = trace_payload.get("strokes")
    if not isinstance(strokes, list):
        return None
    return len(strokes)
