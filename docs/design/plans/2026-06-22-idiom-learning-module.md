# 成语学习模块设计方案

> 最后更新：2026-06-22
> 状态：P1（后端+缓存）✅、P2（前端）✅、P3（固件大字）✅ 已完成（待真机烧录手感确认）
> 关联：复用「生字管理」既有架构（见 `2026-06-16-kaiti-character-trace.md`、`2026-06-02-web-console-implementation.md`）

## 一、目标与范围

在 Web 控制台新增「成语学习」模块，交互与「生字管理」对齐：

1. Web 端**批量输入成语**（粘贴整段文本或逐行成语，自动抽取/去重）。
2. 网格**展示成语汉字**（类似田字格陈列，单位是成语而非单字）。
3. 可为每条成语**生成「成语解读」+「成语小故事」**两段文案（LLM）。
4. 支持**下发到硬件**：屏幕**展示成语大字**（无描红/笔顺动画），同时**朗读**解读或故事（TTS 逐字稿）。
5. **所有内容服务端持久化缓存**：成语列表/分组、解读、故事、两段 TTS 音频均落盘，刷新/换会话可恢复。

非目标：不做描红/笔顺动画；不做拼音逐字教学；不改对话 agent 的常规问答链路。

## 二、与「生字管理」的对应关系（复用优先）

| 维度 | 生字管理（既有） | 成语学习（新增） |
| --- | --- | --- |
| 持久化 store | `VocabExplanationStore`（单字 word+explanation+1 段 TTS） | `IdiomExplanationStore`（成语 explanation+story+2 段 TTS） |
| 分组/列表配置 | runtime config `vocab_groups` | runtime config `idiom_groups` |
| 生成解读 | `POST /api/vocab/explanation` | `POST /api/idiom/content` |
| 列表恢复 | `GET /api/vocab/explanations` | `GET /api/idiom/contents` |
| TTS 试听 | `GET /api/vocab/{char}/tts` | `GET /api/idiom/{idiom}/tts?kind=explanation\|story` |
| 下发到真机 | `POST /api/devices/{id}/trace-say` | `POST /api/devices/{id}/idiom-say` |
| 设备命令/控制消息 | `show_character_trace`（描红） | `show_idiom`（大字填充，无动画） |
| 逐字稿播报计划 | `CharacterTracePlan` | `IdiomDisplayPlan` |
| 前端状态/页签 | `state.vocab` / `#tab-vocab` | `state.idiom` / `#tab-idiom` |

> 结论：后端 store、runtime config、server 端点、前端页签几乎一一镜像，**核心新增工作量在两处**：①一条成语对应两段文案/两段音频的数据模型；②固件「大字静态填充」渲染（区别于既有描红）。

## 三、后端设计

### 3.1 新增 `IdiomExplanationStore`（`voice_robot/memory/idiom_explanation_store.py`）

镜像 `VocabExplanationStore` 的原子写 / 容错读 / TTS 旁路文件（sidecar `.pcm`，索引里存相对路径，不在 JSON 里塞 base64）模式。差异：

- 主键 `idiom`（2–8 个汉字字符串），`_safe_component` 既有正则已涵盖 CJK，可直接复用做文件名。
- 每条目两段文案 + 两段音频：

```json
{
  "idiom": "守株待兔",
  "pinyin": "shǒu zhū dài tù",
  "explanation": "成语解读文案…",
  "story": "成语小故事…",
  "source": "llm | fallback",
  "explanation_tts_path": "idiom_explanations/console-idiom/tts/守株待兔-explanation.pcm",
  "explanation_tts_sample_rate_hz": 16000,
  "story_tts_path": "idiom_explanations/console-idiom/tts/守株待兔-story.pcm",
  "story_tts_sample_rate_hz": 16000,
  "created_at": "...",
  "updated_at": "..."
}
```

- 方法：
  - `upsert(user_id, *, idiom, pinyin, explanation, story, source, explanation_tts=None, explanation_sr=None, story_tts=None, story_sr=None)` —— 文案变更但无新音频时，清掉对应 `*_tts_*` 旧标记（沿用 vocab 的失配清理逻辑，避免播放过期音频）。
  - `get` / `list`（按 `updated_at` 倒序）。
  - `read_tts_audio(user_id, idiom, kind)`，`kind ∈ {explanation, story}` —— 含 vocab 已有的路径越界/后缀/存在性校验。
