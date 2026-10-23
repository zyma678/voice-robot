# 英语学习对话场景 + 可扩展意图场景框架 设计方案

> 最后更新：2026-06-23
> 状态：**P1 后端 ✅、P2 前端 ✅、P3 打磨 ✅ 已完成（2026-06-23）**。剩真机/线上联调。
> 关联：意图识别现状见 `voice_robot/agent/routing.py`；人设现状见 `bot_profile`（`runtime_config.py` / `admin_api.py`）；TTS 持久化缓存现状见 `VocabExplanationStore` / `IdiomExplanationStore`。
> 决策记录：ADR `0008-intent-scene-framework.md`（意图识别方式、场景作用域、提示音缓存取舍）。

## 一、目标与范围

1. **意图识别**：在聊天链路里识别用户「想学英语」的**模糊口语指令**（如「我想学英语」「教我说英语」「咱们用英语聊天吧」「练练口语」「English please」）。触发词表**可在 Web 端编辑**。
2. **英语学习对话场景**：命中后进入「英语学习」场景，Bot 人设切换为**少儿英语老师**，指导口语练习。人设（system prompt / 名称 / 风格）**可在 Web 端编辑、修改，并可一键恢复默认值**。
3. **场景进出（语音驱动）**：在与硬件正常聊天过程中可**通过语音触发进入**英语场景，也可**通过语音指令退出**（如「退出英语」「不学了」「结束练习」）。进入与退出各有一段**固定语音提示**。
4. **固定语音提示可编辑 + 服务端持久化缓存**：进入/退出提示文案在 Web 端可编辑；其 TTS 音频在**服务端持久化缓存**，避免每次进出场景重复消耗 TTS Token（二次合成只在文案变更时发生）。
5. **架构可扩展**：以**「意图场景（Intent Scene）」框架**承载，英语学习是第一个场景实例；后续新增场景（如「数学辅导」「讲故事模式」）只需新增一份场景配置 + 默认人设，不改主链路。
6. **人设配置统一收口**：新建**独立「场景」页签**，把当前散落各处的**所有模型/人设相关配置**集中迁入并**结构化展示**：
   - 模型配置页签的**基础 system prompt**（通用人设基线）；
   - 记忆管理页签的 **Bot 画像**（`bot_profile`：名称/角色画像/回复风格/自定义补充，全局人设层）；
   - 各**意图场景人设**（英语老师等）。
   迁移后，「模型配置」页签只保留**连接类参数**（api_key / model / base_url / user_prompt_template 等管道配置，非人设）。

非目标（本期不做）：
- 不做端侧（固件）新 UI；场景进出提示音/对话走现有 `tts_start → 音频 → tts_end` 链路，固件零改动即可发声。
- 不改 ASR/记忆/描红/成语等既有链路。
- 不做多场景并发；任一时刻一个连接至多处于一个场景。

## 二、已确认的关键决策（详见 ADR 0008）

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 意图识别方式 | **关键词/正则优先 + LLM 兜底** | 关键词命中零成本、确定性强、与现有 `routing.py` 一致；仅在关键词未命中且「疑似学习意图」时调一次轻量 LLM 分类，兼顾模糊指令覆盖与成本。 |
| 场景状态作用域 | **单连接内存态** | 状态存于当前 `/ws` 连接；断连/重启/换设备回到通用对话。实现简单，契合「瘦客户端 + 服务端权威」。 |
| 英语老师默认语言 | **全英文 + 少量关键中文提示** | 营造沉浸；关键纠音/鼓励处用一句中文兜底。该默认值仍可在 Web 端改写并恢复默认。 |
| 提示音缓存 | **服务端持久化 PCM sidecar 缓存**（复用 vocab/idiom 模式） | 文案不变即命中缓存零 Token；缓存 key 含 文案 hash + voice_type + sample_rate + encoding，任一变化自动重合成。 |

## 三、架构设计：可扩展「意图场景」框架

### 3.1 场景模型（Scene）

新增模块 `voice_robot/scenes/`：

