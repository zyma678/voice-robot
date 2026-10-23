from voice_robot.idiom_intent import extract_idiom_query


def test_extract_idiom_from_meaning_questions() -> None:
    assert extract_idiom_query("夜郎自大是什么意思？") == "夜郎自大"
    assert extract_idiom_query("画蛇添足什么意思") == "画蛇添足"
    assert extract_idiom_query("守株待兔的意思") == "守株待兔"
    assert extract_idiom_query("刻舟求剑怎么讲") == "刻舟求剑"


def test_extract_idiom_from_prefix_forms() -> None:
    assert extract_idiom_query("成语刻舟求剑") == "刻舟求剑"
    assert extract_idiom_query("解释一下夜郎自大") == "夜郎自大"


def test_extract_idiom_supports_non_four_char_candidates() -> None:
    # 抽取本身不限四字（四字门槛是 server 新增写库时才施加）。
    assert extract_idiom_query("五十步笑百步是什么意思") == "五十步笑百步"


def test_extract_idiom_tolerates_trailing_particles() -> None:
    # 句末语气词不应让后缀匹配失效（ASR 常把「呀/啊/呢」一并识别进来）。
    assert extract_idiom_query("画蛇添足是什么意思呀") == "画蛇添足"
    assert extract_idiom_query("守株待兔什么意思啊") == "守株待兔"
    assert extract_idiom_query("夜郎自大是啥意思呢") == "夜郎自大"


def test_extract_returns_none_without_query_intent() -> None:
    assert extract_idiom_query("你好呀") is None
    assert extract_idiom_query("今天天气真好") is None
    assert extract_idiom_query("") is None
