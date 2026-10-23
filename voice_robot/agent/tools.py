def generate_math_problem() -> str:
    left = 7
    right = 8
    return f"小明，来试试：{left} 加 {right} 等于多少？"


def generate_story(memory_hints: list[dict] | None = None) -> str:
    topic = "小恐龙"
    for memory in memory_hints or []:
        text = " ".join(
            [
                str(memory.get("title", "")),
                str(memory.get("summary", "")),
                " ".join(memory.get("tags", [])),
            ]
        )
        if "恐龙" in text:
            topic = "小恐龙"
            break
    return f"{topic}找到一片星星叶，送给迷路的小兔。它们笑着说：一起回家吧。"


def get_current_time_text() -> str:
    return "现在是小智练习时间。"
