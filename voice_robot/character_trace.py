import copy
import math
import re
from functools import lru_cache
from typing import Any
import json
from pathlib import Path


_COMMON_PUNCTUATION = "，。！？、,.!?；;：:（）()【】[]{}《》<>\"'“”‘’"
_SINGLE_HANZI_RE = re.compile(r"^[\u4e00-\u9fff]$")
_TRAILING_MODAL_PARTICLES = (
    "来着",  # multi-char first so it strips before single particles
    "啊", "呀", "呢", "嘛", "吗", "吧", "哈", "哦", "喔", "噢", "啦", "呐", "哟", "捏",
)
# Leading filler phrases people prepend to a "how to write X" question. Stripped
# so the core "X怎么写 / 写一下X" patterns below still match. The strict candidate
# validation (single hanzi, or X字, or X的Y字 with Y in X) keeps false positives
# out, so broadening the surrounding phrasing is safe.
_LEADING_FILLERS = (
    "请问一下", "请问下", "请问", "我想问一下", "我想问下", "我想问问", "我想问",
    "想问一下", "想问下", "想问问", "我想知道", "想知道", "我不知道",
    "你知道", "你知不知道", "知不知道", "我想学写", "我想学", "我想学学",
    "教教我", "麻烦你", "麻烦", "帮我看看", "帮我看一下", "我想看看",
    "告诉我", "你能告诉我", "能不能告诉我", "我不会写", "我不会",
)
_WRITE_QUERY_SUFFIXES = (
    "是怎么写的",
    "是怎么写",
    "该怎么写",
    "要怎么写",
    "怎么写的",
    "怎么书写",
    "如何书写",
    "怎样书写",
    "如何写",
    "怎样写",
    "怎么写",
    "咋写的",
    "咋写",
    "字的写法",
    "的写法",
)
_WRITE_COMMAND_PREFIXES = (
    "帮我写一下",
    "帮我写一写",
    "帮我写个",
    "帮我写一个",
    "请写一下",
    "请写一写",
    "能写一下",
    "能写一写",
    "可以写一下",
    "可以写一写",
    "教我写一下",
    "教我写一写",
    "教我写个",
    "教我写一个",
    "教我写",
    "我想写",
    "我要写",
    "写一下",
    "写一写",
    "写一个",
    "写个",
)

_TRACE_AXIS_DOMINANCE = 2.8
_TRACE_AXIS_SNAP_TOLERANCE = 120.0
_TRACE_MIN_POINTS = 5
_TRACE_MAX_POINTS = 32
_TRACE_CURVE_RADIUS = 0.28
_TRACE_MAX_RADIUS = 58.0
_TRACE_MIN_RADIUS = 16.0
_TRACE_MIN_ROUND_LENGTH = 42.0

# Filled 楷体 stroke outlines (真正正楷): flatten the dataset's SVG outline paths
# into polygons, simplify, and cap per-contour vertices to keep payloads bounded.
_TRACE_OUTLINE_BEZIER_STEPS = 8
_TRACE_OUTLINE_SIMPLIFY_EPS = 5.0   # 0..1000 space; RDP perpendicular tolerance
_TRACE_OUTLINE_MAX_POINTS = 96      # per contour, after simplification


_STROKE_DATA: dict[str, list[list[list[int]]]] = {
    "一": [
        [[180, 500], [500, 500], [820, 500]],
    ],
    "二": [
        [[260, 360], [500, 360], [740, 360]],
        [[180, 620], [500, 620], [820, 620]],
    ],
    "三": [
        [[250, 300], [500, 300], [750, 300]],
        [[220, 500], [500, 500], [780, 500]],
        [[160, 700], [500, 700], [840, 700]],
    ],
    "十": [
        [[180, 280], [500, 280], [820, 280]],
        [[500, 200], [500, 500], [500, 820]],
    ],
    "人": [
        [[520, 180], [470, 360], [360, 580], [220, 800]],
        [[520, 230], [600, 430], [700, 620], [830, 800]],
    ],
    "口": [
        [[250, 250], [250, 760]],
        [[250, 250], [760, 250], [760, 760]],
        [[250, 760], [760, 760]],
    ],
    "日": [
        [[260, 200], [260, 820]],
        [[260, 200], [760, 200], [760, 820]],
        [[260, 510], [760, 510]],
        [[260, 820], [760, 820]],
    ],
    "象": [
        [[470, 130], [405, 210], [340, 270]],
        [[395, 215], [600, 215], [535, 310]],
        [[335, 320], [335, 500]],
        [[335, 320], [690, 320], [690, 500]],
        [[335, 500], [690, 500]],
        [[520, 500], [420, 610], [285, 700]],
        [[475, 555], [565, 650], [545, 830], [470, 840]],
        [[535, 610], [410, 710], [265, 780]],
        [[570, 675], [445, 790], [310, 865]],
        [[610, 740], [515, 840], [400, 915]],
        [[585, 710], [700, 805], [815, 900]],
    ],
    "饺": [
        [[235, 235], [320, 320], [360, 415]],
        [[240, 590], [345, 520], [430, 455]],
        [[485, 210], [485, 835]],
        [[485, 225], [750, 225], [810, 335]],
        [[620, 225], [565, 405], [525, 535]],
        [[585, 390], [725, 500], [835, 610]],
        [[510, 555], [635, 680], [760, 835]],
        [[710, 565], [600, 675], [515, 760]],
        [[445, 835], [830, 835]],
    ],
}

