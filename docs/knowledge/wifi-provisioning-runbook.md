# 设备端 Wi-Fi 配网 Runbook（蓝牙配网 + 凭据下发）

最后更新：2026-06-12

> 需求 PRD §11.14（设备端 Wi-Fi 配网）；固件实现 `firmware/main/wifi_client.c`。
> 本文是**当前生效的操作手册**：如何让 ESP32-S3 不重新烧录就换 Wi-Fi。

## 一、完成度（截至 2026-06-12）

| 部分 | 状态 |
| --- | --- |
| 设备端配网固件（BLE + Security1 + PoP + NVS 持久化 + 强制重配网 + 配网 UI 屏） | ✅ 代码完成、`idf.py build` 通过 |
| 配网状态屏（「手机配网中」） | ✅ 完成（`ui_state.c` / `display_lcd.c`） |
| **自有 Web 配网页（浏览器下发 SSID/密码）** | ✅ 代码完成、逻辑单测通过（见 §六），真机待验 |
| 真机验证 | ⏳ 未做（受烧录阻塞，见 `docs/progress/WORK-IN-PROGRESS.md` §三） |

> 两条配网路径都可用：自有 Web 配网页（§六，推荐）或乐鑫官方客户端（§四，已验证的通用回退）。
> ⚠️ 固件现已**pin 固定配网服务 UUID**（Web Bluetooth 必需），旧固件需重新烧录 `build_revert` 才能被 Web 配网页发现。

## 二、技术方案

- **协议**：ESP-IDF `network_provisioning` 组件，BLE 传输，安全等级 **Security1**（X25519 密钥协商 + AES-CTR + protobuf）+ **PoP**（proof-of-possession，防中间人）。
- **BLE 设备名**：`PROV_<device_id>`，如 `PROV_esp32-001`。
- **PoP**：`vr-prov-1234`（`CONFIG_VR_PROV_POP` 默认值，可在 menuconfig 改）。
- **凭据落地**：配网成功 → SSID/密码写 NVS → 自动连网 → 拿 IP → 正常走 `/ws`。断电重启复用 NVS 凭据，无需再配。

## 三、何时进入配网模式

启动逻辑见 `wifi_client.c:154-169`：

1. **无可用凭据**：`CONFIG_VR_WIFI_SSID` 为空 **且** NVS 无已存凭据 → 进配网。
2. **强制重配网**：BOOT 键长按 ≥3s（`app_main.c`）→ 置 NVS `force_prov` 标志 → 重启进配网。
3. 否则用已存/menuconfig 凭据直连；连不上会重试，超阈值转 BLE 手机网关兜底（与配网互斥的另一条 BLE 路径）。

> 默认 `CONFIG_VR_WIFI_SSID` 为空，首次启动会进入 BLE 配网；也可以在根 `.env` 预先填写 Wi-Fi 后通过烧录脚本写入本地配置。

## 四、操作步骤（当前可用路径）

### 步骤 1 — 烧录「空 SSID」固件

```bash
source /esp/esp-idf/export.sh
cd <repo-root>/firmware
idf.py -B build_revert menuconfig    # Voice Robot M0 Configuration:
                                     #   Wi-Fi SSID = （清空）
                                     #   Backend WebSocket URL = ws://<服务端IP>:8090/ws
                                     #   Device ID = esp32-001
idf.py -B build_revert flash monitor
```

设备上电后屏显「手机配网中」，BLE 广播名 `PROV_esp32-001`。

### 步骤 2 — 下发 Wi-Fi 凭据（二选一）

**A. 手机 App**：装乐鑫官方「ESP BLE Provisioning」（iOS/Android）→ 扫 BLE → 选 `PROV_esp32-001` → 输入 PoP `vr-prov-1234` → 选 Wi-Fi、填密码 → 下发。

**B. 命令行 `esp-prov`**：
```bash
python $IDF_PATH/tools/esp_prov/esp_prov.py \
  --transport ble --service_name PROV_esp32-001 \
  --sec_ver 1 --pop vr-prov-1234 \
  --ssid "你的WiFi名" --passphrase "你的WiFi密码"
```

### 步骤 3 — 验证

串口 `monitor` 出现 `got ip: <IP>`，屏幕转正常状态。凭据已存 NVS，断电重启免配。

### 换网 / 重配

BOOT 键长按 ≥3s → 设备重启回配网模式 → 重复步骤 2。

## 五、故障排查

- **扫不到 `PROV_*`**：确认固件 SSID 已置空（否则不进配网）；确认屏显「手机配网中」；BLE 距离 / 浏览器或 App 蓝牙权限。
- **配网失败 / 鉴权错**：PoP 必须是 `vr-prov-1234`；安全等级必须 Security1（`--sec_ver 1`）。
- **下发成功但连不上**：检查 Wi-Fi 名/密码、是否 2.4GHz（ESP32-S3 不支持 5GHz）、路由器认证方式（WPA2）。
- **拿到 IP 但连不上后端**：核对 `Backend WebSocket URL` 对 ESP32 所在网络可达（参考 WIP §三）。

## 六、自有 Web 配网页（已实现，推荐路径）

浏览器（Web Bluetooth）直连 `PROV_<device_id>`，按 protocomm Security1 握手并下发凭据，兑现「蓝牙配对后通过 web 端设置 wifi」，无需装 App / 跑 CLI。

**实现**：
- 客户端 `web_console/provisioning.js`（纯前端、无依赖）：RFC-7748 X25519 + AES-256-CTR（与 mbedtls 一致的连续计数器/nc_off）+ SHA256(PoP) + 手写 protobuf；端点经 0x2901 描述符发现。
- UI：控制台「设备配网」Tab（`web_console/index.html`）。
- 固件：`wifi_client.c` pin 固定服务 UUID `021a9004-0382-4aea-bff4-6b3f1c5adfb4`（与 `provisioning.js` 的 `PROV_SERVICE_UUID` 一致）。
- 测试：`web_console/provisioning.test.js`（`node web_console/provisioning.test.js`）——X25519 RFC 向量、AES-CTR NIST KAT + 跨消息连续性、protobuf 往返、**对模拟设备跑通完整 Security1 握手 + 加密配置交换 + 错误 PoP 拒绝**。BLE 传输层为唯一未测层（真机门控）。

**操作**：
1. 烧录「空 SSID」固件让设备进配网（同 §四步骤 1）。
2. 用 **Chrome / Edge** 经 `http://localhost:8090/console` 或 HTTPS 打开控制台 → 「设备配网」Tab。
3. 填 SSID / 密码（PoP 默认 `vr-prov-1234`）→ 点「连接并配网」→ 浏览器弹窗选 `PROV_esp32-001` → 配网日志显示握手、下发、连接结果。

> 限制：Web Bluetooth 仅 Chromium 系浏览器支持，且页面须 `localhost` 或 HTTPS（安全上下文）。
> 真机联调待解 §五/WIP §三 的烧录阻塞后进行。
