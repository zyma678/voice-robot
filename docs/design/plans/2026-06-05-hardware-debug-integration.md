# 硬件联调整合进对话页 — 实施方案

日期：2026-06-05
范围：把"硬件（ESP32-S3）联调能力"整合进 Web 控制台「对话」页。一次性全做；下发用 HTTP 接口触发；实时监看用 SSE。

## 背景与现状
- 硬件与 Web 控制台共用同一 `/ws`、同一套协议（`protocol.py`）；`device_id == web-console` 视为控制台，其余视为硬件。
- 服务端原本每个 `/ws` 连接完全独立、互不感知；已有进程内在线设备注册表 `_online_connections` 与 `GET /api/devices`。
- 已有按 `user_id` 的审计回看接口 `GET /api/memory/users/{user_id}/audit`（asr/llm_prompt/llm_reply/tts 等事件）。

## 已确认决策
1. 一次性完成"查看设备+回看会话 / 下发文本到硬件播报 / 实时监看硬件会话 / 控制台自测云链路 / 记忆按设备维度"。
2. 下发播报：HTTP 接口触发 `POST /api/devices/{device_id}/say`。
3. 实时监看：SSE `GET /api/devices/{device_id}/events`（独立、不干扰控制台自身 /ws 对话链路）。
4. 界面：对话页顶部设备选择器 + 下发/监看控制。

## 后端改造（voice_robot/）
1. 连接表升级（server.py）：每条连接增加 `websocket` 引用、`user_id`、per-connection 发送队列（anyio memory object stream）。跨连接发送一律投递到目标连接自己的发送队列，由该连接的发送任务消费，保证 anyio 下安全。
2. 事件订阅/广播（server.py）：维护 `device_id → 订阅者集合`。硬件连接的回合事件（asr_result/llm_prompt/llm_reply_*/tts_start/tts_end）在发给硬件本身的同时 fan-out 给订阅者。监看默认只转发文本类事件 + TTS start/end 状态，不转音频字节（控流量）。
3. 定向下发（admin_api.py / server.py）：`POST /api/devices/{device_id}/say` body `{text, user_id?}` → 查目标设备连接 → 复用 `_run_turn` 触发一轮，合成 TTS 推给硬件播报；设备不在线返回 409。
4. 审计带 device 维度（server.py）：auth 记录 device_id；`_audit` payload 带上 device_id（落盘仍按 user_id，不改目录结构，避免破坏现有测试）。
5. 监看流（server.py）：`GET /api/devices/{device_id}/events` 返回 SSE，推送该设备会话事件。

## 前端改造（web_console/）
6. index.html：对话页顶部「历史用户」旁加「硬件设备」下拉 + 「下发播报 / 监看 / 停止监看」按钮。
7. app.js：
   - 选中设备 → 显示在线状态 + 按用户回看审计回合（渲染到逐条日志）。
   - 下发播报 → `POST /api/devices/{id}/say`，气泡/日志标注"已下发到 {device}"。
   - 实时监看 → EventSource 接 SSE，把硬件会话 asr/llm/tts 事件渲染成带"监看"标识的气泡+日志。
   - 控制台自测云链路：保留现有自连 `/ws` 行为不变。

## 验证
- 后端：扩展 pytest（say 接口、SSE 事件、设备在线/离线、跨连接下发）；保证现有测试不破坏。
- 前端：`node --check web_console/app.js`；同步更新 `tests/test_web_console_assets.py`。
- 端到端：脚本模拟一台硬件连 `/ws`，控制台 say 下发能被硬件收到；监看能收到硬件回合事件。

## 风险点
- 跨连接发送/广播是对 `/ws` 分发模型的结构性改造，需小心 anyio 任务/取消语义（本次最大风险）。
- 监看若转发音频字节会有流量压力 → 默认只转发文本类事件 + TTS 状态。

## 实施顺序
1. 后端：连接表升级（发送队列 + websocket/user_id）。
2. 后端：定向下发 `POST /api/devices/{id}/say`。
3. 后端：事件广播 + SSE `GET /api/devices/{id}/events`。
4. 后端：审计带 device_id。
5. 前端：设备选择器 + 下发 + 监看 + 回看。
6. 测试与端到端验证。
