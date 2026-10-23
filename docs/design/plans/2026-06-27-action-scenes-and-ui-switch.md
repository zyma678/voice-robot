# 计划：动作型意图场景（汉字/成语）+ 进入场景切「经典脸」+ 英语 ASR 热词

- 日期：2026-06-27
- 状态：**已完成并部署 + 真机复测通过（2026-06-27）**。确认点：动作型场景 · 成语取材=Web 已录入分组 · 成语两路径（问具体成语 A / 泛意图随机 B）· 新增成语门槛=**仅四字候选** · 一起部署。复测详情与三处修复（汉字重复说抽字 / 成语 LLM 60s 超时 + 批量重生 / VAD 240ms 待烧）见 `../../progress/WORK-IN-PROGRESS.md`。
- 相关：意图场景框架 ADR `../../knowledge/decisions/0008-intent-scene-framework.md`、英语学习计划 `2026-06-23-english-learning-scene.md`
- 背景需求（用户 2026-06-26/27）：
  1. 英语学习**热词表 Web 端可配置**（已实现代码+测试，随本批一起部署）。
  2. 把**汉字书写**意图识别整合进意图场景框架，**触发语 Web 可配**，退出仍是**双击屏幕**。
  3. **成语学习**也定义为一种场景，**触发词 Web 可配**，进入后**从后端随机播放一条成语**（朗读解读 + 成语故事 + 屏幕 2×2 大字），退出**双击屏幕**。
  4. **进入英语学习场景时主界面切到「经典脸」，退出时切回**。

## 一、已确认决策（来自用户答复）

- **D1 上线时机**：英语热词先攒着，和本批 #2/#3 一起部署（一次性 server+前端部署 + 固件烧录）。
- **D2 成语取材**：进入成语场景后，从**控制台「成语学习」已录入分组**里随机抽一条（内容可控、命中缓存零额外 Token；分组为空时回退）。
- **D3 场景运行模型**：汉字/成语是**动作型场景**——触发词 Web 可配 → 触发**一次性动作**（描红 / 随机成语播报）+ 屏幕**双击退出 overlay**；**不进入持久人设、无语音退出、不占服务端 `current_scene` 状态**。英语场景仍是**人设型场景**（持久人设 + 语音退出 + `scene_changed`），不变。

## 二、场景类型抽象

`SceneDefinition` 新增 `kind: str = "persona"`：

- `kind="persona"`（英语）：持久态，`current_scene` 记忆，语音 `exit_keywords` 退出，`persona_prompt` 叠加，`asr_hotwords` 注入，进出发 `scene_changed`（驱动固件切脸）。
- `kind="action"`（汉字/成语）：无状态，仅用 `enter_keywords` 做触发；命中即在**当轮**触发一次性动作；不写 `current_scene`，不发 `scene_changed`，无 `persona_prompt`/`exit_keywords`/`asr_hotwords`。退出由固件 overlay 双击关闭（已存在）。

Web「场景」页签按 `kind` 渲染不同卡片：
- persona 卡：现有全字段（启用/进退触发词/人设/ASR 热词/进退提示音/试听/重建/恢复默认）。
- action 卡：仅「启用 + 进入触发词 + 恢复默认」（无人设/提示音/热词/退出触发词）。

## 三、新增内置动作场景（`scenes/defaults.py`）

### 汉字书写 `hanzi_writing`
- `kind="action"`，`name="汉字书写"`，默认启用。
- `enter_keywords` 默认 = 现有 `character_trace.py` 的规范触发短语（怎么写 / 写一下 / 这个字 / 该怎么写 …）。
- 触发与取字（**保持现有鲁棒抽取，最小改动**）：
  - 主路径：场景启用时，`extract_trace_character(text)` 命中即描红（现有行为，新增「禁用即整体关闭描红」能力）。
  - 扩展路径（兑现「可配触发语」）：当用户在 Web 增配了触发词且 `text` 命中该词、并能从剩余文本抽到单个汉字时，也描红该字。默认词表与主路径等价，**不破坏既有用例**。
- 退出：固件描红 overlay 屏幕点击/双击关闭（已存在），无服务端态。

### 成语学习 `idiom_learning`
- `kind="action"`，`name="成语学习"`，默认启用。
- `enter_keywords` 默认（泛意图触发词）= ("学成语","成语学习","讲成语","成语故事","来个成语","来一个成语","教我成语")。
- **两条进入路径**（当轮 `user_text` 判定，路径 A 优先）：
  - **路径 A — 问具体成语**（如「夜郎自大是什么意思？」）：`extract_idiom_query(text)` 抽出候选成语。
    1. **优先匹配现有成语库**：候选若命中 `load_idiom_groups_settings()` 汇总的库成员 → 用 `IdiomExplanationStore` 缓存的解读+故事（命中=零额外 Token）。
    2. **库里没有 → 新增流程**：现场 `_generate_idiom_content`（解读+故事+TTS 缓存）**并写入 Web 成语库**（加入默认分组 `成语 · 第一批`，复用 `save_idiom_groups_settings`，加成员=追加不覆盖）。下次再问即命中缓存。
  - **路径 B — 泛意图**（命中 `enter_keywords`、且路径 A 未抽到具体成语）：从库汇总成员里随机一条（**优先取已缓存**的），播放。
  - 两条路径都复用 `IdiomDisplayPlan` + `_send_idiom_display`（`show_idiom` 2×2 大字）+ verbatim 朗读「解读+故事」旁白（不调 LLM）。
