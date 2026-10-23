# 火山引擎 ECS 部署 Runbook（服务端公网 WSS）

最后更新：2026-06-21

> 决策 ADR `decisions/0006-production-deploy-ecs-caddy-wss.md`；部署产物 `deploy/`。
> 目标：把 `voice_robot` 服务部署到火山引擎 ECS，经域名 + Caddy 自动 TLS 对外提供 `wss://<域名>/ws` 与 `https://<域名>/console`，让公网上的 ESP32 直连。

## 架构

```
ESP32 / 浏览器 ──wss/https──> [ECS:443 Caddy(自动TLS)] ──http──> [app:8090 uvicorn]
                                                                    │
                              app 主动外连 ─> openspeech.bytedance.com (ASR/TTS, wss)
                                            └> ark.cn-beijing.volces.com (LLM)
```

只对外开 80/443（Caddy）；app 仅容器内网可达。无需 cloudflared（流式链路不拉取文件）。

## 无域名先验证（临时，明文 ws://）

域名/TLS 没就绪前，用 `deploy/docker-compose.no-tls.yml`：不跑 Caddy，把 app 直接暴露在公网 **80** 端口，明文 HTTP + `ws://`。

```bash
cd /opt/voice-robot
cp .env.example .env
vi .env   # 填 VOICE_ROBOT_AUTH_TOKEN（与固件一致）、VOICE_ROBOT_LLM_API_KEY；VR_DOMAIN 可留空
docker compose -f deploy/docker-compose.no-tls.yml up -d --build
docker compose -f deploy/docker-compose.no-tls.yml logs -f app
```

- 控制台：`http://<公网IP>/console`
- ESP32：`Backend WebSocket URL = ws://<公网IP>/ws`（端口 80，明文）
- ⚠️ **浏览器麦克风在 `http://<IP>` 下不可用**（非安全上下文）。真机 ESP32 不受影响；想用浏览器「真机模拟」页测麦克风，SSH 端口转发到本地即可（localhost 是安全上下文）：
  ```bash
  ssh -L 8090:localhost:80 root@<公网IP>     # 然后开 http://localhost:8090/console
  ```

域名就绪后切正式 TLS：见下方 §1–§10（用 `docker-compose.yml` + Caddy），固件改回 `wss://<域名>/ws`。

## 你需要先准备（账号级，需你操作）

- 火山引擎账号（已实名）+ 余额/代金券。
- 一个域名（可在火山或别处注册），能加 A 记录。

## 步骤

### 1. 开 ECS

- 控制台 → 云服务器 ECS → 创建实例。
- 地域：**华北2(北京) cn-beijing**（近 Ark / openspeech，时延低）。
- 镜像：Ubuntu 22.04 LTS；规格：2vCPU/2–4GB 起（原型够用）；系统盘 40GB。
- 网络：分配**公网 IP**（或绑弹性公网 IP），带宽按量/固定按需。
- 设置 root 密码或 SSH 密钥。

### 2. 安全组放行

ECS → 安全组 → 入方向规则，放行：`22`(SSH)、`80`(ACME/HTTP)、`443`(HTTPS/WSS)。其余默认拒绝。

### 3. 域名解析

到域名 DNS 处加一条 **A 记录**：`bot.<你的域名>` → ECS 公网 IP。等生效（`dig bot.<域名>` 能解析到该 IP）。

### 4. 装 Docker

SSH 登录 ECS 后（建议在会话里用 `! ssh root@<IP>`）：

```bash
curl -fsSL https://get.docker.com | sh
docker version && docker compose version   # compose v2 随 docker 安装
```

### 5. 拿代码上去

```bash
# 方式 A：git（仓库可达时）
git clone <仓库地址> /opt/voice-robot && cd /opt/voice-robot
# 方式 B：本地 scp（无 git 时，在本机执行）
#   scp -r <repo-root> root@<IP>:/opt/voice-robot
```

### 6. 配 .env

```bash
cd /opt/voice-robot
cp .env.example .env
vi .env   # 填：
#   VR_DOMAIN=bot.<你的域名>
#   VOICE_ROBOT_AUTH_TOKEN=<强随机串，须与固件 CONFIG_VR_AUTH_TOKEN 一致>
#   VOICE_ROBOT_LLM_API_KEY=<Ark key，或留空稍后在 /console 配>
```

