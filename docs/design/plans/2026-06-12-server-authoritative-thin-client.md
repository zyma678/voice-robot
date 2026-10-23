# 回退端侧自治 → 服务端权威 + 硬件瘦客户端：实施计划

最后更新：2026-06-12

> 需求来源：本轮对话方向调整（PRD §十二已弃用标注）。架构取舍：ADR `docs/knowledge/decisions/0005-revert-to-server-authoritative-thin-client.md`（supersedes 0003/0004）。
> 跨模块、改架构、分期推进；每期标注状态与验收口径。先文档（本计划）→ 再实现 → 完成回写进展。

## 背景与目标

6/11 的「硬件端侧自治」把 ASR→LLM→TTS 三段式、记忆、模型调度移植成 C 跑在固件里、直连火山云（`vr_*` 11 模块 + `vr_llm_ark`）。本轮回退：

- **硬件端只做复杂交互 + 语音输入输出**（采音上行 / 放 TTS / UI / 配网）。
- **模型调度与记忆管理回到服务端单一实现**；服务端后续发布字节云供外网访问，数据全存服务端。
- 删除硬件端闭环直链火山的代码。
- 开发先用 Web「真机模拟」跑通流畅闭环，最后烧录真机验收。

关键事实：6/10 瘦客户端通路（`conversation.c`+`ws_client.c` 走 `/ws` 音频）一直完整；`vr_*` 自治模块从未接入实时通路。故回退 = 删除「在场未接线」的自治代码。本仓库非 git，外科式删除。

## 分期

### Phase 0 — 文档先行 ✅（2026-06-12）
- ADR `0005` 新增（supersedes 0003/0004）；0003/0004 标记被取代。
- 旧 plan/spec/progress/PRD §12 加弃用标注（保留作历史）。
- 本计划新增。

### Phase 1 — 固件回退（删端侧闭环直链火山的代码）
- 硬删除 `firmware/main/vr_{routing,retrieval,context,prompt,markdown,extract,paths,store,runtime,fs,llm_ark}.{c,h}` 与 `firmware/test/test_vr_*.c`。
- `CMakeLists.txt`：删 `vr_*.c` 与仅自治用依赖（cjson/littlefs/http_client/esp-tls/mbedtls，按 build 结果定 TLS 是否保留）。
- `app_main.c`：删 `vr_fs.h` 与 `vr_fs_init()`。`idf_component.yml`：删 littlefs。`partitions.csv`：删 `storage` 分区（app 保留 4MB 供 Opus 编码器）。`run_host_tests.sh`：移除 `test_vr_*`。
- 保留瘦客户端：`conversation.c` `ws_client.c` `audio_*` `vad` `display_lcd`(LVGL) `ui_*` `wifi_client` `ble_gateway` `opus_encoder` `network_provisioning`。
- 验收：`idf.py build` 通过；`run_host_tests.sh` 剩余用例全过。

### Phase 2 — 服务端清理（删自治支撑，保权威链路）
- 硬删 `voice_robot/device_runtime.py`、`device_sync.py`、`tests/test_device_runtime.py`、`tests/test_device_sync.py`。
- 摘除端点/协议：`server.py` 的 push-keys / memory/sync 端点 + ws 命令分发 + memory_report 处理；`admin_api.py` 的 `/autonomous-turn`；`protocol.py` 删 `push_keys`/`memory_sync`/`memory_report_ack` 与 `ClientMemoryReportMessage`。
- 保留 `agent/routing.py`（零行为变更抽取）+ 服务端 ASR/LLM/TTS/记忆/编排全链路。
- 验收：`pytest -q` 全绿。

### Phase 3 — Web 模拟器回退
- `web_console/{index.html,app.js}` 删「自治回合」按钮与 memory_sync/push_keys 演示流；保留瘦客户端真机模拟回环。
- 更新 `tests/test_web_console_assets.py`；`node --check web_console/app.js` 通过。

### Phase 4 — 模拟器跑通流畅闭环（核心体验验收）
- Web 真机模拟页端到端跑通 ASR→服务端（记忆+模型）→TTS，关注流畅度；`tests/test_device_simulator_loop.py` 通过。

### Phase 5 — 后续阶段（本轮不做）
- BLE 配对 → web 设 WiFi 配网全链路核对（`network_provisioning` 已在，待真机标定）。
- 服务端发布字节云供外网访问（评估 `services/public_tunnel.py` 或正式部署），回填固件 `ws_url`。
- 真机烧录验收（先解 WORK-IN-PROGRESS §三「下载握手失败」阻塞）。

## 验证总览

| 层 | 命令 | 通过标准 |
| --- | --- | --- |
| 后端 | `pytest -q` | 全绿 |
| 前端 | `node --check web_console/app.js` + `test_web_console_assets.py` | 通过 |
| 端到端 | `tests/test_device_simulator_loop.py` | 瘦客户端语音 loop 通过 |
| 固件编译 | `idf.py build` | 通过（无 vr_*/littlefs/ark 依赖） |
| 固件单测 | `firmware/test/run_host_tests.sh` | 剩余用例全过 |
| 真机 | 烧录验收 | 后续阶段 |