- `scene.py`：场景的纯数据模型与默认值。
  ```python
  @dataclass(frozen=True)
  class SceneDefinition:
      scene_id: str                 # "english_tutor"
      name: str                     # "英语学习"
      enabled: bool
      enter_keywords: tuple[str, ...]   # 进入触发词/短语（关键词层）
      exit_keywords: tuple[str, ...]    # 退出触发词/短语
      intent_hint: str              # 给 LLM 兜底分类用的一句话意图描述
      persona_prompt: str           # 场景人设（追加/覆盖到 system prompt）
      enter_prompt_text: str        # 进入固定语音提示文案
      exit_prompt_text: str         # 退出固定语音提示文案
  ```
- `registry.py`：场景注册表。内置默认场景列表（首批仅 `english_tutor`），与 Web 端覆盖配置合并后给出**生效场景集**。新增场景 = 往内置默认表加一条 + 默认人设常量，无需改主链路。
- `defaults.py`：各场景的**出厂默认值**（英语老师人设、默认进入/退出触发词、默认提示音文案），供「恢复默认」使用。

### 3.2 意图识别（关键词 + LLM 兜底）

扩展 `agent/routing.py` 风格的纯函数，新增 `scenes/intent.py`：

1. **进入判定**（仅当当前不在任何场景时执行）：
   - L1 关键词层：对生效场景的 `enter_keywords` 逐一匹配（子串/正则）。命中 → 返回该 `scene_id`。
   - L2 LLM 兜底：仅当 L1 未命中、但文本含「弱学习信号」（如出现 `英语/English/口语/speak/学/练` 等粗筛词）时，调一次轻量分类（复用 `ArkChatLLMService.generate_freeform`，输出受限：`scene_id` 或 `none`）。粗筛词不命中则**完全不调 LLM**（绝大多数普通对话零成本）。
2. **退出判定**（仅当当前在场景中时执行）：对 `exit_keywords` 匹配；退出走纯关键词（退出指令明确，不需 LLM，省成本/降误退）。
3. 判定为纯函数 + 注入式 LLM 回调，便于单测（沿用 `routing.py`「pure deterministic」约定，可给 golden 向量）。

> 成本护栏：LLM 兜底每连接、每场景**仅在进入前的粗筛命中时**触发；进入场景后不再跑进入判定；退出判定纯关键词。

### 3.3 场景状态机（单连接内存态）

在 `/ws` 主循环（`server.py` `websocket_endpoint`）的 per-connection 作用域内新增：

```python
current_scene: str | None = None   # None = 通用对话
```

每个文本/ASR 轮在调用 agent 前，经 `scenes/controller.py` 决策：

- `current_scene is None`：跑**进入判定**。
  - 命中 → 置 `current_scene=scene_id`；本轮**只播放「进入提示音」**（verbatim，走缓存，不调对话 LLM）；可选下发一条 `scene_changed` 控制消息供 UI/日志。
- `current_scene == X`：跑**退出判定**。
  - 命中 → 置 `current_scene=None`；本轮**只播放「退出提示音」**（verbatim，走缓存）。
  - 未命中 → 正常对话，但**用场景人设**生成回复（见 3.4）。

> 复用既有 verbatim 播报骨架：`_run_turn` 已支持 `trace_plan/idiom_plan` 这类「念预生成文案、不调 LLM」的路径。新增一个 `prompt_play_plan`（念固定提示音）同构接入，差异仅是音频来自**提示音缓存**而非现合成。

### 3.4 人设分层与场景注入（Persona）

现状：`admin_api._resolved_prompt_config()` 把基础 system prompt（`llm.system_prompt`）与 `bot_profile` 渲染段拼接，经 `prompt_provider` 传入 `ArkChatLLMService`。

**人设三层模型**（由内到外叠加，外层不覆盖只追加）：

1. **基础人设**（`llm.system_prompt`）—— 通用对话基线（「面向小朋友、简短」等）。
2. **全局 Bot 画像**（`bot_profile`）—— 跨场景生效的全局人设层。
3. **当前意图场景人设**（`scenes.{id}.persona_prompt`）—— 仅在该场景激活的轮次追加。

> 这三层即「场景」页签里结构化展示的内容：第 1、2 层属**默认场景（通用对话）**卡片，第 3 层属各**意图场景**卡片。

