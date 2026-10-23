"""各意图场景的出厂默认值。

新增一个场景 = 在此定义其默认 `SceneDefinition` 并加入 `BUILTIN_SCENES`，
再在 `web_console` 渲染一张配置卡片即可——主链路（意图识别/状态机/人设注入/
提示音缓存）对场景数量无感。
"""

from voice_robot.scenes.scene import SceneDefinition

# 少儿英语老师人设（默认值，Web 端「场景」页签可改写并恢复默认）。
# 决策：全英文 + 少量关键中文提示（ADR 0008）。
ENGLISH_TUTOR_PERSONA = (
    "You are a warm, patient kids' English tutor guiding a young child through "
    "spoken-English practice. Speak mostly in simple, slow English with short "
    "sentences. When the child seems confused, or for key encouragement and "
    "pronunciation tips, add one short Chinese sentence to help. Ask one easy "
    "question at a time, gently correct pronunciation, praise often, and keep "
    "every reply to 1-2 sentences so it is easy to listen to and repeat."
)

# 英语场景注入 ASR 的英文热词（少儿口语练习高频词），把中英混合模型软性偏置
# 到英文上——不切语言模型、保留双语识别与实时字幕。控制在约 100 tokens 内
# （火山双向流式 context.hotwords 上限）。Web 端「场景」页签可改写并恢复默认。
ENGLISH_TUTOR_ASR_HOTWORDS = (
    "hello", "hi", "good morning", "good afternoon", "goodbye", "bye",
    "yes", "no", "okay", "please", "thank you", "sorry", "welcome",
    "how are you", "I'm fine", "what's your name", "nice to meet you",
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "twenty", "thirty", "hundred",
    "red", "yellow", "blue", "green", "black", "white", "pink", "orange",
    "apple", "banana", "orange", "cat", "dog", "bird", "fish", "rabbit",
    "mom", "dad", "teacher", "friend", "school", "book", "pen", "water",
    "happy", "good", "great", "big", "small", "I like", "let's go",
    "this is", "what is this", "how do you say",
)

ENGLISH_TUTOR = SceneDefinition(
    scene_id="english_tutor",
    name="英语学习",
    enabled=True,
    enter_keywords=(
        "学英语",
        "学习英语",
        "教我英语",
        "教我说英语",
        "练英语",
        "练口语",
        "练习口语",
        "练习英语",
        "用英语聊天",
        "用英语对话",
        "用英文",
        "英文聊天",
        "英文对话",
        "说英语",
        "英语对话",
        "英语练习",
        "english",
        "speak english",
    ),
    exit_keywords=(
        "退出英语",
        "退出场景",
        "退出学习",
        "结束英语",
        "结束练习",
        "不学英语",
        "不练英语",
        "不学了",
        "不练了",
        "换回中文",
        "说中文",
        "讲中文",
        "exit english",
        "stop english",
    ),
    intent_hint="用户想开始练习/学习英语口语，进入英语学习对话场景",
    persona_prompt=ENGLISH_TUTOR_PERSONA,
    enter_prompt_text="Yay! Let's practice English together! 我们开始英语练习啦～",
    exit_prompt_text="Great job today! 英语练习结束，我们换回中文聊天吧。",
    asr_hotwords=ENGLISH_TUTOR_ASR_HOTWORDS,
)


# —— 动作型场景（kind="action"）：触发词 Web 可配，命中触发一次性动作，屏幕双击退出 ——

# 汉字书写：触发词默认 = 现有规范触发短语（取字仍走 character_trace.extract_trace_character，
# 这些词只作启用门控 + 可配扩展触发，编辑后可加自定义触发语）。
HANZI_WRITING = SceneDefinition(
    scene_id="hanzi_writing",
    name="汉字书写",
    enabled=True,
    enter_keywords=(
        "怎么写",
        "怎么写的",
        "这个字",
        "写一下",
        "教我写",
        "帮我写",
        "字怎么写",
    ),
    exit_keywords=(),
    intent_hint="用户想看某个汉字怎么写（描红演示）",
    persona_prompt="",
    enter_prompt_text="",
    exit_prompt_text="",
    kind="action",
)

# 成语学习：泛意图触发词默认。问具体成语（「X是什么意思」）走 extract_idiom_query，
# 不依赖这些词。
IDIOM_LEARNING = SceneDefinition(
    scene_id="idiom_learning",
    name="成语学习",
    enabled=True,
    enter_keywords=(
        "学成语",
        "成语学习",
        "讲成语",
        "讲个成语",
        "说个成语",
        "听个成语",
        "成语故事",
        "来个成语",
        "来一个成语",
        "教我成语",
    ),
    exit_keywords=(),
    intent_hint="用户想学成语：问具体成语的意思，或泛泛地想听一个成语",
    persona_prompt="",
    enter_prompt_text="",
    exit_prompt_text="",
    kind="action",
)


BUILTIN_SCENES: tuple[SceneDefinition, ...] = (
    ENGLISH_TUTOR,
    HANZI_WRITING,
    IDIOM_LEARNING,
)


def builtin_scene(scene_id: str) -> SceneDefinition | None:
    for scene in BUILTIN_SCENES:
        if scene.scene_id == scene_id:
            return scene
    return None
