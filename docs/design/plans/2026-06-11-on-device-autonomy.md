# 硬件端侧自治：分期实施计划

> ⚠️ **已弃用（2026-06-12）**：本计划被回退方案 `docs/design/plans/2026-06-12-server-authoritative-thin-client.md` 取代，架构决策见 ADR `0005`。硬件已回归「服务端权威 + 瘦客户端」，端侧自治代码（`vr_*`）已删除。本文保留作历史。

最后更新：2026-06-11（2026-06-12 标记弃用）

> 需求来源：PRD §十二（硬件端侧自治）。架构取舍：ADR `docs/knowledge/decisions/0003-on-device-autonomy-dual-plane.md`。
> 本计划是跨模块、改架构、分多期的工程，按期推进；每期标注状态与验收口径。先文档（本计划）→ 再实现 → 完成回写进展。

## 〇、目标回顾（一句话）

让设备**联网即独立**跑完整语音交互（直连火山云 + 端侧记忆），自建服务端退为**管理面 + 调试宿主**；Web 经控制平面连设备、同步记忆、下发 key；核心链路靠 **Python 权威实现 + C 移植 + golden 向量**保持复用与一致，**先 Web 调试再烧录**。

## 一、架构总览（双平面）

```
┌────────────┐   数据平面(语音, 直连)   ┌──────────────┐
│  ESP32-S3  │ ───────────────────────▶ │   火山云      │
│  固件(C)   │   ASR2.0 / Ark / seed-tts │ ASR/LLM/TTS  │
│            │ ◀─────────────────────── └──────────────┘
│ 端侧编排+  │
│ 端侧记忆   │   控制平面(管理, 不带音频)  ┌──────────────┐   Web 控制台
│            │ ◀──────────────────────▶ │  自建服务端   │ ◀──────────────
└────────────┘   key下发/记忆同步/命令    │ 管理面+调试宿主│
                                          │  /ws 权威参考 │  (开发期音频代理)
                                          └──────────────┘
```

- **数据平面**：设备直连火山云三段式，服务端不参与。
- **控制平面**：设备保留一条到服务端的轻量长连接（沿用 `/ws` 鉴权与设备注册表，但区分消息类别，不走音频）；Web 经服务端 `/api/devices/{id}/*` 驱动同步与命令。
- **调试宿主**：服务端现有完整 `/ws` 音频代理 + 真机模拟器保留不动，作为端侧实现的权威参考。

## 二、可移植核心契约（两端共享的"单一事实"）

这是"复用核心链路"的落点——**不是共享代码，是共享契约 + 向量**。落在 `docs/design/specs/`（新增或并入既有 spec），实现两份（Python 权威 / C 移植）：

1. **记忆数据模型**：profiles / conversations(turns.jsonl+meta) / memories(index+分类 md) / summaries(index+md) / 审计——固件与服务端**同构**（PRD §六、§11.7）。
2. **检索打分**：关键词(0.3)+重要度(0.2)+时间衰减(0.2)+过期降权+已解决降权，Top-3（`file_retriever` / `context_builder` 现有口径）。纯算术，C 可精确复刻。
3. **Prompt 组装**：system_prompt + 画像块 + 记忆块（短期窗口 + 长期摘要 + Top-3）+ user 模板（对齐 `context_builder` 与 runtime 配置）。
4. **抽取 / 摘要触发规则**：每轮后抽取（轮次<3 或纯闲聊跳过）；第 40 轮起每 20 轮滚动摘要、始终留最近 20 轮（`profile_extractor` / `_maybe_summarize` 现有口径）。
5. **云接口契约**：ASR 2.0（sauc 双向流式、`X-Api-Key`+`X-Api-Resource-Id`）/ Ark chat completions 流式 / seed-tts V3 单向流式——见 `docs/knowledge/integrations/`。
6. **golden 测试向量**：固定输入（含画像/记忆/多轮）→ 期望的 检索结果 / 组装 Prompt / 记忆写入 patch。服务端产出基准，固件 host C 单测比对，作为两端漂移的回归闸。