**存储不做数据迁移（保护线上数据，AGENT.md §二.7）**：默认场景的两层仍复用既有运行态键 `llm.system_prompt` 与 `bot_profile`，只是**展示位置**搬到「场景」页签；意图场景人设落新 `scenes.{id}` 段。这样既满足「统一收口 + 结构化展示」，又不改既有部署的运行态 JSON 结构、不冒覆盖线上配置的风险。

**改造点**：让 prompt 解析**感知当前场景**。
- `_resolved_prompt_config(store, scene_id=None)`：先按现状拼基础 system prompt + `bot_profile`；当 `scene_id` 命中生效场景时，再在末尾追加**场景人设段**（`【英语学习场景】…`）。
- `_build_runtime_agent()` 已是**每轮重建**且 `prompt_provider` 为 lambda；把 `current_scene` 透传进去即可让该轮用场景人设：`_trigger_text_turn`/ASR 轮构建 agent 时传入 `scene_id`，`prompt_provider=lambda: _resolved_prompt_config(store, scene_id)[0]`。
- Token 归因：进入场景的轮次 `set_current_scenario(scene_id)`（现有 metrics contextvar），使该场景的 LLM/TTS 用量在 `token_usage_store` 里可按场景统计。

### 3.5 运行态配置（Web 可编辑）

`RuntimeConfigStore` 新增 `scenes` 段，结构：

```json
{
  "scenes": {
    "english_tutor": {
      "enabled": true,
      "enter_keywords": ["学英语", "教我英语", "练口语", "用英语聊天", "English"],
      "exit_keywords": ["退出英语", "不学英语了", "结束练习", "不练了"],
      "persona_prompt": "You are a friendly kids' English tutor ...",
      "enter_prompt_text": "好呀，我们开始英语练习吧！Let's speak English together!",
      "exit_prompt_text": "英语练习结束啦，我们换回中文聊天吧。"
    }
  }
}
```

- `load_scene_settings(scene_id)` / `save_scene_settings(scene_id, ...)`：归一化 + 与出厂默认合并（缺字段回落默认）。
- **恢复默认**：保存时若某字段提交空串，回落到 `scenes/defaults.py` 的出厂值；提供「恢复默认」即提交全部默认值（前端按钮 → PUT 默认）。
- 沿用现有 `runtime_config` 容错读/原子写约定。

### 3.6 固定提示音的服务端持久化缓存

新增 `voice_robot/memory/prompt_voice_store.py`，镜像 `VocabExplanationStore` 的 sidecar `.pcm` 模式：

- 缓存键：`(scene_id, kind)`，`kind ∈ {enter, exit}`；条目记录 `text_hash`（文案 SHA1 短哈希）+ `voice_type` + `sample_rate_hz` + `encoding` + `tts_path`。
- 取音频 `get_or_synthesize(scene_id, kind, text, tts_service, voice_type, sample_rate, encoding)`：
  1. 命中（hash + voice + sr + enc 全一致）→ 直接读 `.pcm` 返回，**零 Token**。
  2. 未命中（首次 / 文案改了 / 换音色或采样率）→ 调 `_collect_tts_audio` 现合成 → 落盘 → 返回。
- 失效触发：Web 保存提示音文案、或语音配置改了 `tts_voice_type/tts_sample_rate_hz/tts_encoding` 时，下次取音自动因 key 不匹配重合成（无需手动清缓存）。
- 提供预热与试听端点（见 3.7），便于 Web 端保存后立即生成并试听，避免首个用户进出场景时才现合成。

> 设计理由：进入/退出提示音是**固定短句、复用率极高**，每次现合成是纯浪费。沿用 vocab/idiom 已验证的「文案+TTS 一起落盘、JSON 存相对路径不塞 base64」模式，零新机制。

### 3.7 协议与 API

**WS 协议（`protocol.py`）**：
- 新增 server 控制消息 `scene_changed`（字段 `scene_id`、`action ∈ {enter, exit}`、`name`），供 Web 控制台/日志展示「已进入/退出 英语学习场景」。固件未知类型安全忽略，无需改固件。

