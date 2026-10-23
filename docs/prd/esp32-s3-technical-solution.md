# ESP32\-S3 技术方案

**最后更新：2026-07-12**

**方案概要：** 基于微雪 ESP32\-S3 1\.83寸触摸液晶开发板（CZ154），构建 AI 语音交互机器人。端侧负责语音采集、唤醒检测与音频播放；云端通过 WebSocket 实现 ASR → LLM Agent → TTS 全链路低延迟交互。目标：端到端响应延迟 \< 2s，支持离线唤醒 \+ 在线对话。

**开发策略：** Web 版优先 → 验证 Agent 效果 → 迁移硬件。云端服务和 WebSocket 协议层完全复用。

esp32-s3官方开发文档:https://www.waveshare.net/wiki/ESP32-S3-Touch-LCD-1.83

# 参考资料
## demo
- https://github.com/waveshareteam/ESP32-S3-Touch-LCD-1.83
- https://github.com/SaltyFishPro/ESP32-S3-player

## audio demo
- https://github.com/mkalinec/esp32-s3-audio-player
- https://github.com/dude84/esphome-audio-sentinel
- https://github.com/CelliesProjects/ESP32_VS1053_Stream


# 一、项目目标与核心指标

|目标维度|具体指标|
|---|---|
|交互体验|端到端语音响应延迟 \< 2s（WiFi 环境下）|
|内容安全|仅回答学习/科普/故事/口算类问题，拒绝不良内容|
|使用门槛|语音唤醒 \+ 一键对话，儿童可独立操作|
|续航能力|板载锂电池支持连续对话 \> 2小时|
|成本控制|硬件 BOM \< 200 元，云端月费 \< 55 元|

---

# 二、系统架构

## 2\.1 端\-云分离架构

端侧专注音频 I/O 与低功耗唤醒，云端承担 AI 推理全链路。通过 WebSocket 长连接实现双向实时音频流传输。

**核心数据流：**

1. **唤醒** → 端侧离线唤醒词检测（"你好小智"），无需联网

2. **录音上传** → Opus 编码 → WebSocket 发送至云端

3. **AI 处理** → ASR 转文字 → LLM Agent 生成回复 → TTS 合成语音

4. **流式回传** → TTS 音频流实时推送至端侧播放

## 2\.2 技术选型总览

|模块|选型|说明|
|---|---|---|
|硬件主控|ESP32\-S3 \(微雪 CZ154\)|双核240MHz、WiFi\+BLE、1\.83寸触摸屏、双麦\+喇叭|
|Web 前端|React \+ TypeScript|VAD 自动检测 \(@ricky0123/vad\-web\)、Opus 编码|
|后端服务|Python FastAPI|单机服务，WebSocket 端点|
|LLM|Doubao\-Seed\-2\.0\-mini|火山引擎方舟平台，¥0\.2/百万token\(入\)|
|ASR|豆包流式语音识别 2\.0|首包延迟 \~200ms，¥1/小时|
|TTS|豆包语音合成 2\.0|儿童友好音色，流式输出，¥5/万字符|
|Agent 框架|LangGraph|有状态图执行引擎，适合多轮对话|
|记忆存储|文件系统 \(JSON/Markdown\)|零依赖、人类可读、Git 可追踪|

---

# 三、硬件方案

## 3\.1 核心硬件

|模块|选型|关键参数|
|---|---|---|
|主控芯片|ESP32\-S3\-WROOM\-1|双核 240MHz、512KB SRAM、WiFi\+BLE 5\.0|
|开发板|微雪 CZ154|1\.83寸 LCD 触摸屏、板载双麦\+喇叭、锂电池管理|
|麦克风|板载 MEMS 双麦|I2S 接口、支持 AEC|
|扬声器|板载 1W 喇叭|I2S DAC、16kHz/16bit|
|显示屏|1\.83寸 TFT LCD|240×280、SPI、电容触摸|

## 3\.2 引脚分配

> 2026-06-07 校正：以下引脚以微雪 ESP32-S3-Touch-LCD-1.83 官方 BSP `waveshare/esp32_s3_touch_lcd_1_83` 为准。早期草案中的 I2S/LCD/触摸 GPIO 不再作为实现依据。

|外设|信号|GPIO|说明|
|---|---|---|---|
|I2C 总线|SDA / SCL|GPIO15 / GPIO14|触摸、音频 codec、PMU 等共享 I2C|
|I2S 音频|MCLK / BCLK / WS|GPIO16 / GPIO9 / GPIO45|ES8311/ES7210，16kHz/16bit/mono|
|I2S 播放|DOUT|GPIO8|ESP32 → ES8311|
|I2S 录音|DIN|GPIO10|ES7210/codec → ESP32|
|功放|PA_EN|GPIO46|高电平有效|
|SPI LCD|SCLK / MOSI / CS / DC|GPIO6 / GPIO7 / GPIO5 / GPIO4|ST7789(P)，SPI2，RGB565|
|LCD 控制|RST / BL|GPIO38 / GPIO40|RST 低有效，背光走 LEDC PWM|
|触摸|TP_INT / TP_RST|GPIO13 / GPIO39|CST816(S)，I2C|
|按键|BOOT|GPIO0|备用 PTT/重连/设置入口|

重要约束：1.83 寸板官方 BSP 没有 TCA9554/IO 扩展器，LCD_RST、LCD_BL、TP_RST、PA_EN 都是 ESP32-S3 直连 GPIO。

---

# 四、通信协议 \(WebSocket\)

Web 版与硬件版共用同一协议，仅端侧音频 I/O 实现不同。

