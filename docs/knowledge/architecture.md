# 系统架构总览

> 当前生效的架构事实。代码结构或链路变化时请同步更新本文件。
> 最后更新：2026-06-29

## 1. 顶层组成

| 组件 | 目录 | 职责 |
| --- | --- | --- |
| 后端服务 | `voice_robot/` | FastAPI 服务，承载 `/ws` 实时语音协议、管理 API、Agent 编排、记忆与指标 |
| 固件 | `firmware/` | ESP32-S3 设备端固件（ESP-IDF）：音频采集/播放、LCD 显示、BLE 网关；设备为“瘦客户端”，模型编排与记忆均在服务端（ADR 0005）|
| Web 控制台 | `web_console/` | 管理与调试前端（`/console`），纯静态资源（`index.html` / `app.js` / `styles.css`） |
| 工具脚本 | `scripts/`、`tools/` | 冒烟测试、假硬件、串口探测、BLE 网关客户端等 |
| 运行态数据 | `runtime/` | 记忆、会话、审计、上传、控制台配置等运行期产物（gitignore） |
| 测试 | `tests/` | pytest 套件，以 `TestClient` 覆盖 HTTP/WS 链路 |

## 2. 后端模块（`voice_robot/`）

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| 服务入口 | `server.py` | FastAPI app、`/ws` WebSocket 端点、会话主循环 |
| 协议定义 | `protocol.py` | `/ws` 客户端/服务端消息的 Pydantic 模型（`extra="forbid"`） |
| 管理 API | `admin_api.py` | `/api/*` 状态、设备、记忆只读、LLM 运行态配置、文本直连等 |
| 配置 | `config.py` / `runtime_config.py` | 根 `.env` 配置与控制台运行态覆盖配置 |
| Agent | `agent/` | `graph.py`（编排）、`state.py`（状态）、`tools.py`（工具）、记忆检索 + 安全拦截 + 本地兜底回复 |
| 语音/模型服务 | `services/` | `asr.py`（火山双向流式）、`tts.py`（V3 单向流式）、`llm.py`、`opus_codec.py` |
| 描红讲解 | `character_trace.py` / `character_trace_narration.py` | 单字描红触发、笔画 payload、本地离线写法讲解模板与通用讲解 |
| 记忆 | `memory/` | 会话 `conversation_store`、画像 `profile_extractor`、摘要 `summary_store`、审计 `audit_log_store`、检索 `context_builder` / `file_retriever` / `file_store` |
| 指标 | `metrics/` | `token_usage_store`、调用上下文 `context.py` |

## 3. 关键链路

### 3.1 实时语音（`/ws`）
1. 客户端 `auth` → `audio_start` → 二进制音频帧 → `audio_end`。
2. 服务端流式 ASR（火山双向流式 WebSocket，主动外连 openspeech；本地 fallback 将 UTF-8 字节当文本）。
3. Agent 编排：记忆检索 → 安全拦截 → LLM（Ark/Doubao，运行态可配 key/model/prompt）或本地兜底。
4. TTS 流式回包：`tts_start` → 音频字节 → `tts_end`。
5. 设备身份：`device_id == "web-console"` 视为控制台，其余视为硬件设备（`_device_kind` 规则）。

### 3.1.1 汉字描红同步播报
1. 服务端在文本输入或 ASR 结果上执行单字描红规则匹配，命中后生成 `show_character_trace` payload。
2. 只有在服务端确认能下发 `show_character_trace` 时，当前轮回复才切换为离线写法讲解；普通问答不会被覆盖。
3. 讲解文本由 `character_trace_narration.py` 本地生成，不依赖外网，当前内置专属模板覆盖 `十`、`象`，其它有笔画数据的字走通用讲解。
4. 当前消息顺序是：`text_input_ack/asr_result` → `llm_prompt` → `llm_reply_*` → `llm_reply` → `tts_start` → `show_character_trace` → TTS 音频 → `tts_end`。

调试入口：
- `GET /api/trace/preview?character=十`：返回该字的描红 payload（Web 预览用）
- `POST /api/devices/{device_id}/trace`：下发描红命令到指定设备（真机联调用）

### 3.2 BLE 移动网关回退
- 无直连网络的设备经手机 BLE 网关接入：`auth` 携带 `transport=ble_mobile_gateway` + `gateway_id`。
- 控制指令（如 `POST /api/devices/{id}/volume`）经同一 WebSocket 下发 `set_volume` 等消息。
- 冒烟：`scripts/gateway_smoke.py`。

### 3.3 固件 WebSocket 连接与重连

- **端点配置化**：`VR_WS_URL` 与可选的 `VR_CLOUD_WS_URL` 从根 `.env` 经烧录脚本写入本地固件配置，仓库不内置私人服务器地址。环境选择持久化在 NVS `vrcfg/ws_env`（0=local, 1=cloud）。
- **自动重连链路**：`esp_websocket_client` 内置重连（每 2s）+ Wi-Fi 恢复时 `IP_EVENT_STA_GOT_IP` 主动踢 stop+start + 60s 无连接自动 reboot 兜底。
- **Wi-Fi 永不放弃**：开机首次连上后（`s_wifi_boot_done`），后续 Wi-Fi 断线无限重试（不再限 8 次）。
- **设置页切换环境**：`voice_ws_set_env()` 只有已连接+已认证时跳过，否则一律执行 stop+set_uri+start 重连。

## 4. 外部依赖
- **LLM**：火山 Ark / Doubao chat completions（运行态配置，未配置则本地兜底）。
- **ASR / TTS**：火山流式服务（`openspeech.bytedance.com`），后端主动外连；详见 [`integrations/`](./integrations/)。

## 5. 运行与验证
- 运行/测试命令见根 [`README.md`](../../README.md)。
- 后端改动需保证 `pytest -q` 通过；前端 JS 改动跑 `node --check web_console/app.js`，并同步 `tests/test_web_console_assets.py`。