## 三、分期

> 状态图例：⬜ 未开始 / 🔄 进行中 / ✅ 完成。无硬件可做的优先前移；纯 DSP/真机项后置（沿用 ADR 0002「不假装验证」原则）。

### P0 契约固化（无硬件）— ✅（2026-06-11）
- ✅ 契约 spec：`docs/design/specs/on-device-memory-port-contract.md`（含「实现优先于早期设计稿」校准：真实检索公式无时间衰减；deleted 在排序前过滤等微妙点）。
- ✅ golden 生成器 + 基准：`tests/golden/generate_memory_contract.py` → `tests/golden/memory_contract.json`（检索排序/分数、记忆块、本地摘要、规则抽取、Prompt 渲染、markdown、safe_component）。
- ✅ 回归闸：`tests/test_golden_contract.py`；`pytest -q` 207 passed。

### P1 服务端参考化改造（无硬件）— ✅（2026-06-11，保守路线）
- ✅ 把 `agent/graph.py` 的**纯决策**抽成 `voice_robot/agent/routing.py`（`is_unsafe` / `classify_reply_route` / `enrich_memory_query`），`graph.py` 委托，**零行为变更**（现有 agent 用例全绿）；这是固件 C 端要 1:1 复刻的"端口"。
- ✅ golden 扩 `orchestration_routing` 向量 + 单测 `tests/test_agent_routing.py`；spec 增 §10 编排管线（步骤序列 + 副作用分类 + 路由表）。`pytest -q` **211 passed**。
- ↪ **直连云参考路径改挪到 P3**（带 rationale）：现 `server.py` `/ws` + `services/asr|llm|tts` 已是三段式权威参考；脱离 `/ws` 另建"设备视角直连"网络路径属**投机性并行实现**，无硬件时收益低、风险高。等 P3 固件 bring-up 真正需要趟协议时再建，且就地复用既有 services。
- 采用保守路线（只抽净纯决策、保持单一 `ainvoke` 流程），未做激进的 LangGraph 节点化拆分——golden 闸已足以护住行为，激进拆分性价比低。

### P2 控制平面：同步协议 + key 下发（无硬件可先做服务端/Web 侧）

**P2-后端 — ✅（2026-06-11）**
- ✅ 协议：`protocol.py` 加 `push_keys` / `memory_sync` 服务端消息（数据走通用 `payload`）。
- ✅ `voice_robot/device_sync.py`：`build_keys_payload`（resolve_* 解析有效 key/model/prompt，带 `config_version`）、`build_memory_snapshot`（有界版本化快照）、`merge_memory_snapshot`（**纯**冲突解析：按 id/key 较新时间戳为准、等时间戳异内容判冲突保留本地）、`keys_payload_summary`（非敏感摘要）。
- ✅ 端点：`POST /api/devices/{id}/push-keys`（HTTP 只回非敏感摘要，密钥仅走设备 `/ws`）、`POST /api/devices/{id}/memory/sync`；`consume_commands` 分发两类控制消息。
- ✅ 测试：`tests/test_device_sync.py`（merge 5 例 + payload 2 例 + 端点 4 例）；`pytest -q` **222 passed**。PRD §12.7 登记协议。

