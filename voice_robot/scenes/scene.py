from dataclasses import dataclass


@dataclass(frozen=True)
class SceneDefinition:
    """一个意图场景的生效定义（出厂默认 + 运行态覆盖合并后的结果）。

    `enter_keywords` / `exit_keywords` 是关键词层触发词（子串匹配，大小写不敏感）；
    `intent_hint` 仅在关键词未命中、走 LLM 兜底分类时使用；`persona_prompt` 在该
    场景激活的轮次追加到 system prompt；`enter_prompt_text` / `exit_prompt_text`
    是进入/退出的固定语音提示文案（服务端持久化缓存其 TTS 音频）。

    `asr_hotwords` 是该场景激活时注入 ASR 的英文/领域热词（火山双向流式
    `request.corpus.context` 的 `hotwords`，上限约 100 tokens），把识别软性
    偏置到该场景常用词上（如英语场景偏置常见英文单词），不切换语言模型、保留
    中英混合识别与实时字幕。空 = 不注入，按默认中英混合模型识别。

    `kind` 区分场景运行模型：
    - `"persona"`（如英语学习）：持久态——进入后 `current_scene` 记忆、`persona_prompt`
      叠加、`asr_hotwords` 注入、语音 `exit_keywords` 退出、进出下发 `scene_changed`
      （驱动固件切「经典脸」）。
    - `"action"`（如汉字书写、成语学习）：无状态——只用 `enter_keywords` 触发一次性
      动作（描红 / 随机或指定成语播报），命中即在当轮收尾；不写 `current_scene`、
      不发 `scene_changed`、不用 `persona_prompt`/`exit_keywords`/`asr_hotwords`；
      退出由固件 overlay 屏幕双击关闭（已存在）。
    """

    scene_id: str
    name: str
    enabled: bool
    enter_keywords: tuple[str, ...]
    exit_keywords: tuple[str, ...]
    intent_hint: str
    persona_prompt: str
    enter_prompt_text: str
    exit_prompt_text: str
    asr_hotwords: tuple[str, ...] = ()
    kind: str = "persona"
