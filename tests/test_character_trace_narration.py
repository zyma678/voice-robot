from voice_robot.character_trace import character_trace_payload
from voice_robot.character_trace_narration import build_trace_narration


def test_build_trace_narration_uses_template_for_ten() -> None:
    narration = build_trace_narration("十", character_trace_payload("十"))

    assert "正在写“十”字" in narration
    assert "先写一横" in narration
    assert "再写一竖" in narration
    assert "小路口" in narration


def test_build_trace_narration_uses_template_for_elephant() -> None:
    narration = build_trace_narration("象", character_trace_payload("象"))

    assert "正在写“象”字" in narration
    assert "上面" in narration
    assert "身子" in narration
    assert "大象" in narration


def test_build_trace_narration_falls_back_for_supported_hanzi_without_template() -> None:
    narration = build_trace_narration("日", character_trace_payload("日"))

    assert "正在写“日”字" in narration
    assert "一共有4笔" in narration
    assert "一笔一笔" in narration
    assert "记住" in narration
