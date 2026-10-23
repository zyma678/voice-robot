import pytest

from voice_robot.character_trace import (
    character_trace_payload,
    extract_trace_character,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("十字怎么写", "十"),
        ("十 字怎么写", "十"),
        ("，十字怎么写？", "十"),
        ("十怎么写", "十"),
        ("大象的象怎么写", "象"),
        ("大 象 的 象 怎么写", "象"),
        ("，大象的象怎么写？", "象"),
        ("饺怎么写", "饺"),
        ("饺咋写", "饺"),
        ("饺如何写", "饺"),
        ("“饺”字怎么写", "饺"),
        ("饺子的饺怎么写啊", "饺"),
        ("饺子的饺该怎么写", "饺"),
        ("饺子的饺要怎么写", "饺"),
        ("饺子的饺是怎么写的", "饺"),
        ("写一下饺子的饺", "饺"),
        ("写一下大象的象", "象"),
        ("，写一下饺子的饺！", "饺"),
        ("写一写饺子的饺", "饺"),
        ("教我写饺子的饺", "饺"),
        ("帮我写一下饺子的饺", "饺"),
        ("请写一下饺子的饺", "饺"),
        ("能写一下饺子的饺吗", "饺"),
        ("大象的象字怎么写", "象"),
        ("写一下大象的象字", "象"),
        ("写一下大象的象这个字", "象"),
        # Fuzzier phrasings (2026-06-19): leading fillers, extra suffixes/prefixes
        # and modal particles, while keeping the strict candidate validation.
        ("请问饺子的饺怎么写", "饺"),
        ("我想知道饺怎么写", "饺"),
        ("你知道大象的象怎么写吗", "象"),
        ("教教我饺子的饺怎么写", "饺"),
        ("饺字怎么写哈", "饺"),
        ("饺字怎么写来着", "饺"),
        ("饺怎样写", "饺"),
        ("饺子的饺怎么书写", "饺"),
        ("饺子的饺是怎么写", "饺"),
        ("饺字的写法", "饺"),
        ("饺的写法", "饺"),
        ("写个大象的象", "象"),
        ("写一个饺子的饺", "饺"),
        ("我想写饺子的饺", "饺"),
        ("麻烦写一下饺子的饺", "饺"),
    ],
)
def test_extract_trace_character_matches_single_hanzi(text: str, expected: str) -> None:
    assert extract_trace_character(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "十怎么读",
        "十 在怎么读",
        "十读什么",
        "恐龙字怎么写",
        "怎么写十字",
        "abc字怎么写",
        "大象的恐龙怎么写",
        "大象的abc怎么写",
        "大象象怎么写",
        "饺子的饺怎么读",
        "饺子的饺什么意思",
        "写一下饺子的恐",
        "写一下饺子",
        "教我写饺子",
        "写一下abc的a",
        # New fuzzy prefixes/suffixes must not over-trigger on non-single targets.
        "我想写作业",
        "写个生日礼物",
        "饺子的写法",
        "请问今天天气怎么样",
        "",
    ],
)
def test_extract_trace_character_rejects_non_single_hanzi(text: str) -> None:
    assert extract_trace_character(text) is None


def test_character_trace_payload_regularizes_basic_hanzi_into_even_guides() -> None:
    payload = character_trace_payload("十")

    assert payload is not None
    assert payload["character"] == "十"
    assert payload["duration_ms"] == 1800
    assert len(payload["strokes"]) == 2
    heng, shu = payload["strokes"]
    assert len(heng) >= 5
    assert len(shu) >= 5
    assert len({point[1] for point in heng}) == 1
    assert len({point[0] for point in shu}) == 1
    assert heng[0][0] < heng[-1][0]
    assert shu[0][1] < shu[-1][1]


def test_character_trace_payload_supports_example_hanzi_from_context_question() -> None:
    payload = character_trace_payload("象")

    assert payload is not None
    assert payload["character"] == "象"
    assert len(payload["strokes"]) == 11


def test_character_trace_payload_rounds_cornered_strokes_within_firmware_budget() -> None:
    payload = character_trace_payload("象")

    assert payload is not None
    assert len(payload["strokes"][1]) > 3
    assert len(payload["strokes"][3]) > 3
    assert all(2 <= len(stroke) <= 32 for stroke in payload["strokes"])
    assert all(
        0 <= axis <= 1000
        for stroke in payload["strokes"]
        for point in stroke
        for axis in point
    )


def test_character_trace_payload_supports_jiao_for_write_prefix_question() -> None:
    payload = character_trace_payload("饺")

    assert payload is not None
    assert payload["character"] == "饺"
    assert len(payload["strokes"]) >= 8


def test_character_trace_payload_includes_kaiti_outlines_aligned_with_strokes() -> None:
    payload = character_trace_payload("象")

    assert payload is not None
    outlines = payload.get("outlines")
    assert outlines is not None
    # One filled-outline entry per stroke (median), in order.
    assert len(outlines) == len(payload["strokes"]) == 11
    for stroke_outline in outlines:
        # Each stroke has at least one closed contour (>= 3 vertices), all in box.
        assert stroke_outline
        for contour in stroke_outline:
            assert len(contour) >= 3
            assert all(0 <= x <= 1000 and 0 <= y <= 1000 for x, y in contour)


def test_character_trace_payload_returns_none_for_unsupported_hanzi() -> None:
    # "龘" is outside the MakeMeAHanzi dataset (9k common chars). This keeps the
    # "missing data" path covered even after we add the full dataset file.
    assert character_trace_payload("龘") is None