- 命名空间：与 vocab 一致，成语库是**控制台全局库**（不绑定具体设备用户），固定 `IDIOM_CONTENT_USER_ID = "console-idiom"`。

### 3.2 RuntimeConfig 增加 `idiom_groups`（`voice_robot/runtime_config.py`）

镜像 `load/save_vocab_groups_settings`：

- 结构：`{ active_group, active_status_filter, groups: { 名称: { idioms:[...], statuses:{...}, sections:[...] } }, explanation_prompt_template, story_prompt_template }`。
- 复用 `_unique_*` / `_normalize_*` 同款归一化（把 `characters` 换成 `idioms`，状态枚举沿用 未学/新学/已学，可选）。
- 默认分组名如「成语 · 第一批」。
- admin_api 加 `GET/PUT /api/idiom/groups`（镜像 `get/update_vocab_groups_settings`）。

### 3.3 server.py 端点

复用既有 `_call_llm_raw` / `_collect_tts_audio` / 设备命令流（`_command_send_for_device`）。

1. **`POST /api/idiom/content`**
   - body：`{ idiom, regenerate?, explanation_prompt?, story_prompt? }`。
   - 校验：`idiom` 为 2–8 个汉字（`一 ≤ c ≤ 鿿`）。
   - 非 `regenerate` 且命中缓存：直接返回缓存文案 + `tts_url`（带 `kind`）。
   - 否则：**一次 LLM 调用**返回 JSON `{"pinyin","explanation","story"}`（省一次往返），沿用 `_parse_explanation_json` 同款容错解析（兼容 ```json 围栏 / 纯文本降级）。LLM 不可用时走 `_fallback_idiom_content`（用成语本身拼一段保底文案，参考 `_fallback_explanation`）。
   - 分别对 explanation、story 合成 TTS，`upsert` 落盘；返回 `has_explanation_tts/has_story_tts` 与 `tts_url`。TTS 失败仅保存文字、附 `tts_detail`（与 vocab 一致，持久化失败不阻断生成）。
2. **`GET /api/idiom/contents`** —— 列出全部缓存条目恢复网格，按 `created_at` 升序（与 vocab 列表行为一致）。
3. **`GET /api/idiom/{idiom}/tts?kind=explanation|story`** —— 返回缓存 PCM（`audio/L16`），镜像 `get_vocab_tts_audio`。
4. **`POST /api/devices/{device_id}/idiom-say`**
   - body：`{ idiom, text, kind, user_id? }`，`text` 即要朗读的解读/故事逐字稿。
   - 构建成语大字渲染载荷（见 3.4），向设备命令流 `send_nowait({"type":"idiom_say", idiom, text, glyphs, user_id})`。
   - 设备未连接/忙 → 409，沿用 vocab 下发的错误语义。

### 3.4 大字渲染载荷与播报计划

- **大字数据来源复用楷体字形**：成语每个字都用既有 `character_trace_payload(ch)` 取轮廓（kaiti outlines），逐字组成 `glyphs: [payload, …]`。即「大字」= 用描红同源的矢量字形做**静态填充**，不放笔顺动画。这样无需新造大号点阵字体（固件现仅有 16px CJK 字体，做不出大字）。
- **屏幕布局：2×2**。4 字成语在 240×284 屏上排成 2 行 2 列同时静态展示（非逐字轮播）；每格约 110×110，居中留边。非 4 字成语（如 3/6 字）按行优先尽量铺满，超出格数滚动或缩格，布局参数化。
- **`IdiomDisplayPlan`**（镜像 `CharacterTracePlan`）：`{ idiom, pinyin, glyphs, narration }`。
- WS 回合 `_run_turn` 增加 `idiom_plan` 参数（与 `trace_plan` 并列）：
  - 命中 `idiom_plan` 时**跳过 LLM**、逐字稿 verbatim 播报（`reply_source="local-rule"`，与 trace-say 同款回合记账）。
  - 在 `tts_start` 处发送展示控制消息（对应既有 `_send_character_trace` 的位置，新增 `_send_idiom_display`）。
- `consume_commands` 增加 `idiom_say` 分支：`await _trigger_text_turn(tg, text, user_id, idiom_plan_override=IdiomDisplayPlan(...))`，与 `trace_say` 分支对称。

### 3.5 协议（`voice_robot/protocol.py`）

- `ServerMessageType` 增加 `"show_idiom"`。
- `ServerControlMessage` 增字段：`idiom: str|None`、`pinyin: str|None`、`glyphs: list|None`（沿用既有 `payload/strokes/outlines` 风格，全部可空）。
- 客户端控制消息 `extra="forbid"`，先扩协议再扩固件（公约 §五）。

## 四、固件设计（`firmware/main`）

LCD 240×284，仅 16px CJK 字体 → 大字必须走矢量字形填充。

- `ws_client.c` `_handle_control` 增加 `else if (strcmp(type,"show_idiom")==0)` 分支：复用既有 `json_extract_trace_outlines` 解析每个字的轮廓，逐字调用渲染。
- `display_lcd.c/.h` 新增 `voice_display_lcd_show_idiom(const char *idiom, glyph 列表, 每字 duration_ms)`：
  - **复用 trace 的轮廓填充渲染，去掉笔顺逐笔 reveal 动画**（即一次性把整字轮廓填满）。
  - **2×2 同屏静态展示**：4 字成语一次铺满 2 行 2 列，每字一格静态填充，不轮播；朗读期间画面保持。布局常量（格尺寸/间距/边距）参数化。
- 内存沿用 trace 既有策略：轮廓在 PSRAM 上 `heap_caps_calloc`，失败回退内部 RAM。
- **务实可选项（降固件改动量）**：不另起 `show_idiom`，而是给既有 `show_character_trace` 增加 `mode:"fill"`（或 `duration_ms:0` 表示静态填充），服务端按字逐帧分发。推荐**正式新增 `show_idiom`**（语义清晰、便于成语整体布局与轮播），fill 复用模式作为备选记录在此。

## 五、前端设计（`web_console`）

- `index.html`：顶部新增「成语学习」页签 `data-tab-target="idiom"` 与 `#tab-idiom` 面板，结构镜像 `#tab-vocab`：批量输入框 → 成语网格 → 设备选择 + 「全部生成」+ 下发 + 「打印」；提示词区分「解读模板 / 故事模板」两个 textarea。
- `app.js`：
  - `state.idiom`（镜像 `state.vocab`）：`groups / statuses / sections / contents（每条 {pinyin,explanation,story,has_*_tts}）/ glyphs（按需缓存的字形轮廓，供大字展示与打印）/ activeGroup / promptTemplates / serverLoaded`。
  - 批量录入：从文本抽取成语——按空白/标点/换行切分，过滤出「连续 2–8 个汉字」的词条并去重（区别于 vocab 的「抽单字」）。
  - **网格布局：一行 2 个成语**（CSS grid `grid-template-columns: repeat(2, 1fr)`，窄屏 ≤1100px 自动堆叠为 1 列，遵循公约 §六.2）。每张卡展示成语大字（4 字 2×2）+ 组词/拼音；卡内可编辑解读、故事；两个独立「试听」按钮（解读/故事）；下发时可选朗读哪一段。
  - 持久化沿用 vocab 的防抖保存（`PUT /api/idiom/groups`）+ 启动从服务端加载、失败回退本机缓存的策略。
