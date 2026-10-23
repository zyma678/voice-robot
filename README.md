# Voice Robot

W1 prototype for an ESP32-S3/Web voice robot backend.

## Configuration

所有服务端、语音服务、固件和部署参数统一从仓库根目录的 `.env` 读取：

```bash
cp .env.example .env
# 编辑 .env，至少设置共享鉴权 token；需要联网语音时再填写 LLM/ASR/TTS key。
```

`.env` 已被 Git 忽略。不要提交真实 Key、Wi-Fi 密码、服务器地址或私人设备信息。

## Run Tests

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

## Run Server

本地默认以硬件联调模式启动 Web 控制台和后端，监听所有网卡：

```bash
scripts/serve.sh
```

常用命令：

```bash
scripts/serve.sh status
scripts/serve.sh stop
scripts/serve.sh restart
scripts/serve.sh logs
```

默认地址：

- Web 控制台：`http://<本机局域网 IP>:8090/console`
- WebSocket：`ws://<本机局域网 IP>:8090/ws`
- 本机健康检查：`http://127.0.0.1:8090/health`
- 日志：`runtime/server.log`
- PID：`runtime/server.pid`

## Deploy with Docker

```bash
cp .env.example .env
# 无 TLS（仅建议首次联调）
docker compose -f deploy/docker-compose.no-tls.yml up -d --build
# 有域名/TLS
docker compose -f deploy/docker-compose.yml up -d --build
```

TLS 模式还需在 `.env` 设置 `VR_DOMAIN`。Docker 与本地服务使用同一份根目录配置。

如果要覆盖监听地址或端口：

```bash
HOST=127.0.0.1 PORT=8089 scripts/serve.sh restart
```

### 硬件联调服务（8090 端口）

硬件设备从局域网连进来时，默认直接用：

```bash
scripts/serve.sh restart
```

说明：

- **`--host 0.0.0.0`**：硬件设备要从局域网连进来，必须绑 `0.0.0.0`（不能 `127.0.0.1`）。
- **`--ws-ping-timeout 180`**：硬件在「收 TTS + 紧接着录音」的连续满负荷期会来不及回 WS Pong，uvicorn 默认 20s ping 超时会误判掉线并关连接（设备侧表现为 `transport_poll_write(0)` → Reconnecting）。设备已有 15s 应用层心跳保活，故放宽 uvicorn 这层 ping 超时；缺省的 20s/20s 不要用。
- 正式联调语音链路时不要加 `--reload`，避免文件变更在隧道建立中途打断进程。
- 第一次发起语音识别时才会按需启动 cloudflared 隧道，并等待边缘就绪（最长约 25 秒），首轮稍慢属正常；之后会复用同一隧道。
- 隧道依赖 `cloudflared` 在 PATH 中（如 `/usr/local/bin/cloudflared`）。

## LLM Configuration

The web console can persist a runtime LLM API key and use it for Ark/Doubao chat completions.

- Console page: `/console`
- Runtime config file: `runtime/config/console_settings.json`
- Default Ark endpoint: `https://ark.cn-beijing.volces.com/api/v3/chat/completions`

If no runtime API key is configured, the server falls back to the local built-in reply logic.

## WebSocket Protocol

Endpoint: `/ws`

Client JSON messages:

- `{"type":"auth","device_id":"web-dev","token":"<your-token>"}`
- `{"type":"audio_start","user_id":"default-user"}`
- `{"type":"audio_end"}`
- `{"type":"heartbeat"}`

Client binary frames are treated as audio chunks. The local fallback ASR decodes UTF-8 bytes as text for development tests.

## Verify

Terminal 1:

```bash
.venv/bin/uvicorn voice_robot.server:app --host 127.0.0.1 --port 8080
```

Terminal 2:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/ws_smoke.py
.venv/bin/python scripts/gateway_smoke.py --base http://127.0.0.1:8080
```

Expected smoke output includes `auth_ok`, `asr_result`, `llm_reply`, `tts_start`, `audio_bytes=...`, and `tts_end`.

`scripts/gateway_smoke.py` is a gateway-only smoke test for the first-stage BLE mobile gateway flow. It authenticates to `/ws` as:

```json
{
  "type": "auth",
  "device_id": "esp32-001",
  "token": "<your-token>",
  "transport": "ble_mobile_gateway",
  "gateway_id": "web-gateway-local"
}
```

Then it calls `POST /api/devices/{device_id}/volume` and verifies that the same websocket receives:

```json
{"type":"set_volume","volume":17}
```