**P2-双向 + Web — ✅（2026-06-11，理想方案一次落地）**
- ✅ 设备→服务端上报：协议加 `ClientMemoryReportMessage`(`memory_report`) + 服务端 `memory_report_ack`；`server.py` recv_loop 处理 → `device_sync.apply_memory_report`（线程内执行，merge→落盘 profile/memories/summaries）→ 冲突写审计 `memory_sync_conflict` → 回 ack。
- ✅ 落盘：`FileMemoryStore.apply_index_snapshot`（重写 index + 重渲染各 `.md`）、`SummaryStore.apply_snapshot`（按 `summary_contents` 重写 `.md` + index，保留 created_at 维持合并 identity）；`build_memory_snapshot` 增 `summary_contents`。turns 不参与合并（设计边界）。
- ✅ Web：对话页设备控制区加「下发 key」「同步记忆」按钮（`/push-keys`、`/memory/sync`）；真机模拟器处理 `push_keys`/`memory_sync`/`memory_report_ack` 并加「上报记忆」按钮（`memory_report` 回环）。
- ✅ 测试：`test_device_sync.py`（merge/payload/apply/入站 E2E/端点 共 14 例）+ `test_web_console_assets.py` 增 3 个元素断言；`pytest -q` **225 passed**；`node --check` OK。

### P3 端侧三段式云客户端 — 🔄
- ✅ **服务端「设备视角」参考路径（2026-06-11，无真机，TestClient）**：`voice_robot/device_runtime.py`（`device_memory_root` 按 `memory_root/devices/<id>` 设备私有命名空间）+ 端点 `POST /api/devices/{id}/autonomous-turn`（复用 `_build_runtime_agent(root)`，跑与 `/chat` 同一 agent 管线，但对设备命名空间）。`/memory/sync` 现**镜像**快照进设备命名空间 → 闭环「Web 改记忆→同步→设备视角回复反映」。这是固件 `vr_runtime` 的 Python 权威对照。
  - 模拟器「自治回合」：`web_console` 设备模拟器加 `#sim-auto-text`/`#sim-auto-turn`，浏览器即可演示「同步的记忆出现在回复的 retrieved 里」。
  - 测试 `tests/test_device_runtime.py`（同步前 retrieved 空 / 同步后命中 / 安全拦截）；`pytest -q` **228 passed**。
- ✅ LLM(Ark) 客户端 `vr_llm_ark.{h,c}`（见 P4-B，idf build）。
- ⬜ C 流式云客户端（**真机阶段**，TLS + 火山二进制帧）：ASR 2.0 上行→文本；seed-tts V3 下行 PCM。对 `docs/knowledge/integrations/` 契约与参考路径验收。
- 复用既有端侧能力：`audio_capture`/`vad`/`conversation` 状态机、环形缓冲播放、LVGL UI。
- 配置来源：直连云所需 key/url 从 NVS 读取（由 P2 控制平面下发）。
- 验收：真机一轮「说话→端侧 ASR→端侧 LLM→端侧 TTS 播放」跑通；host C 单测覆盖协议封包/解析。