|方向|消息类型|格式|说明|
|---|---|---|---|
|端→云|auth|JSON|设备认证：device\_id \+ token|
|端→云|audio\_start|JSON|开始录音通知|
|端→云|audio\_data|Binary|Opus 编码音频帧（20ms/帧）|
|端→云|audio\_end|JSON|录音结束通知|
|云→端|tts\_start|JSON|即将推送语音回复|
|云→端|tts\_data|Binary|TTS 音频流（Opus 编码）|
|云→端|tts\_end|JSON|语音回复结束|
|云→端|text|JSON|文字字幕（LCD 展示）|
|双向|heartbeat|JSON|心跳保活，30s 间隔|

---

# 五、AI Agent 设计 \(LangGraph\)

## 5\.1 状态图

Agent 采用 LangGraph StateGraph，包含 7 个节点和条件路由：

```Plain Text
用户输入 → 记忆检索 → 组装Prompt → 调用LLM → 安全检查 → 回复/工具调用
                                                          ↓
                                              提取记忆 → 保存记忆 → 返回回复

```

安全检查后三路分支：需要工具调用 → 执行工具 → 回到 LLM；直接回复 → 提取记忆；不安全 → 终止。

## 5\.2 State 定义

```Python
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]  # 对话消息
    user_id: str
    user_profile: dict           # 用户画像
    retrieved_memories: list     # 检索到的相关记忆
    new_memories: list           # 需要保存的新记忆
    needs_tool: bool             # 是否需要工具调用
    safety_passed: bool          # 内容安全是否通过
    response_text: str           # 最终回复文本（供 TTS）

```

## 5\.3 Graph 构建

```Python
from langgraph.graph import StateGraph, END

def build_agent_graph():
    graph = StateGraph(AgentState)

    graph.add_node("retrieve_memory", retrieve_memory_node)
    graph.add_node("build_prompt", build_prompt_node)
    graph.add_node("call_llm", call_llm_node)
    graph.add_node("safety_check", safety_check_node)
    graph.add_node("tool_execute", tool_execute_node)
    graph.add_node("extract_memory", extract_memory_node)
    graph.add_node("save_memory", save_memory_node)

    graph.set_entry_point("retrieve_memory")
    graph.add_edge("retrieve_memory", "build_prompt")
    graph.add_edge("build_prompt", "call_llm")
    graph.add_edge("call_llm", "safety_check")

    graph.add_conditional_edges("safety_check", route_after_safety, {
        "tool_call": "tool_execute",
        "respond": "extract_memory",
        "blocked": END,
    })

    graph.add_edge("tool_execute", "call_llm")
    graph.add_edge("extract_memory", "save_memory")
    graph.add_edge("save_memory", END)

    return graph.compile()

def route_after_safety(state):
    if not state["safety_passed"]: return "blocked"
    if state["needs_tool"]: return "tool_call"
    return "respond"

```

## 5\.4 System Prompt

```Plain Text
你是小学生专属语音玩伴"小智"，温柔、简短、童趣。

【关于这个孩子】
- 名字：{name} / 年级：{grade} / 兴趣：{interests}

【你记得的事情】
- {相关记忆 Top 3}

【回复规则】
- 每次回复不超过3句话，总字数不超过50字
- 用词简单，适合小学生理解
- 多鼓励，少批评
- 拒绝不适合儿童的话题，温和引导转移

```

## 5\.5 工具定义

```Python
@tool
def get_current_time() -> str:
    """获取当前时间"""

@tool
def generate_math_problem(difficulty: str = "easy") -> str:
    """生成口算题。difficulty: easy/medium/hard"""

@tool
def check_math_answer(problem: str, answer: int) -> str:
    """检查口算答案"""

@tool
def tell_story(topic: str = "random") -> str:
    """讲一个简短故事"""

```

---

# 六、记忆系统（文件系统方案）

## 6\.1 三层记忆模型

|层级|存储内容|生命周期|存储方式|
|---|---|---|---|
|短期记忆|当前会话上下文|单次会话|内存（列表）|
|长期记忆|重要事件和知识点|永久|Markdown 文件|
|用户画像|稳定的偏好和属性|永久（可更新）|JSON 文件|

## 6\.2 目录结构

```Plain Text
memory/
├── profiles/                    # 用户画像
│   └── xiaoming.json
├── conversations/               # 对话历史（按日期归档）
│   └── xiaoming/2026-06-01/09-15-30.json
├── memories/                    # 长期记忆（核心）
│   └── xiaoming/
│       ├── index.json           # 记忆索引（摘要+元数据）
│       ├── learning/001_乘法口诀薄弱.md
│       ├── preference/001_喜欢恐龙.md
│       ├── event/001_周三语文考试.md
│       └── knowledge/001_家里有猫叫咪咪.md
└── embeddings/                  # 向量缓存（可选）

```

## 6\.3 记忆文件格式

**用户画像** \(`profiles/xiaoming.json`\)：

```JSON
{
  "user_id": "xiaoming",
  "name": "小明",
  "age": 9,
  "grade": "三年级",
  "interests": ["恐龙", "奥特曼", "乐高"],
  "strengths": ["语文阅读", "英语单词"],
  "weaknesses": ["乘法口诀", "应用题"],
  "personality": "活泼好动，注意力容易分散"
}

```

**长期记忆** \(`memories/xiaoming/learning/001_乘法口诀薄弱.md`\)：

```Markdown
---
id: "mem_001"
category: "learning"
importance: 0.8
tags: ["数学", "乘法", "口诀"]
status: "resolved"
---
# 乘法口诀薄弱（7×8）
- 2026-06-01：7×8 回答为 54（错误）
- 2026-06-02：回答正确（56），标记为已掌握 ✓

```

## 6\.4 检索机制