**管理 API（`admin_api.py` / `server.py`）**：
- `GET /api/scenes`：列出生效场景（含默认/覆盖来源标记）。
- `GET /api/scenes/{scene_id}` / `PUT /api/scenes/{scene_id}`：读/存单场景配置（触发词、人设、提示音文案、enabled）。
- `POST /api/scenes/{scene_id}/reset`：恢复出厂默认。
- `GET /api/scenes/{scene_id}/prompt-tts?kind=enter|exit`：试听提示音（命中缓存或现合成并缓存）。
- `POST /api/scenes/{scene_id}/prompt-tts/rebuild`：保存后主动预热提示音缓存。

### 3.8 Web 控制台（`web_console/`）—— 新增独立「场景」页签，人设配置统一收口

新增**独立页签「场景」**（`data-tab-target="scenes"` / `#tab-scenes`，置于「模型配置」附近）。**所有模型/人设相关配置集中到此页签**，按卡片结构化展示：

- **卡片①：默认场景（通用对话）** —— 收编现有散落配置：
  - **基础 system prompt**：从「模型配置」页签迁来（仍读写 `GET/PUT /api/settings/llm` 的 `system_prompt` 字段）。
  - **Bot 画像**：从「记忆管理」页签的 `bot-profile` 区迁来（名称/角色画像/回复风格/自定义补充 + enabled，仍读写 `GET/PUT /api/settings/bot-profile`）。
- **卡片②：英语学习场景**（首个意图场景，读写 `/api/scenes/english_tutor`）：
  - 场景开关 enabled。
  - 进入/退出**触发词**编辑（标签/逐行，关键词层）。
  - **场景人设** prompt。
  - 进入/退出**固定语音提示文案** +「试听」+「保存并重建缓存」。
  - 「恢复默认」按钮（→ `POST /api/scenes/english_tutor/reset`）。
- 框架预留「新增场景」位（后续场景自动渲染为新卡片）。

**迁移动作**：
- 「模型配置」页签移除 system prompt 编辑块，只留连接类参数（api_key/model/base_url/user_prompt_template）。
- 「记忆管理」页签移除 `Bot 画像` 区块。
- 两处迁入「场景」页签的默认场景卡片；**后端端点不变**（仅前端 DOM 归位），降低风险。
- 资源版本号 `?v=` bump（前端静态资源改了必须 bump）。

同步更新 `tests/test_web_console_assets.py`：新增「场景」页签关键元素断言；调整原 system prompt / Bot 画像 断言到新位置（避免误删既有要求保留的元素 id —— AGENT.md §三.2）。

## 四、改动清单（按文件）

| 文件 | 改动 |
| --- | --- |
| `voice_robot/scenes/scene.py`（新） | `SceneDefinition` 数据模型 |
| `voice_robot/scenes/defaults.py`（新） | 英语老师出厂默认（人设/触发词/提示音文案） |
| `voice_robot/scenes/registry.py`（新） | 内置场景表 + 与运行态覆盖合并 → 生效场景 |
| `voice_robot/scenes/intent.py`（新） | 进入/退出判定（关键词层 + LLM 兜底回调），纯函数 |
| `voice_robot/scenes/controller.py`（新） | 单连接场景状态决策（进入/退出/对话三分支） |
| `voice_robot/memory/prompt_voice_store.py`（新） | 提示音 PCM sidecar 持久化缓存 |
| `voice_robot/runtime_config.py` | `load/save_scene_settings` + 归一化/默认合并 |
| `voice_robot/admin_api.py` | `_resolved_prompt_config(..., scene_id)` 场景人设注入；scenes/prompt-tts API |
| `voice_robot/server.py` | per-connection `current_scene`；轮前场景决策；提示音 verbatim 播报 plan；`scene_changed` 下发；prompt-tts 端点 |
| `voice_robot/protocol.py` | `scene_changed` 控制消息 |
| `voice_robot/metrics/context.py` | （复用）进入场景轮 `set_current_scenario(scene_id)` |
| `web_console/{index.html,app.js,styles.css}` | 新增独立「场景」页签（默认场景卡片 + 英语场景卡片，结构化）；把 system prompt（原模型配置）与 Bot 画像（原记忆管理）DOM 迁入；`?v=` bump |
| `tests/` | 意图判定 golden、场景状态机、提示音缓存命中/失效、API roundtrip、web assets 断言 |

## 五、分期落地