### P4 固件：端侧记忆移植（含完整智能）（真机+host 单测）— 🔄 已开始
- ✅（2026-06-11）**确定性核心移植**（纯逻辑、无真机、host 单测对齐 golden）：
  - `firmware/main/vr_routing.{h,c}`：`is_unsafe`/`classify_reply_route`/`enrich_memory_query`，对齐 golden `orchestration_routing`。
  - `firmware/main/vr_retrieval.{h,c}`：UTF-8 `[\w一-鿿]+` 分词 + 打分（命中率*0.7+重要度*0.3、resolved*0.5、deleted 排序前过滤）+ 排序，对齐 golden `retrieval` 分数与排序。
  - host 测试 `firmware/test/test_vr_{routing,retrieval}.c` + 运行器 `firmware/test/run_host_tests.sh`（7 个 host 测试全过）；`tests/test_firmware_host_tests.py` 把它纳入 `pytest -q`（226 passed）。
  - 已加入 `firmware/main/CMakeLists.txt` SRCS；`idf.py build` 通过（PCM bin 55% 余量）。
  - **Prompt 组装**（2026-06-11）：`vr_context.{h,c}`（§4 四段记忆块 + 空白折叠裁剪 + 段/项上限，对齐 golden `memory_block_lines`）、`vr_prompt.{h,c}`（§8 system+user 模板渲染 + 强制记忆注入 + hints 兜底，对齐 golden `ark_prompt_default/forced_injection`）；host 测试 `test_vr_context.c`（串联两者）。`idf.py build` 通过。
    - 注：ESP 工具链 `-Werror=format-truncation` 比 host `cc` 严格——组合行改用有界 `append()` 而非 `snprintf %s`；`idf.py build` 为权威编译闸。记忆块 >6000 字的预算压缩路径暂未移植（golden 未覆盖）。
  - **markdown 渲染**（2026-06-11）：`vr_markdown.{h,c}`（§9 记忆/摘要 `.md` 渲染，对齐 golden `memory_markdown`/`summary_markdown`）；含 `vr_py_float_str` 还原 Python `str(float)`（1.0→"1.0" 而非 "1"）。host 测试 `test_vr_markdown.c`，`idf.py build` 通过。
  - **规则抽取**（2026-06-11）：`vr_extract.{h,c}`（§6 `_extract_with_rules` 兜底：兴趣/风格/称呼/薄弱点）；手写 `不会\s*([0-9xX+\-*/= ]+)` 与 3 条称呼模式（无 regex 库）+ 尾语气词剥除；对齐 golden `rule_extraction` 全 5 例。`idf.py build` 通过。
  - **路径归一化**（2026-06-11）：`vr_paths.{h,c}`（§2 `safe_component`/`safe_filename`，对齐 golden）；纯逻辑、idf build 通过。
  - **小结**：纯确定性核心 **7 件全移植** —— routing/retrieval/context/prompt/markdown/extract/paths，11 个 host 测试全过、纳入 `pytest`、idf build 通过。
- ✅ **存储选型 ADR**（2026-06-11）：`docs/knowledge/decisions/0004-on-device-memory-storage.md`——LittleFS + 独立 `storage` 分区 + cJSON（已是依赖）+ POSIX 抽象（host 可测）+ 本地文件不要求字节对齐（同步走快照）。
  - **§1 存储模块（host 可测部分）**（2026-06-11）：`vr_store.{h,c}`（POSIX + cJSON）——画像 JSON 读写、`meta.json`、`turns.jsonl` 追加（自增 sequence）、`index.json` 读写；host 测试 `test_vr_store.c` 用临时目录跑通往返，并**串联 `vr_retrieval`**（载入 index→检索 mem_a1、deleted 排除）验证存储+检索端到端。
    - ⏸ **设备集成后置**：`vr_store.c` **暂未入固件 SRCS**——需先做分区表加 `storage` + LittleFS 挂载 + cJSON `REQUIRES`（ADR 0004），且 flash 行为（磨损/满盘/掉电/首刷格式化）须真机验收。host 测试经 `run_host_tests.sh`/`pytest` 覆盖逻辑。
  - **端侧 runtime 编排**（2026-06-11）：`vr_runtime.{h,c}`——把 routing/retrieval/context/prompt/extract/store **串成一轮对话闭环**（ASR 文本→富化检索→载入 index/最近轮→记忆块→安全/路由→回复→落盘 turn→规则抽取→写结构化记忆）；LLM 以回调注入（设备接云、host 用桩）。host 集成测 `test_vr_runtime.c` 验证：检索记忆进入 prompt、抽取写回 index、安全拦截兜底、无 LLM 时 fallback、meta/turns 自增。
    - ⏸ 同 `vr_store` 一样**暂未入 SRCS**（依赖 cJSON + FS）；host/pytest 覆盖逻辑。本轮未接：画像 patch 落盘、长期摘要、math/time 工具（占位）、profile/summary 上下文段。