_DEFAULT_GRAPHICS_PATH = (
    Path(__file__).resolve().parent / "resources" / "hanzi" / "graphics.txt"
)
_DATASET_CANVAS_SIZE = 1000.0
_DATASET_MARGIN = 72.0


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clamp_point(point: tuple[float, float]) -> tuple[float, float]:
    return (_clamp(point[0], 0.0, 1000.0), _clamp(point[1], 0.0, 1000.0))


def _point_towards(
    start: tuple[float, float], end: tuple[float, float], distance: float
) -> tuple[float, float]:
    total = _distance(start, end)
    if total <= 1e-6:
        return start
    ratio = distance / total
    return (
        start[0] + (end[0] - start[0]) * ratio,
        start[1] + (end[1] - start[1]) * ratio,
    )


def _segment_is_horizontal(dx: float, dy: float) -> bool:
    return abs(dx) >= abs(dy) * _TRACE_AXIS_DOMINANCE and abs(dy) <= _TRACE_AXIS_SNAP_TOLERANCE


def _segment_is_vertical(dx: float, dy: float) -> bool:
    return abs(dy) >= abs(dx) * _TRACE_AXIS_DOMINANCE and abs(dx) <= _TRACE_AXIS_SNAP_TOLERANCE


def _snap_stroke_axes(
    stroke: tuple[tuple[int, int], ...]
) -> list[tuple[float, float]]:
    if not stroke:
        return []

    xs = [float(x) for x, _ in stroke]
    ys = [float(y) for _, y in stroke]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    width = max_x - min_x
    height = max_y - min_y

    # Decide axis snapping per-stroke (not per-segment) so tiny curvature at a
    # stroke's head doesn't accidentally flip a clearly vertical stroke into a
    # horizontal segment (e.g. dataset medians).
    if height <= _TRACE_AXIS_SNAP_TOLERANCE and width >= height * _TRACE_AXIS_DOMINANCE:
        y = sum(ys) / max(1.0, float(len(ys)))
        return [(float(x), y) for x in xs]
    if width <= _TRACE_AXIS_SNAP_TOLERANCE and height >= width * _TRACE_AXIS_DOMINANCE:
        x = sum(xs) / max(1.0, float(len(xs)))
        return [(x, float(y)) for y in ys]

    # Fallback: gentle per-segment snapping to remove only obvious jitter.
    snapped: list[tuple[float, float]] = [tuple(map(float, stroke[0]))]
    for raw_x, raw_y in stroke[1:]:
        prev_x, prev_y = snapped[-1]
        next_x = float(raw_x)
        next_y = float(raw_y)
        dx = next_x - prev_x
        dy = next_y - prev_y
        if _segment_is_horizontal(dx, dy):
            next_y = prev_y
        elif _segment_is_vertical(dx, dy):
            next_x = prev_x
        snapped.append((next_x, next_y))
    return snapped


def _line_samples(start: tuple[float, float], end: tuple[float, float], steps: int) -> list[tuple[float, float]]:
    if steps <= 1:
        return [start, end]
    return [
        (
            start[0] + (end[0] - start[0]) * (index / steps),
            start[1] + (end[1] - start[1]) * (index / steps),
        )
        for index in range(steps + 1)
    ]