### 7. 起服务

```bash
cd /opt/voice-robot
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml logs -f caddy
docker compose -f deploy/docker-compose.yml logs -f app
```

### 8. 配语音 key（ASR/TTS）

可直接在根 `.env` 填 ASR/TTS 的 `api_key` / `resource_id`，也可在浏览器控制台「模型配置」中设置；控制台保存值优先于环境变量并持久化到运行卷。

### 9. 验证

- `https://bot.<域名>/console` 打得开、证书有效（锁标）。
- 「真机模拟」页以硬件 device_id 接入（页面会用 `wss://<域名>/ws`），跑通一轮 ASR→模型→TTS。
- 或本机烟测：`python scripts/ws_smoke.py`（把脚本里的地址改成 `wss://bot.<域名>/ws`，token 用 .env 里的值）。

### 10. 固件指向公网

`firmware` → `idf.py menuconfig` → Voice Robot M0 Configuration：
- `Backend WebSocket URL` = `wss://bot.<域名>/ws`
- `Auth Token` = 与 `.env` 的 `VOICE_ROBOT_AUTH_TOKEN` 一致

重新烧录 `build_revert`（固件已支持 wss：`ws_client.c` 挂了 CA bundle，`CONFIG_MBEDTLS_CERTIFICATE_BUNDLE=y`）。

## 运维

- 更新：`git pull && docker compose up -d --build`（app 容器重建；记忆/配置在卷里不丢）。
- 日志：`docker compose logs -f app`。
- 备份：`docker run --rm -v deploy_vr-runtime:/v -v $PWD:/b busybox tar czf /b/runtime-backup.tgz -C /v .`。
- 重启续证：`caddy-data` 卷已持久化 ACME 账户/证书，正常重启不会重签（避免 Let's Encrypt 限流）。

## 故障排查

### 磁盘撑满导致全面 500（2026-06-21 实例）

**症状**：固件报 `speech turn failed: Expecting value: line XXXX`；所有 `/api/*` 返回 `Internal Server Error`。

**根因**：`conversations/<user>/turns.jsonl.*.bak` 备份文件无上限增长（每次对话摘要都产生 ~98MB 备份），325 个文件共 13GB 撑满 20GB 磁盘。磁盘满时 `console_settings.json` 写入被截断变成非法 JSON，所有读取运行时配置的 API 返回 500。

**排查与修复步骤**：

```bash
# 1. 确认磁盘
ssh root@<HOST> "df -h /"
# 如果 Use% = 100%：

# 2. 定位大文件
du -sh /var/lib/docker/volumes/deploy_vr-runtime/_data/memory/conversations/
find /var/lib/docker/volumes/deploy_vr-runtime/_data/memory/conversations/ -name '*.bak' | wc -l

# 3. 清理备份（不影响当前 turns.jsonl）
find /var/lib/docker/volumes/deploy_vr-runtime/_data/memory/conversations/ -name '*.bak' -delete

# 4. 检查 console_settings.json 是否损坏
python3 -c "import json; json.loads(open('/var/lib/docker/volumes/deploy_vr-runtime/_data/config/console_settings.json').read())"
# 如果报 JSONDecodeError：用 Python 找最后一个有效 }/]，计算未闭合括号数，追加关闭符修复

# 5. 重启
cd /opt/voice-robot && docker compose -f deploy/docker-compose.yml restart app

# 6. 验证
curl -s https://<YOUR_DOMAIN>/api/status | python3 -m json.tool
```

**预防**：TODO — 后端代码应限制 `.bak` 文件数量或定期清理。

## 注意 / 已知约束

- **单 worker / 单实例**：app 在进程内持有设备注册表与连接态，暂不可水平扩展（ADR 0006 后果）。要扩容先把注册表外置。
- ASR/TTS 语音 key 可走根 `.env`，也可由 `/console` 运行态覆盖。
- 安全组务必最小化；`AUTH_TOKEN` 用强随机；根 `.env` 不入库（已 gitignore）。
- **磁盘容量**：20GB 系统盘在 conversations 备份文件增长下可能不够，定期检查 `df -h /` 并清理 `.bak`。