- 同步更新 `tests/test_web_console_assets.py`（公约 §三）：补 `#tab-idiom`、`#idiom-print` 等关键 id 断言，勿删既有。

### 5.1 A4 打印（镜像 `vocabPrint`，文字版）

复用既有 `renderVocabA4Pages` / `showVocabPrintModal` / `renderPrintCell` 的画布渲染 + 弹窗保存/打印链路（A4 竖版 ~150 DPI PNG，移动端长按保存、桌面端下载或直接打印）。

- **新增 `idiomPrint()`**：取当前分组（按状态过滤后）可见成语，缺字形时批量 `fetchTracePayload` 补齐（纯读，不调模型，与 vocab 打印一致）。
- **打印内容只显示文字（成语大字），不打印解读/故事/拼音**——满足「打印出来的可以只显示文字」。
- **每条成语一格、整页排版**：页面 2 列（一行放两个成语），每格内 4 字**横排成一行（1×N）**大字轮廓填充（区别于屏幕的 2×2），格下不加解读条。每页放若干行（按 A4 高度算），页眉为分组名 + 页码。
- 绑定 `#idiom-print` 按钮，弹窗复用 `#print-overlay`。

## 六、持久化落盘汇总（满足「全部服务端缓存」）

| 内容 | 落点 |
| --- | --- |
| 成语列表 / 分组 / 状态 / 提示词模板 | runtime config `idiom_groups`（`runtime/…/console_settings.json` 同款文件） |
| 解读文案 / 故事文案 / 来源 / 拼音 | `runtime/<memory_root>/idiom_explanations/console-idiom/index.json` |
| 解读 TTS / 故事 TTS（PCM sidecar） | `…/idiom_explanations/console-idiom/tts/<成语>-{explanation,story}.pcm` |

