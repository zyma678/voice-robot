# 0006. 生产部署：火山引擎 ECS + Docker Compose + Caddy 自动 TLS（WSS）

- 状态：已接受
- 日期：2026-06-12
- 相关：ADR `0005`（服务端权威 + 瘦客户端）；部署产物 `deploy/`；runbook `docs/knowledge/volcengine-deploy-runbook.md`；`voice_robot/server.py`（`/ws`）、`firmware/main/ws_client.c`

## 背景

ADR 0005 把架构定为「服务端权威 + 硬件瘦客户端」，并要求**服务端发布到字节火山引擎供外网访问**，让家里 Wi-Fi 上的 ESP32 经公网连 `/ws`。需要选一个对外可达、支持 WebSocket 长连接、能加密的部署形态。

关键约束：
- 服务用 **WebSocket `/ws`** 长连接 + 流式音频 → serverless/veFaaS（请求-响应、连接时长受限）不适配。
- 服务**主动外连** openspeech.bytedance.com（火山 ASR/TTS，WSS）与 ark.cn-beijing.volces.com（LLM）；**不需要**对外提供文件拉取（旧的 cloudflared 隧道 / `public_base_url` 在当前流式链路已是死代码）。
- ESP32 用 `wss://` 需要校验服务器证书。

## 备选方案

- **A：ECS + Docker Compose + Caddy 自动 TLS（本 ADR 选定）**。单台 Linux VM 跑 app 容器（uvicorn）+ Caddy 容器（ACME 自动签 Let's Encrypt 证书、反代 `/ws` 与 `/console`）。优点：WS 原生支持、可控、可复现、TLS 零手工；缺点：单点（原型够用）。
- **B：veFaaS / 函数服务**。WS 长连接 + 流式不适配，否决。
- **C：VKE（Kubernetes）**。可扩展，但对单服务原型过重。
- **D：内部 TCE/FaaS**。内网为主，ESP32 在公网外不可直达，需额外暴露，绕远。

## 决策

采用 **A**，并锁定：

1. **计算**：火山引擎 ECS（地域取 cn-beijing，近 Ark/openspeech），Ubuntu 22.04，公网 IP，安全组放行 22/80/443。
2. **运行**：`deploy/docker-compose.yml` 起两个容器——`app`（`deploy/Dockerfile`，run-from-source，`uvicorn voice_robot.server:app:8090`，仅内网暴露）+ `caddy`（`deploy/Caddyfile`，反代 `app:8090`，对外 80/443）。
3. **对外/加密**：绑定域名（A 记录 → ECS 公网 IP），Caddy 自动签发/续期 TLS；固件连 `wss://<域名>/ws`。
4. **关闭未用项**：`VOICE_ROBOT_AUTO_PUBLIC_TUNNEL=false`（cloudflared 死代码，prod 不跑）。
5. **密钥**：服务端、ASR/TTS、固件与部署参数统一从根 `.env`（gitignore）读取；部署后仍可经 `/console「模型配置」`覆盖模型与语音配置，覆盖值落在持久卷 `runtime/config/console_settings.json`。
6. **持久化**：命名卷 `vr-runtime` 挂 `/app/runtime`（记忆 + 控制台配置）；`caddy-data` 持久化 ACME 账户与证书（避免重启重签触发限流）。
7. **固件**：`ws_client.c` 加 `esp_crt_bundle_attach`（校验 Let's Encrypt 链，含 ISRG Root），`CONFIG_MBEDTLS_CERTIFICATE_BUNDLE=y`；`ws://` 时该字段不生效，可常开。

## 后果

- 正面：对外加密可达、WS 原生、一条 `docker compose up -d` 可复现；证书自动化；密钥不入库。
- 负面 / 代价：单 ECS 单点（原型可接受，后续可上 VKE / 负载均衡 + 多副本，但 app 在进程内持有设备注册表/连接态，水平扩展需先抽出共享状态）；ECS 与公网带宽有成本。
- 后续关注：
  - app **单 worker**：连接态在模块全局，扩多 worker/副本前要把设备注册表外置（Redis 等）。
  - 域名/证书续期监控；安全组最小化；`AUTH_TOKEN` 强随机。
  - 真机 `wss://` 首次握手时延与 ESP32 TLS 内存占用，真机标定。