def _quadratic_samples(
    start: tuple[float, float],
    control: tuple[float, float],
    end: tuple[float, float],
    steps: int,
) -> list[tuple[float, float]]:
    if steps <= 1:
        return [start, end]
    points: list[tuple[float, float]] = []
    for index in range(steps + 1):
        t = index / steps
        one_minus_t = 1.0 - t
        points.append(
            (
                one_minus_t * one_minus_t * start[0]
                + 2.0 * one_minus_t * t * control[0]
                + t * t * end[0],
                one_minus_t * one_minus_t * start[1]
                + 2.0 * one_minus_t * t * control[1]
                + t * t * end[1],
            )
        )
    return points


def _cubic_samples(
    start: tuple[float, float],
    control1: tuple[float, float],
    control2: tuple[float, float],
    end: tuple[float, float],
    steps: int,
) -> list[tuple[float, float]]:
    if steps <= 1:
        return [start, end]
    points: list[tuple[float, float]] = []
    for index in range(steps + 1):
        t = index / steps
        u = 1.0 - t
        a = u * u * u
        b = 3.0 * u * u * t
        c = 3.0 * u * t * t
        d = t * t * t
        points.append(
            (
                a * start[0] + b * control1[0] + c * control2[0] + d * end[0],
                a * start[1] + b * control1[1] + c * control2[1] + d * end[1],
            )
        )
    return points


def _corner_cosine(
    prev_point: tuple[float, float],
    point: tuple[float, float],
    next_point: tuple[float, float],
) -> float:
    in_x = prev_point[0] - point[0]
    in_y = prev_point[1] - point[1]
    out_x = next_point[0] - point[0]
    out_y = next_point[1] - point[1]
    in_len = math.hypot(in_x, in_y)
    out_len = math.hypot(out_x, out_y)
    if in_len <= 1e-6 or out_len <= 1e-6:
        return -1.0
    return (in_x * out_x + in_y * out_y) / (in_len * out_len)


def _rounded_path(stroke: tuple[tuple[int, int], ...]) -> list[tuple[float, float]]:
    snapped = _snap_stroke_axes(stroke)
    if len(snapped) <= 2:
        return snapped

    rounded: list[tuple[float, float]] = [snapped[0]]
    cursor = snapped[0]
    for index in range(1, len(snapped) - 1):
        prev_point = snapped[index - 1]
        point = snapped[index]
        next_point = snapped[index + 1]
        in_len = _distance(prev_point, point)
        out_len = _distance(point, next_point)
        corner_cosine = _corner_cosine(prev_point, point, next_point)
        should_round = (
            min(in_len, out_len) >= _TRACE_MIN_ROUND_LENGTH and corner_cosine > -0.94
        )
        if not should_round:
            rounded.extend(_line_samples(cursor, point, 2)[1:])
            cursor = point
            continue

        radius = _clamp(
            min(in_len, out_len) * _TRACE_CURVE_RADIUS,
            _TRACE_MIN_RADIUS,
            _TRACE_MAX_RADIUS,
        )
        entry = _point_towards(point, prev_point, radius)
        exit = _point_towards(point, next_point, radius)
        straight_steps = max(2, int(round(_distance(cursor, entry) / 90.0)))
        curve_steps = max(4, int(round(radius / 12.0)))
        rounded.extend(_line_samples(cursor, entry, straight_steps)[1:])
        rounded.extend(_quadratic_samples(entry, point, exit, curve_steps)[1:])
        cursor = exit
    rounded.extend(_line_samples(cursor, snapped[-1], 2)[1:])
    return [_clamp_point(point) for point in rounded]


def _stroke_target_point_count(
    stroke: tuple[tuple[int, int], ...], path: list[tuple[float, float]]
) -> int:
    path_length = sum(_distance(path[index], path[index + 1]) for index in range(len(path) - 1))
    corner_count = max(0, len(stroke) - 2)
    base_count = max(_TRACE_MIN_POINTS, int(round(path_length / 105.0)) + 2)
    target = max(len(stroke) + corner_count * 2, base_count)
    return min(_TRACE_MAX_POINTS, target)