- 退出：固件成语 overlay 屏幕点击/双击关闭（已存在），无服务端态。

#### `extract_idiom_query` 与误判防护（待定细节 → 见下「确认点」）
- 抽取：归一化后按查询后缀（`是什么意思`/`什么意思`/`啥意思`/`的意思`/`怎么讲`/`什么典故`/`的故事`/`成语X`…）取后缀前的 2–8 连续汉字候选。
- **优先库内子串匹配**：先看 `user_text` 是否含任一库成员成语（确定是成语，最稳）→ 命中直接走路径 A.1。
- **新增成语的误判防护**：库里没有、要走 A.2 新增时，**只接受 4 连续汉字候选**（绝大多数成语为四字；避免「苹果是什么意思」这类把非成语写进库）。非四字候选不新增、不拦截，交给普通对话回答。

## 四、当轮处理顺序（`server.py` 一个 turn 内）

ASR → `user_text` 后：
1. **动作场景命中检测**（按启用的 action 场景）：
   - `hanzi_writing`：`extract_trace_character` 能抽到字 → 描红动作（复用 `_plan_character_trace`/`_send_character_trace`）。
   - `idiom_learning`：路径 A `extract_idiom_query` 抽到具体成语 → 解读该成语（库内缓存 / 库外新增并入库）；否则路径 B 命中泛意图触发词 → 随机一条。
   - 命中即本轮以该动作收尾（verbatim 播报，不进对话/不改 `current_scene`），与现有 trace/idiom verbatim 路径一致。
   - **优先级**：具体成语查询（A）> 汉字描红 > 成语泛意图（B）> 英语进出 > 普通对话。（描红针对单字、成语针对 2–8 字，二者候选不冲突。）
2. **人设场景进出**（现有 `_scene_for_turn`，仅英语）：enter/exit + `scene_changed`。
3. 普通对话。

> 现有描红已在 turn 流里（`server.py:1717/2082` 调 `_plan_character_trace`）。本次把「是否描红」纳入 `hanzi_writing` 场景的启用/触发词治理；成语动作为新增分支。

## 五、固件：进入场景切「经典脸」（#3）

- 服务端**已**在英语场景进出下发 `scene_changed`（`scene_action=enter|exit`）。
- 固件 `ws_client.c`：新增 `scene_changed` 分支：
  - `enter` → `voice_display_lcd_scene_face_push()`：记住当前 `s_ui_style`，**临时**切到 `UI_STYLE_FACE`（**不写 NVS**，不污染用户默认）。
  - `exit` → `voice_display_lcd_scene_face_pop()`：恢复进入前的风格。
- `display_lcd.c`：实现 push/pop（复用 `ui_style_apply` 重建控件树，但不持久化）；WS 断连兜底恢复（避免中途掉线卡在脸上；可在现有断连处理里调用 pop）。
- 动作场景（汉字/成语）**不切脸**（无 `scene_changed`），只弹 overlay。

## 六、英语 ASR 热词（#1，已实现）

- `SceneDefinition.asr_hotwords` + `ENGLISH_TUTOR_ASR_HOTWORDS`（少儿口语高频英文词，≤100 tokens）。
- `services/asr.py`：`VolcASRConfig.hotwords` → `request.corpus.context` 的 `{"hotwords":[...]}`（双向流式支持，软偏置，保留中英混合 + 实时字幕）。
- `server.py`：`_build_runtime_asr_service(scene_id=current_scene)` 注入当前场景热词。
- Web 场景 persona 卡新增「ASR 热词」编辑区；`?v=20260626-asrhotwords`。
- 已加测试，`pytest -q` 420 passed（仅 6 既有 firmware-static 无关失败）。

## 七、改动清单

- `scenes/scene.py`：`kind` 字段。
- `scenes/defaults.py`：`HANZI_WRITING` / `IDIOM_LEARNING` 内置场景 + 默认触发词。
- `scenes/registry.py`：`kind` 透传（出厂值，不可被运行态覆盖）。
- `runtime_config.py`：action 场景 save/load 只持久化 `enabled`+`enter_keywords`（其余字段对 action 无意义）。
- `admin_api.py`：`_scene_payload` 加 `kind`；`SceneSettingsUpdate` 对 action 场景宽容（多余字段忽略）；scenes API 不变。
- `server.py`：动作场景命中检测 + 成语随机动作；trace 纳入 `hanzi_writing` 启用治理。
- `web_console/`：场景卡按 `kind` 渲染；资源版本 bump。
- `firmware/`：`scene_changed` 分支 + `display_lcd` 临时切脸 push/pop；`idf.py build` + 烧录。
- 测试：动作场景触发/随机成语取材/汉字启用门控/`kind` 渲染断言。

## 八、部署与验收（一次性）

1. server + 前端增量部署（仅 `voice_robot/`+`web_console/`，不碰根 `.env` 与 runtime 卷）。
2. 固件 `idf.py flash`（含 `scene_changed` 切脸 + 既有成语 P3）。
3. 真机复测：英语进出切脸 + 英文数数识别（热词）/ 汉字「X怎么写」描红双击退出 / 成语触发随机播报 + 2×2 大字双击退出。