- **P1 后端框架 + 英语场景（无前端）** ✅ 已完成（2026-06-23）：`voice_robot/scenes/`（scene/defaults/intent/controller/registry）、`memory/prompt_voice_store.py`、runtime config `scenes` 段、`_resolved_prompt_config(scene_id)` 人设注入、`_build_runtime_agent(scene_id)`、单连接 `current_scene` 状态机 + `_scene_for_turn`、`_run_turn` 的 `scene_prompt_plan` verbatim 缓存播报、scenes CRUD/reset/prompt-tts API、`scene_changed` 协议。新增测试 `test_scene_intent`/`test_prompt_voice_store`/`test_scenes_api`/`test_scene_ws`，`pytest -q` 402 passed（仅 6 个既有 firmware-static 失败，与本期无关）。
- **P2 Web 控制台** ✅ 已完成（2026-06-23）：新增独立「场景」页签（`#tab-scenes`，资源版本 `?v=20260623-scenes`）。默认场景卡片收编**基础 system prompt**（从模型配置迁出，仍读写 `/api/settings/llm`；保存时先 `?reveal=true` 取全量字段再回写，避免 all-or-nothing PUT 清空 key/model）+ **Bot 画像**（从记忆管理迁出，读写 `/api/settings/bot-profile`）。意图场景卡片由 `GET /api/scenes` 动态渲染（`buildSceneCard`，未来场景自动成卡）：enabled/进退触发词/人设/进退提示音（试听 + 保存并重建缓存）/恢复默认。同步从模型配置移除 system prompt 输入（`saveLLMSettings` 改为保留 `state.llmSettings.systemPrompt`）、从记忆管理移除 Bot 画像页签。`test_web_console_assets` 增 `test_console_scenes_tab_assets_are_present` 并改既有断言；`node --check` OK、`pytest -q` 403 passed（仍仅 6 个既有 firmware-static 失败）。
- **P3 打磨** ✅ 已完成（2026-06-23）：
  - LLM 兜底分类提示词加 few-shot；输出解析抽出 `_parse_scene_classification` 并加固（容错 code fence/引号/`scene: xxx` 标签/`none`/子串），新增 `test_scene_llm_classify`。
  - 按场景 Token 用量：`token_usage_store.aggregate` 本就 `by_scenario` 分组；converse 轮 `set_current_scenario(scene_id)` 已把场景内 LLM 用量归到 `scenario=english_tutor`，新增 `test_scene_ws::test_in_scene_conversation_is_attributed_to_scene_scenario` 验证（不需改前端，Token 统计页自动按 scenario 展示）。
  - 扩展性：未默认上线第二个场景（避免改动产品行为），改以 `test_scene_extensibility` 证明——monkeypatch 注入 `math_tutor` 后，registry/API/intent/人设注入全链路无需改码即生效。
  - `pytest -q` 415 passed（仍仅 6 个既有 firmware-static 失败无关）。

## 六、验证

- `pytest -q` 全绿；新增：`tests/test_scene_intent.py`、`tests/test_scene_controller.py`、`tests/test_prompt_voice_store.py`、`tests/test_scenes_api.py`，并扩 `test_web_console_assets.py`。
- `node --check web_console/app.js`。
- 端到端（TestClient `/ws`）：模拟 `text_input`「我想学英语」→ 收到 `scene_changed(enter)` + 进入提示音 TTS；场景内对话 `llm_prompt` 含英语人设；`text_input`「退出英语」→ `scene_changed(exit)` + 退出提示音；缓存第二次进出**不再产生 TTS 合成调用**（断言缓存命中）。

## 七、风险与护栏

- **误进入/误退出**：触发词过宽会误进场景。护栏：进入用「弱信号粗筛 + LLM 兜底」降低误判；退出用明确指令关键词；触发词 Web 可调。
- **LLM 兜底成本**：仅粗筛命中才调一次；进入场景后不再判定。需在 ADR 固化粗筛词与「不调 LLM」的默认路径。
- **提示音缓存陈旧**：缓存 key 含 文案 hash + voice + sr + enc，任一变化自动重合成，杜绝「改了文案还放旧音」。
- **不覆盖线上数据**：缓存写入仅限提示音 sidecar（runtime 卷），不触碰用户对话/分组数据（遵守 AGENT.md §二.7）。