def _resample_path(
    path: list[tuple[float, float]], target_count: int
) -> list[tuple[int, int]]:
    if not path:
        return []
    if len(path) == 1:
        x, y = path[0]
        return [(int(round(x)), int(round(y)))]

    cumulative = [0.0]
    for index in range(1, len(path)):
        cumulative.append(cumulative[-1] + _distance(path[index - 1], path[index]))
    total_length = cumulative[-1]
    if total_length <= 1e-6:
        x, y = path[0]
        return [(int(round(x)), int(round(y)))] * min(target_count, 2)

    resampled: list[tuple[int, int]] = []
    for index in range(target_count):
        target_distance = total_length * (index / max(1, target_count - 1))
        segment_index = 1
        while segment_index < len(cumulative) and cumulative[segment_index] < target_distance:
            segment_index += 1
        if segment_index >= len(path):
            point = path[-1]
        else:
            start = path[segment_index - 1]
            end = path[segment_index]
            segment_start = cumulative[segment_index - 1]
            segment_length = max(1e-6, cumulative[segment_index] - segment_start)
            ratio = (target_distance - segment_start) / segment_length
            point = (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
        normalized = (
            int(round(_clamp(point[0], 0.0, 1000.0))),
            int(round(_clamp(point[1], 0.0, 1000.0))),
        )
        if not resampled or normalized != resampled[-1]:
            resampled.append(normalized)

    if len(resampled) == 1:
        resampled.append(resampled[0])
    return resampled[:_TRACE_MAX_POINTS]


def _regularize_stroke(stroke: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    path = _rounded_path(stroke)
    target_count = _stroke_target_point_count(stroke, path)
    resampled = _resample_path(path, target_count)
    return tuple(resampled[:_TRACE_MAX_POINTS])


def _normalize_question(text: str) -> str:
    compact = "".join(str(text or "").split())
    for quote in ('"', "'", "“", "”", "‘", "’"):
        compact = compact.replace(quote, "")
    return compact.strip(_COMMON_PUNCTUATION)


def _strip_leading_fillers(text: str) -> str:
    normalized = text
    changed = True
    while changed:
        changed = False
        for filler in _LEADING_FILLERS:
            if normalized.startswith(filler):
                normalized = normalized[len(filler) :].strip(_COMMON_PUNCTUATION)
                changed = True
                break
    return normalized


def extract_trace_character(text: str) -> str | None:
    normalized = _strip_trailing_modal_particles(
        _strip_leading_fillers(_normalize_question(text))
    )
    candidate = _extract_from_write_query(normalized)
    if candidate:
        return candidate
    for prefix in _WRITE_COMMAND_PREFIXES:
        if normalized.startswith(prefix):
            candidate = _extract_target_fragment(normalized[len(prefix) :])
            if candidate:
                return candidate
    return None


def _strip_trailing_modal_particles(text: str) -> str:
    normalized = text
    changed = True
    while changed:
        changed = False
        for particle in _TRAILING_MODAL_PARTICLES:
            if normalized.endswith(particle):
                normalized = normalized[: -len(particle)].strip(_COMMON_PUNCTUATION)
                changed = True
                break
    return normalized


def _extract_from_write_query(text: str) -> str | None:
    direct_suffix = "字怎么写"
    if text.endswith(direct_suffix):
        candidate = text[: -len(direct_suffix)].strip(_COMMON_PUNCTUATION)
        if _SINGLE_HANZI_RE.match(candidate):
            return candidate

    for suffix in _WRITE_QUERY_SUFFIXES:
        if not text.endswith(suffix):
            continue
        candidate = _extract_target_fragment(text[: -len(suffix)])
        if candidate:
            return candidate
    return None


def _extract_target_fragment(text: str) -> str | None:
    fragment = text.strip(_COMMON_PUNCTUATION)
    if fragment.endswith("这个字"):
        fragment = fragment[: -len("这个字")].strip(_COMMON_PUNCTUATION)
    if _SINGLE_HANZI_RE.match(fragment):
        return fragment
    if fragment.endswith("字") and _SINGLE_HANZI_RE.match(fragment[:-1]):
        return fragment[:-1]
    match = re.fullmatch(r"(.+)的([\u4e00-\u9fff])字", fragment)
    if match:
        context, candidate = match.groups()
        if candidate in context:
            return candidate
    match = re.fullmatch(r"(.+)的([\u4e00-\u9fff])", fragment)
    if match:
        context, candidate = match.groups()
        if candidate in context:
            return candidate
    return None


def extract_pronunciation_character(text: str) -> str | None:
    return extract_trace_character(text)

# MakeMeAHanzi outline paths use only absolute M/L/Q/C/Z commands.
_SVG_TOKEN_RE = re.compile(r"[MLQCZmlqcz]|-?\d+\.?\d*")


def _parse_svg_outline(path_data: str) -> list[list[tuple[float, float]]]:
    """Flatten a MakeMeAHanzi stroke outline (SVG path) into raw-coord contours.

    Beziers (Q/C) are subdivided into polylines; each subpath (M…Z) becomes one
    closed contour. Coordinates stay in the dataset's raw (y-up) space; the
    shared normalization below maps them into our 0..1000 device box.
    """
    tokens = _SVG_TOKEN_RE.findall(path_data or "")
    contours: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    cmd = ""
    cx = cy = 0.0
    start_x = start_y = 0.0
    i = 0
    n = len(tokens)

    def _num(idx: int) -> float:
        return float(tokens[idx])

    while i < n:
        token = tokens[i]
        upper = token.upper()
        if upper in "MLQCZ":
            cmd = upper
            i += 1
            if cmd == "Z":
                if current:
                    contours.append(current)
                    current = []
                cx, cy = start_x, start_y
                continue
        if cmd in ("M", "L"):
            if i + 1 >= n:
                break
            cx, cy = _num(i), _num(i + 1)
            i += 2
            if cmd == "M":
                if current:
                    contours.append(current)
                current = [(cx, cy)]
                start_x, start_y = cx, cy
                cmd = "L"  # implicit lineto for repeated coordinate pairs
            else:
                current.append((cx, cy))
        elif cmd == "Q":
            if i + 3 >= n:
                break
            x1, y1, x2, y2 = _num(i), _num(i + 1), _num(i + 2), _num(i + 3)
            i += 4
            current.extend(
                _quadratic_samples((cx, cy), (x1, y1), (x2, y2), _TRACE_OUTLINE_BEZIER_STEPS)[1:]
            )
            cx, cy = x2, y2
        elif cmd == "C":
            if i + 5 >= n:
                break
            x1, y1, x2, y2, x3, y3 = (_num(i + k) for k in range(6))
            i += 6
            current.extend(
                _cubic_samples(
                    (cx, cy), (x1, y1), (x2, y2), (x3, y3), _TRACE_OUTLINE_BEZIER_STEPS
                )[1:]
            )
            cx, cy = x3, y3
        else:
            i += 1
    if current:
        contours.append(current)
    return contours


def _compute_norm(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Center + uniform scale mapping the ink bbox into the 0..1000 box w/ margin."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    width = max(1.0, max(xs) - min(xs))
    height = max(1.0, max(ys) - min(ys))
    span = max(width, height)
    usable = max(1.0, _DATASET_CANVAS_SIZE - 2.0 * _DATASET_MARGIN)
    scale = usable / span
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    return cx, cy, scale


def _apply_norm(x: float, y: float, norm: tuple[float, float, float]) -> tuple[float, float]:
    cx, cy, scale = norm
    xf = (x - cx) * scale + (_DATASET_CANVAS_SIZE / 2.0)
    # Flip Y (dataset is y-up, screen is y-down).
    yf = (cy - y) * scale + (_DATASET_CANVAS_SIZE / 2.0)
    return _clamp(xf, 0.0, _DATASET_CANVAS_SIZE), _clamp(yf, 0.0, _DATASET_CANVAS_SIZE)


def _normalize_medians_with(
    medians: list[list[list[int]]], norm: tuple[float, float, float]
) -> list[tuple[tuple[int, int], ...]]:
    out: list[tuple[tuple[int, int], ...]] = []
    for stroke in medians:
        normalized: list[tuple[int, int]] = []
        for item in stroke:
            if not isinstance(item, list) or len(item) != 2:
                continue
            try:
                xf, yf = _apply_norm(float(item[0]), float(item[1]), norm)
            except (TypeError, ValueError):
                continue
            point = (int(round(xf)), int(round(yf)))
            if not normalized or point != normalized[-1]:
                normalized.append(point)
        if len(normalized) >= 2:
            out.append(tuple(normalized))
    return out


def _rdp_simplify(
    points: list[tuple[float, float]], epsilon: float
) -> list[tuple[float, float]]:
    """Ramer–Douglas–Peucker simplification of a polyline."""
    if len(points) <= 2:
        return points
    start, end = points[0], points[-1]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    seg_len = math.hypot(dx, dy)
    max_dist = -1.0
    index = 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        if seg_len <= 1e-6:
            dist = math.hypot(px - start[0], py - start[1])
        else:
            dist = abs(dy * px - dx * py + end[0] * start[1] - end[1] * start[0]) / seg_len
        if dist > max_dist:
            max_dist = dist
            index = i
    if max_dist > epsilon:
        left = _rdp_simplify(points[: index + 1], epsilon)
        right = _rdp_simplify(points[index:], epsilon)
        return left[:-1] + right
    return [start, end]


def _decimate(points: list[tuple[float, float]], max_count: int) -> list[tuple[float, float]]:
    if len(points) <= max_count:
        return points
    step = len(points) / float(max_count)
    return [points[int(i * step)] for i in range(max_count)]


def _normalize_outlines_with(
    raw_outlines: list[list[list[tuple[float, float]]]],
    norm: tuple[float, float, float],
) -> list[list[list[list[int]]]]:
    result: list[list[list[list[int]]]] = []
    for contours in raw_outlines:
        norm_contours: list[list[list[int]]] = []
        for contour in contours:
            poly = [_apply_norm(x, y, norm) for x, y in contour]
            poly = _rdp_simplify(poly, _TRACE_OUTLINE_SIMPLIFY_EPS)
            if len(poly) > _TRACE_OUTLINE_MAX_POINTS:
                poly = _decimate(poly, _TRACE_OUTLINE_MAX_POINTS)
            if len(poly) >= 3:
                norm_contours.append([[int(round(px)), int(round(py))] for px, py in poly])
        result.append(norm_contours)
    return result


@lru_cache(maxsize=512)
def _load_graphics_record(character: str) -> dict[str, Any] | None:
    """Load a single character's stroke record from the MakeMeAHanzi graphics.txt.

    The dataset is a newline-delimited JSON file (~9.5k records). For our
    usage the simplest path is an on-demand scan with an LRU cache; real-time
    performance stays fine because only a handful of characters are requested
    per session, and repeated lookups are cached.
    """
    path = _DEFAULT_GRAPHICS_PATH
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("character") == character:
                    return obj
    except OSError:
        return None
    return None


@lru_cache(maxsize=512)
def _load_trace_geometry(
    character: str,
) -> tuple[tuple[tuple[tuple[int, int], ...], ...], list[list[list[list[int]]]] | None] | None:
    """Return (median_strokes, outlines) for a character, or None if unsupported.

    median_strokes: regularized centerlines (drives stroke-order reveal + the
    legacy skeleton path). outlines: per-stroke 楷体 filled contours in 0..1000,
    or None when only hand-crafted bring-up strokes (no glyph outline) exist.
    Both share one normalization (computed from the outline ink extent) so the
    reveal mask aligns with the filled glyph.
    """
    # Prefer the dataset for best "copybook" fidelity + broad coverage.
    record = _load_graphics_record(character)
    if record:
        medians = record.get("medians")
        outline_paths = record.get("strokes")
        if isinstance(medians, list) and isinstance(outline_paths, list):
            raw_outlines = [_parse_svg_outline(d) for d in outline_paths if isinstance(d, str)]
            ink_points = [p for contours in raw_outlines for c in contours for p in c]
            if ink_points:
                norm = _compute_norm(ink_points)
                normalized = _normalize_medians_with(medians, norm)
                if normalized:
                    median_strokes = tuple(
                        _regularize_stroke(stroke) for stroke in normalized
                    )
                    outlines = _normalize_outlines_with(raw_outlines, norm)
                    return median_strokes, outlines

    # Fallback for a handful of hand-crafted sample strokes / bring-up (no outline).
    strokes = _STROKE_DATA.get(character)
    if strokes is None:
        return None
    median_strokes = tuple(
        _regularize_stroke(tuple((int(x), int(y)) for x, y in stroke)) for stroke in strokes
    )
    return median_strokes, None


def character_trace_payload(
    character: str, *, duration_ms: int = 1800
) -> dict[str, Any] | None:
    if not _SINGLE_HANZI_RE.match(character or ""):
        return None
    geometry = _load_trace_geometry(character)
    if geometry is None:
        return None
    median_strokes, outlines = geometry
    payload: dict[str, Any] = {
        "character": character,
        "duration_ms": int(duration_ms),
        "strokes": copy.deepcopy(
            [[[x, y] for x, y in stroke] for stroke in median_strokes]
        ),
    }
    if outlines:
        payload["outlines"] = copy.deepcopy(outlines)
    return payload