多维度评分：关键词匹配\(0\.3\) \+ 重要度加权\(0\.2\) \+ 时间衰减\(0\.2\) \+ 过期降权 \+ 已解决降权，取 Top 3 注入 Prompt。

```Python
class FileMemoryRetriever:
    def search(self, query: str, user_id: str, top_k: int = 3) -> list:
        index = load_index(user_id)
        keywords = extract_keywords(query)
        scored = [(score_entry(entry, keywords), entry) for entry in index]
        return load_top_k(scored, top_k)

    def _score_entry(self, entry, keywords):
        score = keyword_match(entry, keywords) * 0.3
        score += entry["importance"] * 0.2
        score += time_decay(entry["last_accessed"]) * 0.2
        if expired(entry): score *= 0.1
        if entry["status"] == "resolved": score *= 0.5
        return score

```

## 6\.5 记忆提取策略

每轮对话结束后，通过 LLM 判断是否有值得记住的信息（learning/preference/event/knowledge 四类）。规则预判：对话轮次 \< 3 或内容全是闲聊时跳过提取，节省 API 调用。

## 6\.6 记忆应用场景

|场景|效果|
|---|---|
|口算 7×8 答错了|下次主动出 7×8："上次这道题难住你了，再试试？"|
|说"我喜欢恐龙"|讲故事融入恐龙元素，科普推荐古生物话题|
|说"明天要考语文"|今天主动说"要不要复习一下古诗？"|

---

# 七、服务端实现

## 7\.1 项目结构

```Plain Text
voice-robot/
├── server.py          # FastAPI 主入口 + WebSocket
├── graph.py           # LangGraph 状态图定义
├── state.py           # Agent 状态模型
├── nodes.py           # 各节点实现
├── tools.py           # Agent 可用工具
├── memory/
│   ├── file_store.py  # 记忆写入
│   └── file_retriever.py  # 记忆检索
├── services/
│   ├── asr.py         # 火山 ASR 调用
│   └── tts.py         # 火山 TTS 调用
├── config.py          # 配置
└── requirements.txt

```

## 7\.2 WebSocket 服务入口

```Python
from fastapi import FastAPI, WebSocket
from graph import agent
from langchain_core.messages import HumanMessage

app = FastAPI()

@app.websocket("/ws")
async def voice_chat(ws: WebSocket):
    await ws.accept()
    user_id = "xiaoming"

    while True:
        audio_data = await ws.receive_bytes()
        user_text = await transcribe_audio(audio_data)
        await ws.send_json({"type": "asr_result", "text": user_text})

        result = await agent.ainvoke({
            "messages": [HumanMessage(content=user_text)],
            "user_id": user_id,
            "user_profile": {},
            "retrieved_memories": [],
            "new_memories": [],
            "needs_tool": False,
            "safety_passed": True,
            "response_text": "",
        })

        reply_text = result["response_text"]
        await ws.send_json({"type": "llm_reply", "text": reply_text})

        async for audio_chunk in synthesize_speech(reply_text):
            await ws.send_bytes(audio_chunk)
        await ws.send_json({"type": "tts_end"})

```

## 7\.3 依赖与启动

```Plain Text
langgraph>=0.2.0
langchain-core>=0.3.0
langchain-openai>=0.2.0
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
websockets>=12.0

```

```Bash
pip install -r requirements.txt
export DEEPSEEK_API_KEY="sk-xxx"
export VOLC_ASR_APPID="xxx"
export VOLC_TTS_APPID="xxx"
uvicorn server:app --host 0.0.0.0 --port 8080 --reload

```

---

# 八、火山引擎成本估算

> **使用假设：** 每周100次对话 → 每月400次会话 × 5轮，每轮用户说20字/3\.5s、Agent回复50字

|环节|月用量|单价|月费用|
|---|---|---|---|
|ASR|\~1\.94小时|¥1/小时|\~¥2|
|LLM|\~350万token|入¥0\.2\+出¥2/百万|\~¥1\.5|
|TTS|\~10万字符|¥5/万字符|\~¥50|
|**合计**|—|—|**\~¥53/月**|

**降本策略：** TTS 缓存高频话术 \+ 限制回复≤30字，优化后可降至 **¥22\-29/月**。

**接入配置：**

```Python
LLM_CONFIG = {
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "model": "doubao-seed-2.0-mini",
    "temperature": 0.7,
    "max_tokens": 150,
}
ASR_CONFIG = {
    "cluster": "volcengine_streaming_common",
    "format": "opus",
    "sample_rate": 16000,
}
TTS_CONFIG = {
    "voice_type": "zh_female_child_cute",
    "encoding": "opus",
    "speed_ratio": 0.9,
    "pitch_ratio": 1.1,
}

```

---

# 九、开发排期

|阶段|目标|交付物|周期|
|---|---|---|---|
|W1|跑通最小闭环|FastAPI \+ ASR \+ LLM \+ TTS 串联|2天|
|W2|Web UI 上线|React 对话界面 \+ 录音 \+ 播放|3天|
|W3|体验优化|流式 TTS、VAD 断句、记忆系统|3天|
|W4|对接硬件|ESP32\-S3 固件适配同一协议|2天|

---

# 十、风险与应对

|等级|风险项|应对措施|
|---|---|---|
|高|网络不稳定|离线兜底话术 \+ 自动重连 \+ 音频预缓冲|
|高|LLM 输出不安全内容|Prompt 约束 \+ 关键词过滤 \+ 内容安全网关|
|中|ESP32\-S3 内存不足|音频分帧处理、PSRAM 外扩|
|中|唤醒词误唤醒率高|ESP\-SR 阈值调优 \+ 二次确认|
|低|锂电池续航不足|深度睡眠 \+ 动态降频|

