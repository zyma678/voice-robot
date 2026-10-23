"""成语查询意图抽取（纯函数，便于单测）。

从一句话里抽出用户想问的成语，如「夜郎自大是什么意思？」→「夜郎自大」。
只负责"抽候选"；候选是否真成语、要不要新增写库、命中缓存与否，由调用方
（server）按「优先库内匹配，库外仅四字才新增」的策略裁决。
"""

from __future__ import annotations

import re

_HANZI = "一-鿿"
_HANZI_RUN_RE = re.compile(f"[{_HANZI}]{{2,8}}$")
_PUNCTUATION = " 。，,.!！?？、；;：:~～…—-（）()【】[]\"'“”‘’`"
# 句末语气词：先剥掉再做后缀匹配，否则「画蛇添足是什么意思呀」会因结尾是汉字而漏判。
_TRAILING_PARTICLES = "呀啊呢吗吧嘛哦噢喔啦耶呐"

# 查询后缀（按长度从长到短匹配，先长后短避免「什么意思」吃掉「是什么意思」）。
_QUERY_SUFFIXES = (
    "是什么意思",
    "是啥意思",
    "什么意思",
    "啥意思",
    "的意思是什么",
    "的意思",
    "是什么成语",
    "这个成语",
    "是什么典故",
    "什么典故",
    "的典故",
    "的故事",
    "什么故事",
    "怎么讲",
    "怎么理解",
    "是成语吗",
)

# 「成语X」「讲讲X」这类前缀触发（后跟成语）。
_QUERY_PREFIXES = (
    "成语",
    "讲讲",
    "讲一下",
    "解释一下",
    "解释",
)


def _normalize(text: str) -> str:
    compact = "".join(str(text or "").split())
    for quote in ('"', "'", "“", "”", "‘", "’"):
        compact = compact.replace(quote, "")
    compact = compact.strip(_PUNCTUATION)
    return compact.rstrip(_TRAILING_PARTICLES)


def _trailing_idiom(fragment: str) -> str | None:
    candidate = fragment.strip(_PUNCTUATION)
    # 去掉成语前的「这个」等贴边修饰不需要——直接取结尾的 2~8 连续汉字。
    match = _HANZI_RUN_RE.search(candidate)
    if match:
        return match.group(0)
    return None


def extract_idiom_query(text: str) -> str | None:
    """抽出用户问的成语候选（2~8 连续汉字）；抽不到返回 None。"""
    normalized = _normalize(text)
    if not normalized:
        return None

    for suffix in _QUERY_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            candidate = _trailing_idiom(normalized[: -len(suffix)])
            if candidate:
                return candidate

    for prefix in _QUERY_PREFIXES:
        if normalized.startswith(prefix) and len(normalized) > len(prefix):
            rest = normalized[len(prefix) :].strip(_PUNCTUATION)
            # 前缀型：取开头的 2~8 连续汉字。
            match = re.match(f"[{_HANZI}]{{2,8}}", rest)
            if match:
                return match.group(0)

    return None