- ✅ **设备 FS 集成（A，2026-06-11，idf build 验证）**：
  - flash 4MB→8MB；`partitions.csv` 加 `storage,data,littlefs,0x400000,3M`；依赖加 `joltwallet/littlefs`（1.22.1）。
  - `vr_fs.{h,c}`：`esp_vfs_littlefs_register`（首刷格式化）挂 `/littlefs` + `vr_store_set_root`；`app_main` 启动早期调用（非致命）。
  - `vr_store.c`/`vr_runtime.c`/`vr_fs.c` 入 SRCS + `REQUIRES espressif__cjson joltwallet__littlefs`；`idf.py build` 通过（app 54% 余量）。修了 `vr_runtime` 一处 `-Werror=restrict`（`e->id` 与 `e->path` 同结构别名 → 改用 id 副本 + 有界 `append`）。
  - ⏸ 真机后置：挂载/格式化/持久化/磨损/掉电须真机验收（与 WIP §三烧录阻塞并轨）。
- 🔄 **B = P3 端侧云客户端**：
  - ✅ **LLM(Ark) 客户端**（2026-06-11，idf build）：`vr_llm_ark.{h,c}`（`esp_http_client` + TLS 证书包 + cJSON）——组装 system+user prompt 的 chat-completions 请求、POST、解析 `choices[0].message.content`；提供 `vr_llm_ark_callback`（vr_llm_fn 适配，ctx=config）接 runtime。`REQUIRES esp_http_client esp-tls mbedtls`，idf build 通过（54% 余量）。⏸ 真跑需网络 + key（经 push_keys 下发→NVS→config）+ 真机。
  - ⬜ **ASR 2.0 / seed-tts 流式 WS 客户端**：火山 sauc/V3 二进制帧协议，重；**置于真机阶段**实现+联调（盲写不可验、风险高，宜对着真云/真机做）。
- ⬜ 收尾：push_keys→NVS→config 接线（让 runtime 的 LLM 回调拿到下发的 key）；把对话流切到 `vr_runtime`（P6）；画像 patch 落盘、apply_index_snapshot 渲 `.md`、每 20 轮摘要、§4 >6000 预算压缩；端侧大缓冲栈/堆放置。
- ⬜ 真机：多轮对话后记忆文件结构与服务端同构、可被控制平面同步回 Web。

### P5 配网硬化：BLE 配对手机 → 手机 Wi-Fi/热点（真机）— ⬜
- 在 §11.14 `network_provisioning` 基础上：手机（Web Bluetooth 原型先行）经 BLE 下发 SSID/密码，覆盖「连手机热点」场景；设备写 NVS 自动联网，屏幕展示配网状态。
- 验收：真机经手机 BLE 配网后联网成功，进入数据平面直连云。

### P6 端到端联调与切换（真机）— ⬜
- 设备默认走端侧自治；服务端 `/ws` 代理保留为开发/回退路径（可配置）。
- 验收：拔掉自建服务端，设备仍能独立对话；Web 经控制平面仍可连设备、同步记忆、下发 key。

## 四、依赖与顺序

P0 → P1 → P2 可在**无硬件**下连续推进（本轮重点）；P3/P4/P5/P6 需真机，且 P3 依赖 P2 的 key 下发、P4 依赖 P0 的 golden 向量。真机阶段与现有 P0 烧录阻塞（WORK-IN-PROGRESS §三）解除后并轨。

## 五、风险

| 风险 | 应对 |
| --- | --- |
| 编排+记忆两份实现漂移 | 端侧编排做薄 + golden 向量回归闸（P0）；契约写成 spec |
| 固件三段式 TLS+二进制协议工程量大 | 逐段对契约 + host 单测；先 PCM 上行，弱网再切 Opus（§11.16）|
| key 分发安全面扩大 | 经鉴权控制平面下发 + NVS；Web 侧掩码；单列安全边界评审 |
| 端侧 flash 容量/写寿命 | P4 评估，必要时扩分区/挂 SD；摘要可降级为同步时服务端补算 |
| 记忆双向同步覆盖丢数据 | 版本/时间戳 + 方向 + 冲突进审计 |

## 六、本轮（2026-06-11）交付边界

本轮只做**方案讨论 + 文档沉淀**：PRD §十二、ADR 0003、本计划、进展回写。**不进入编码**。下轮从 P0（契约固化，无硬件）起手。