> (注：内容由 AI 生成，请谨慎参考）

---

# 十一、实施补充（2026-06-02 对话增量需求）

以下内容为本轮开发过程中新增并确认的需求，作为后续实施基线：

## 11.1 Web 管理与调试页（已纳入范围）

1. 必须提供 Web 端管理与调试页面。
2. 前端代码目录必须与后端代码目录分开管理（当前采用 `web_console/` 与 `voice_robot/` 分离）。
3. 管理页面需支持运行态状态查看、记忆查看、WebSocket 调试。

## 11.2 LLM 配置能力（已纳入范围）

1. Web 端暴露 API key 录入入口。
2. API key 交互必须支持四种状态：
- 编辑态
- 非编辑态
- 隐藏
- 显示
3. API key 必须接入真实 Doubao/方舟调用链路，不是仅前端展示。
4. 运行态配置项扩展为：
- `model`
- `base_url`
- `api_key`
- `system_prompt`
- `user_prompt_template`
5. 以上配置需支持读取、编辑、保存并即时生效。

## 11.3 文本交互能力（已纳入范围）

1. Web 页面新增文本输入框，支持直接与 agent 对话。
2. 需提供对应后端文本对话接口。
3. 对话结果需可展示回复来源（如真实大模型或本地兜底），便于调试。

## 11.4 需求来源与文档治理约束（从本条开始执行）

1. 后续实现任务不再读取飞书文档作为执行输入。
2. 需求原始信息统一从 `docs/prd/` 获取。
3. 对话中新出现的需求，必须先同步更新到 `docs/prd/`，再进入实现。
4. 当前工作进展与剩余工作统一记录在：`docs/progress/WORK-IN-PROGRESS.md`。

## 11.5 流式文字输出（新增）

1. Agent 文字回复需要改为流式输出，前端应能边接收边展示。
2. WebSocket 语音链路需支持流式文字事件：
- `llm_reply_start`
- `llm_reply_delta`
- `llm_reply_end`
3. Web 文本对话需提供流式接口，便于页面直接展示增量文本。
4. 保留必要的回复来源与 fallback 详情，方便排查真实大模型链路与本地兜底链路。

## 11.6 Web 控制台左侧 Tab + 主工作区布局（新增）

1. Web 页面改为左侧导航 Tab、右侧主工作区布局。
2. 左侧 Tab 至少覆盖：
- 运行态总览
- 文本交流
- 实时调试
- 记忆管理
- 模型配置
3. 右侧主工作区只展示当前 Tab 对应内容，减少页面纵向堆叠。
4. 页面需保持移动端可用，窄屏下导航和主工作区可自然堆叠。

## 11.7 记忆系统完善（新增）

1. 所有聊天内容必须按用户维度持久化存储在文件系统中。
2. 短期记忆以最近 20 轮对话为窗口，将聊天记录拼接到当前上下文中，窗口随聊天轮次滚动。
3. 长期记忆来源于短期窗口外的信息，每隔 20 轮对窗口外对话做一次压缩，生成摘要信息。
4. 长期摘要需要拼接到当前上下文中，与短期窗口共同参与 Agent 回复生成。
5. 长期摘要优先使用 Doubao/方舟生成，失败时 fallback 到本地确定性摘要。
6. 第 40 轮结束后压缩第 1-20 轮，始终保留最近 20 轮完整对话作为短期窗口。
7. 主聊天记录与调试审计事件分层存储：主聊天记录进入上下文，调试审计默认只用于排查。
8. 结构化记忆需要根据用户聊天内容分析偏好，生成并持续更新用户画像。
9. Web 端需要提供记忆管理工具，支持查看、修改、删除用户画像、结构化记忆、原始聊天轮次、长期摘要和审计日志。
10. 先完成设计文档，再进入开发实现。

#### 开发落地状态（2026-06-03）

- 已实现按用户维度的主聊天 JSONL 持久化（`conversations/<user_id>/turns.jsonl` + `meta.json`）。
- 已实现最近 20 轮短期记忆窗口，并拼接到 LLM `memory_block`（`MemoryContextBuilder`）。
- 已实现第 40 轮后压缩 1-20 轮、之后每 20 轮滚动压缩的长期摘要机制（`summaries/<user_id>/summary_000001_000020.md` + `index.json`）。
- 已实现基于聊天内容的画像抽取与结构化记忆写入，调用失败时使用本地规则兜底。
- 已实现 Web 端用户画像、对话轮次、长期摘要、结构化记忆、审计日志的查看、编辑与删除。
- 已确认审计日志不默认进入 Agent 上下文，仅用于排查。

## 11.8 Token 调用统计（新增）

1. 增加 Token 调用统计模块，统计 LLM 请求的 token 用量与调用次数。
2. Web 页面支持查看总量（prompt/completion/total tokens、calls）。
3. Web 页面支持按以下维度拆分查看：
- 用户（user_id）
- 使用场景（例如：文本对话 / 文本流式 / 语音 WebSocket）
- 模型厂商（例如：方舟 / 本地兜底 / 其他）
4. Web 页面支持基于 token 用量的预估花费展示，并支持输入预算后展示剩余额度（以“元”为单位）。

## 11.9 语音交互模块（可打断）（新增）

1. Web 端优先验证可打断的语音对话体验，后续快速同步到硬件端。
2. Web/硬件端共用同一套 `/ws` 协议；端侧上行 Opus(16k/mono)，下行先用 PCM(16k/mono) 分片。
3. ASR/TTS 使用火山独立语音能力；主链路拆分为独立 ASR -> Agent/LLM/记忆 -> 独立 TTS，确保语音识别文本会进入现有大模型与记忆系统。已实现的火山 RealtimeAPI 端到端适配仅作为备用/参考，不再作为 `/ws` 主路径。
4. Web 控制台支持填写语音 API key（同 LLM 的编辑/隐藏/显示状态），保存到后端运行态配置。
5. 打断验收：VAD start（或新文本输入）触发 `barge_in` 后，200ms 内止播（以 `tts_end(reason=barge_in)` 为准）。
6. 独立 ASR/TTS 的真实火山接口以独立接入文档为准；当前先保持 Web/硬件 `/ws` 端侧协议稳定。
7. 详细设计见：`docs/design/specs/voice-interaction-module-design.md`。

## 11.10 硬件端 LCD/触摸 UI（新增）

1. ESP32-S3 硬件端必须提供本机 UI 界面，1.83 寸 LCD + 触摸屏作为主操作入口，BOOT 键只作为备用入口。
2. 用户不应依赖串口日志判断设备状态；启动、联网、云端鉴权、在线待机、录音、思考、播放、打断、错误都需要在屏幕上有明确反馈。
3. 待机页需要提供大号触摸按钮，支持点按开始/停止录音、长按按住说话、播放中点按打断。
4. 播放页需要展示 Bot 回复字幕和播放状态；收到 `llm_reply_delta` 时字幕可增量更新，收到 `tts_end` 后回到待机或展示错误/打断提示。
5. 错误页或 Toast 需要展示 Wi-Fi、WebSocket、鉴权、ASR、TTS、音频设备等错误摘要，并支持触摸重试。
6. 设置/诊断页至少展示 server url、token 配置状态、device_id、固件版本、音量、VAD 阈值、屏幕亮度和最近错误；复杂配置仍优先在 Web 控制台完成。
7. 固件实现优先复用官方 BSP `waveshare/esp32_s3_touch_lcd_1_83` 和 LVGL；LCD/触摸/UI 代码放在独立模块中，不混入后端 `voice_robot/` 或 Web `web_console/`。
8. UI 状态以网络、WebSocket、ASR/LLM/TTS、音频播放任务投递的事件为准，UI 任务只做状态迁移和渲染，不阻塞音频采集/播放。

## 11.11 Web 硬件音量控制（新增）

1. Web 控制台的硬件设备控制区需要提供音量控制按钮，选择在线硬件设备后可调整该设备播放音量。
2. 音量范围为 `0-100`，Web 端提供滑杆、当前数值展示和静音/恢复入口；无在线硬件时控件不可用或提示不可用。
3. 后端新增设备音量控制接口，通过现有硬件 WebSocket 命令通道下发 `set_volume` 命令，不影响文本对话、语音对话和设备监看链路。
4. ESP32-S3 固件收到 `set_volume` 后应调用 ES8311 DAC 音量寄存器设置，并在串口日志/UI 状态中反馈当前音量；非法值需要裁剪到 `0-100`。
5. 默认音量使用最小值 `0`，避免烧录或重启后突然外放；联真机调试时通过 Web 控制台手动调高。
6. 音量控制用于联真机调试阶段快速确认扬声器输出，后续可同步到硬件本机设置/诊断页。
9. 详细实施计划见：`docs/design/plans/2026-06-06-esp32-firmware-plan.md`。

## 11.12 Wi-Fi 优先 + BLE 手机网关兜底（新增）

1. ESP32-S3 需要同时支持 Wi-Fi 与手机蓝牙通信能力，长期使用形态为：有 Wi-Fi 时优先通过 Wi-Fi 直连后端；Wi-Fi 不可用时，通过手机 BLE 网关转发到后端。
2. Wi-Fi 成功时沿用当前硬件直连 `/ws` 协议，设备直接向后端认证并保持长连接，传输标记为 `transport=wifi_direct`。
3. Wi-Fi 连接失败或运行中断线超过重试阈值时，设备不得直接退出固件主流程；应启动 BLE 广播并进入蓝牙兜底等待状态，屏幕显示“等待手机连接/蓝牙网关”。
4. 手机侧作为 BLE Central 与云端 WebSocket Gateway：
- 手机连接 ESP32-S3 BLE Peripheral；
- 手机再连接后端 `/ws`；
- 手机向后端认证时使用真实硬件 `device_id`，并附带 `transport=ble_mobile_gateway` 与 `gateway_id`；
- 后端设备列表需要展示设备当前通道与网关标识，便于调试。
5. 第一阶段先实现 BLE 控制/状态兜底，不承载完整语音音频：
- ESP32-S3 广播固定服务，支持控制特征写入；
- 手机或调试工具可通过 BLE 下发 `set_volume` 等轻量控制命令；
- ESP32-S3 通过通知特征上报 Wi-Fi 失败、BLE 已连接、音量已设置等状态；
- 后端先完成网关连接元数据识别，保持 `/ws` 主协议稳定。
6. 为了在没有原生手机 App 的阶段先完成联调，Web 管理控制台需要提供“手机网关”调试页作为浏览器原型：
- 浏览器通过 Web Bluetooth 作为 BLE Central 连接 `VR-<device_id>`；
- 浏览器再通过 `/ws` 向后端认证，使用真实硬件 `device_id`，并附带 `transport=ble_mobile_gateway` 与 `gateway_id`；
- 调试页至少展示 BLE 连接状态、云端连接状态、最近一次 BLE status notify、网关日志；
- 调试页需要支持手动发送 `set_volume` 到 BLE control characteristic；
- 当后端向该 `device_id` 下发 `set_volume` 命令时，浏览器网关需要自动转写为 BLE 控制命令发给硬件；
- 当后端向该 `device_id` 下发 `say` 命令时，第一阶段只记录日志，不在浏览器原型里承载完整语音链路。
7. 第二阶段再实现 BLE 音频链路：
- 手机采集/转发音频，或 ESP32-S3 分片经 BLE 发给手机；
- 手机网关复用后端 `audio_start` / binary audio / `audio_end` / TTS 下行协议；
- 需要单独评估 BLE MTU、带宽、丢包重传、端到端延迟和可打断播放策略。
8. 安全边界：
- BLE 控制命令仅允许有限白名单，第一阶段至少包括 `set_volume`；
- 后端仍以 `/ws` token 鉴权为准，BLE 网关不得绕过云端鉴权；
- `gateway_id` 只用于调试和链路观测，不作为信任凭据。

## 11.13 硬件持续对话模式（2026-06-10 新增）

1. 硬件端语音交互形态为**对话模式开关**，不是单次按键说一句：
- 待机页点按大按钮 → 进入对话模式，设备可主动语音引导（如"我在听，请说"）；
- 对话模式内持续循环：聆听（VAD 自动断句）→ 上行音频 → ASR→LLM→TTS → 播放回复 → 回到聆听；
- 再次点按大按钮 → 退出对话模式，停止采集与播放，回待机。
2. 复用现有 `/ws` 协议与后端链路：上行 `audio_start`/二进制帧/`audio_end`，下行 `asr_partial`/`asr_result`/`llm_reply_*`/`tts_start`/二进制 PCM/`tts_end`；语音识别文本进入现有 Agent 与记忆系统，**不另建会话通道**。固件需显示 `asr_partial` 实时字幕，空识别或可恢复错误需提示用户重试并自动恢复聆听。
3. 回声与打断策略（MVP 取舍，已确认）：
- 第一阶段**半双工**：TTS 播放期间暂停麦克风上行（不做 AEC），播放结束自动恢复聆听；
- 全双工 barge_in（播放中检测人声立即打断）作为后续增强，依赖 ES7210 AEC 评估。
4. 性能与流畅性指标：
- 上行音频帧 20ms/帧，采集→发送端到端缓冲 ≤ 100ms，不得因 UI 渲染丢帧；
- VAD 断句尾静音判定 ≤ 800ms，从"说完"到 `audio_end` 发出 ≤ 1s；
- 播放侧维持现有环形缓冲策略，杜绝 underflow 卡顿；
- 上行第一阶段用 PCM s16le 16k/mono（≈32KB/s，Wi-Fi 可承受）；第二阶段切 Opus 16kbps（≈2KB/s）以适配弱网与 BLE 网关音频链路。
- 设备仍按 20ms 发送，服务端向双向流式 ASR 合并为 200ms PCM 包；不得以 20ms 碎包直接压缩并转发上游。VAD 默认尾静音 320ms，兼顾自然停顿完整性与响应速度。
5. 语音交互功能可开启/关闭：对话模式开关即语音交互开关；Web 控制台可远程对指定设备开启/关闭对话模式，便于调试。

## 11.14 设备端 Wi-Fi 配网（2026-06-10 新增）

1. 设备需支持**不烧录改配置**即可更换 Wi-Fi：无可用凭据或连接持续失败时进入配网模式。
2. 配网方案采用 ESP-IDF `wifi_provisioning`（BLE transport），手机用乐鑫 EspBLEProv 类工具或后续自有页面下发 SSID/密码；成功后写入 NVS 并自动连网。
3. 配网模式与「Wi-Fi 失败 → BLE 手机网关兜底」是两条互斥的 BLE 路径，由启动状态机区分：
- 无凭据 → 配网模式（屏幕展示配网指引/设备名）；
- 有凭据但连接失败 → 先重试，超阈值进入 BLE 网关兜底；兜底页提供入口可手动切换回配网模式。
4. 屏幕需展示配网状态：等待配网 / 收到凭据 / 连接中 / 成功 / 失败原因。

## 11.15 硬件 UI 升级为 LVGL 中文界面（2026-06-10 新增，细化 11.10）

1. 固件 UI 从色块状态屏升级为 LVGL 图形界面：中文文字、图标、可点按大按钮。
2. 必备页面：待机页（大号对话模式按钮 + 在线状态）、对话页（聆听/思考/播放状态 + ASR 文本与 Bot 字幕滚动）、错误页（可读错误 + 触摸重试）、设置/诊断页（11.10 既有要求）、配网页（11.14）。
3. 中文渲染使用按需裁剪的字体子集，控制 Flash/PSRAM 占用；LVGL 渲染任务不得阻塞 I2S 采集/播放与 WebSocket 收发（沿用事件队列架构）。
4. 触摸为主输入（CST816），BOOT 键保留为备用 PTT/诊断入口。

## 11.16 协议增量（2026-06-10，随对话模式/Opus 落地）

> 字段定义在 `voice_robot/protocol.py`，此处同步登记（AGENT.md §五.1）。

1. `audio_start.codec` 新增 `"opus_raw"`：端侧上行**裸 Opus 包**，每包以 `[u16be 长度][Opus 包]` 帧化（区别于 Web MediaRecorder 的容器 `"opus"`）。后端将裸包封装为 Ogg/Opus 容器后用 ffmpeg 解码。
2. 云→端新增 `set_conversation{enabled:bool}`：远程开启/关闭硬件对话模式；经 `POST /api/devices/{id}/conversation` 触发。
3. 固件上行 codec 由 Kconfig `VR_UPLINK_CODEC` 选择，默认 `pcm_s16le`（流式 ASR、低延迟）；`opus_raw` 为弱网/BLE 可选项。
4. 云→端 `tts_start` 增加 `tts_sample_rate_hz`：告知下行 PCM 采样率，客户端（设备/Web 真机模拟）据此用匹配采样率播放，避免逐帧重采样产生的电流杂音。

## 11.17 性能、交互与模型成本治理（2026-07-11 新增）

1. 语音轮次必须具备可关联的唯一 `turn_id`，不得用连接对象地址、文本长度或音频长度拼接，避免等长输入发生观测数据串轮。
2. 对话记录不得把 PCM 以 Base64 长期内嵌在 `turns.jsonl`。TTS 音频改为内容寻址 sidecar，轮次只保存相对路径、格式、采样率与字节数；读取端需兼容历史 Base64，并在安全重写时无损迁移。
3. 普通回复 TTS 增加有界、内容寻址的持久化缓存。缓存身份至少包含文案、服务类型、资源/音色、采样率与编码；命中时不得再次调用 TTS，文案或语音配置变化必须自动失效。
4. `tts_start` 表示“音频已准备好且即将下发”，不得在整段合成开始前提前发送。LLM 完成到首段音频可播期间，端侧保持“回复中/准备语音”而不是误显示“播放中”。
5. 每轮至少记录 LLM 完成耗时、TTS 准备耗时、发送/播放耗时、端到端耗时、缓存命中、回复字符数、成功/失败及错误类型；LLM 用量同时记录请求类型、prompt 字符数、Token 与调用延迟。
6. LLM 上下文采用显式预算：短期对话默认只取最近 8 轮，长期摘要与相关结构化记忆补足长期信息，总字符预算默认不超过 3200；确定性的安全、时间、口算等本地路径不得先构建完整模型上下文。
7. Web 成本页除 Token 与估算费用外，至少展示成功/失败调用、平均与 P95 LLM 延迟、平均 prompt Token；价格必须标明为可配置估算，不能把页面默认值当成所有运行态模型的真实价格。
8. 成本优化优先级按实际计费权重排序：先消除重复 TTS、无效 ASR 和重复模型调用，再压缩 LLM prompt。所有缓存都必须有容量上限和配置身份隔离，不得以牺牲个性化正确性换取跨用户错误复用。
9. 本期不自动清理历史 `.bak` 或线上用户数据；存量迁移必须可回滚、先写 sidecar 再原子替换元数据。历史备份清理另行提供清单并由用户确认。
10. 详细决策见 ADR `0009-server-performance-and-model-cost-guardrails.md`，实施计划见 `docs/design/plans/2026-07-11-performance-cost-optimization.md`。
11. ASR/TTS 成本事件必须带版本号和完整计量字段；旧版缺字段事件只能计为“已忽略历史样本”，不得用 0 时长/0 字符污染平均值和费用。
12. 上游 TTS 中途断流时可一次性播放已完成的前半段，但必须标记 `reason=partial`，不得写入通用缓存或会话回放 sidecar；LLM `finish_reason=length` 同样不得当作完整成功回复。
13. WebSocket 必须限制设备 ID、用户 ID、文本、调试行、单音频帧、单语音段总字节与最长时间，防止异常设备无界占用内存、ASR 时长和 LLM prompt。
14. 同一时间窗口存在多个付费模型时，单一输入/输出单价不得套用到全部 Token；控制台应标记为“不可估算”或按模型分别计价。
15. ASR 热词必须覆盖所有启用场景的进入意图短语，并在场景激活后追加退出短语和领域词；首次进入场景不能因为“尚未进入”而缺少意图热词。
16. ASR/意图/LLM 阶段的有效进展必须刷新端侧有界 watchdog。面向用户的错误消息使用可操作中文提示，技术详情放在 `detail`/本地审计；空识别不得进入 LLM，避免无效延迟与模型费用。

---

# 十二、硬件端侧自治（2026-06-11 新增 → 2026-06-12 已弃用回退）

> ⚠️ **本章整章已弃用（2026-06-12）**。产品方向调整为「**服务端权威 + 硬件瘦客户端**」：硬件只做复杂交互与语音输入输出（采音上行 / 放 TTS / UI / 配网），模型调度与记忆管理回到服务端单一实现；服务端后续发布到字节云供外网访问，数据全部存服务端。端侧自治代码（`vr_*` + Ark 直连）已删除。
>
> 现行架构与取舍见 ADR `docs/knowledge/decisions/0005-revert-to-server-authoritative-thin-client.md`，落地计划见 `docs/design/plans/2026-06-12-server-authoritative-thin-client.md`。本章保留作历史。其中 **§12.3 配网（BLE 配对手机 → 手机 Wi-Fi/热点）仍然有效**（属于瘦客户端形态，另见 §11.14）。

> （历史）本章曾是端侧自治需求的唯一来源。架构取舍记录见 ADR `0003`（已被 `0005` 取代），分期落地计划见 `docs/design/plans/2026-06-11-on-device-autonomy.md`（已弃用）。

## 12.1 目标：联网即可独立交互

1. 硬件**只要联网就能独立完成整条语音对话交互**，不依赖本项目自建的 FastAPI 服务端处于运行链路中。
2. 「独立」的边界明确为：**独立于自建服务端，但仍需联网**——ASR / LLM / TTS 仍是火山云端能力，设备**直连火山云**完成识别、推理、合成；不追求断网离线对话（ESP32-S3 无法本地跑 ASR/LLM/TTS）。
3. 记忆管理模块与语音交互流程核心从服务端「搬到」硬件：固件在端侧自行编排 ASR→记忆检索→组装 Prompt→LLM→安全→TTS，并在端侧持久化与更新记忆。

## 12.2 架构：数据平面 / 控制平面分离（双平面）

1. **数据平面（语音）**：设备 → 火山云**直连**。三段式独立链路（流式 ASR 2.0 / Ark·Doubao / seed-tts V3），与现有服务端 `services/asr|llm|tts` 链路**逐段对齐**，不采用火山 Realtime 端到端方案（与 §11.9 既有决策一致：要让 ASR 文本进入端侧记忆与 LLM，逐轮可控）。
2. **控制平面（管理/同步）**：设备 ↔ 自建服务端保留**一条轻量长连接（不承载音频）**。Web 控制台经服务端向设备下发 API key、同步记忆、发送远程命令。复用现有设备注册表与 `/api/devices/{id}/*` 命令通道，能穿透 NAT / 手机热点。
3. **服务端角色降级**：从「运行时音频代理」降级为「**管理面 + 开发调试宿主**」。现有完整 `/ws` 音频代理链路**继续保留**，作为 Web 调试与「真机模拟器」的运行环境，也是端侧实现的**权威参考实现**。
4. **权威实现与一致性纪律**：编排 + 记忆这套核心链路以 **Python 服务端为权威参考实现**，固件为**对齐它的 C 移植**；用一组 **golden 测试向量**（同一输入在两端产出一致的检索结果 / Prompt / 记忆写入）约束两端行为一致。日常迭代仍**先在 Web/服务端调试，再烧录到硬件**。

## 12.3 配网：BLE 配对手机 → 手机 Wi-Fi / 热点

1. 硬件联网方式：**先与手机蓝牙（BLE）配对**，再**通过手机端联 Wi-Fi 或手机热点**上网（在 §11.14 设备端 BLE 配网基础上硬化）。
2. 手机侧（先用 Web Bluetooth 浏览器原型，后续自有 App）经 BLE 向设备下发 SSID / 密码（含「连手机热点」场景）；设备写入 NVS 并自动联网。
3. 配网与运行解耦：配网只负责让设备拿到可上网的 Wi-Fi；联网后语音走数据平面直连云、管理走控制平面长连接。

## 12.4 Web 控制台：连接硬件、同步记忆、管理 key

1. Web 控制台可**连接指定在线硬件**（经控制平面），查看与编辑该设备的**记忆管理信息**（用户画像、结构化记忆、对话轮次、长期摘要、审计日志），并**同步回硬件**。
2. Web 控制台**管理模型后台 key**（LLM 的 `api_key/model/base_url/system_prompt/user_prompt_template`，语音的 `asr_*/tts_*`，沿用 §11.2 的编辑/隐藏/显示四态），保存后经控制平面**下发到硬件**，供设备直连云端使用。
3. 同步需有**明确的方向与冲突策略**：区分「Web→设备」下发与「设备→Web」上报；记忆带版本/时间戳，避免双向覆盖丢数据（具体策略在计划中定义）。
4. key 在端侧的存储与下发需考虑**安全**：经鉴权后的控制平面下发，落 NVS；Web 侧展示仍遵循掩码/显示态规则。

## 12.5 端侧记忆智能范围（v1 即完整）

1. v1 硬件端即具备**完整记忆智能**：除「最近 N 轮短期窗口 + 本地检索打分」外，端侧**直接调用 LLM** 完成用户画像抽取、结构化记忆抽取，并**每 20 轮做一次长期摘要**（对齐服务端 `profile_extractor` / `summary_store` / 第 40 轮起滚动压缩的既有策略）。
2. 端侧记忆数据模型与目录结构与服务端**保持一致**（profiles / conversations / memories / summaries / 审计），以便控制平面双向同步与 golden 向量比对。
3. 端侧记忆的存储介质为设备 flash（容量与写寿命在计划中评估，必要时挂载 SD / 扩 flash 分区）。

## 12.6 复用与开发模式（约束）

1. 服务端与 Web 的整条交互核心链路与硬件端**复用**（契约层复用：云接口契约、记忆数据模型、Prompt 模板、协议、golden 向量；实现为 Python + C 两份，靠 spec 与向量保持一致）。
2. 日常开发模式：**先在 Web 端调试**（服务端跑权威参考实现），**之后再烧录到硬件**。
3. 本章为重大、跨模块、改架构、分多期的改动：先沉淀 PRD（本章）+ ADR + 分期计划，再进入编码（遵循 AGENT.md §一.2）。

## 12.7 控制平面协议增量（2026-06-11，随 P2 落地）

> 字段定义在 `voice_robot/protocol.py`，此处同步登记（AGENT.md §五.1）。控制平面消息走现有设备 `/ws`，不承载音频。

1. 云→端 `push_keys`：下发设备直连云所需的模型/语音配置，数据在 `payload`（`config_version` + `llm{api_key,model,base_url,system_prompt,user_prompt_template}` + `speech{asr_*,tts_*}`）。经 `POST /api/devices/{id}/push-keys` 触发；HTTP 响应只回非敏感摘要（`*_set` 布尔 + model/base_url + config_version），密钥仅走设备 `/ws` 通道。
2. 云→端 `memory_sync`：下发某 user 的有界记忆快照，`payload` 含 `snapshot_version` + `profile` + `memories_index` + `summaries_index` + `recent_turns(≤20)` + `conversation_meta`。经 `POST /api/devices/{id}/memory/sync`（body `user_id`）触发。
3. 双向已打通：**服务端→设备**（`push_keys`/`memory_sync` 下发）与**设备→服务端**（`/ws` 入站 `memory_report` → `device_sync.apply_memory_report` 合并落盘 + 回 `memory_report_ack`）。合并核心 `merge_memory_snapshot` 按 id/key 较新时间戳为准、等时间戳异内容判冲突→保留本地并写审计（`memory_sync_conflict`）。
4. 合并边界（有意为之）：画像 / 结构化记忆 / 长期摘要为合并权威集；**原始对话轮次（turns）不参与跨端合并**——两端各自维护 append-only 序号日志（跨独立序号计数器的合并不在范围内）。
5. 端侧记忆同步：摘要 `.md` 正文随快照 `summary_contents` 携带，接收方可重建本地 `.md`；结构化记忆按 index 条目重渲染 `.md`，保证两端目录同构。