> 注意（公约 §二.7 / 记忆[[feedback-never-overwrite-prod-data]]）：联调只用 GET 验证读路径；写接口（生成/下发）必须本地环境或独立测试分组，禁止向生产写测试数据。`*.bak` 暴增曾撑满远端磁盘（[[server-disk-full-bak-files]]），新 store 不引入 `.bak`，沿用 `.tmp`→`replace` 原子写。

## 七、验证

- `pytest -q` 全绿；新增最小覆盖：
  - `IdiomExplanationStore` 往返（upsert→get→list→read_tts_audio 两 kind、文案改无音频时清旧标记、路径越界拒绝）。
  - `TestClient` 串起 `POST /api/idiom/content`（缓存命中/regenerate/LLM 降级）、`GET /api/idiom/contents`、`GET /api/idiom/{idiom}/tts`、`POST /api/devices/{id}/idiom-say`（设备未连 409）。
  - `GET/PUT /api/idiom/groups` 归一化。
- `node --check web_console/app.js`；更新 `tests/test_web_console_assets.py`。
- 固件 `show_idiom` 走真机/模拟器验证大字填充与轮播（沙箱不绑端口，HTTP/WS 用 `TestClient`，公约 §四）。

## 八、分期建议

- **P1（后端+缓存）✅ 已完成（2026-06-22）**：`IdiomExplanationStore` + runtime `idiom_groups` + `POST /api/idiom/content`、`GET /api/idiom/contents`、`/tts`、`/groups` + 测试。同时已在服务端打通 `idiom_say` 下发 + `show_idiom` 控制消息 + `IdiomDisplayPlan` verbatim 播报（固件渲染留 P3，未知控制类型被安全忽略）。
- **P2（前端）✅ 已完成（2026-06-22）**：`#tab-idiom` 页签、`state.idiom`、批量录入（抽取 2–8 连续汉字）、一行 2 个网格（卡内 4 字 2×2 大字楷体填充）、解读/故事生成与编辑、两段试听、下发（可选朗读解读/故事）、分组持久化（`/api/idiom/groups`，含本机回退）、A4 文字打印（页面 2 列、每个成语 4 字横排一行、仅文字）。资源版本号 `20260622-idiom2`。
- **P3（固件大字）✅ 已完成（2026-06-22）**：`display_lcd.c/.h` 新增流式 `voice_display_lcd_show_idiom_begin/_glyph`（2×2 行优先布局、`idiom_fill_outline_into` 楷体轮廓静态填充、A8 canvas 白字重着色、点击关闭、无 timer/reveal 动画）；`ws_client.c` 新增 `json_split_glyphs` + `show_idiom` 分支（逐字复用一份 strokes/outlines 缓冲解析渲染）。`idf.py build` 通过（app 13% free）。**待真机烧录确认手感**（2×2 布局/字号/可读性）。
- 各期落地后回写 `docs/progress/WORK-IN-PROGRESS.md` 与本文件状态。
```
