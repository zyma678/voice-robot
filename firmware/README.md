# Voice Robot Firmware — M0–M7

> 最后更新：2026-06-10。里程碑全貌与设计见 [`../docs/design/plans/2026-06-06-esp32-firmware-plan.md`](../docs/design/plans/2026-06-06-esp32-firmware-plan.md)。

ESP-IDF 工程：ESP32-S3 上线后端 `/ws`，鉴权、心跳、收 TTS 播放、采麦上行、本机 LVGL 触摸 UI、对话模式、BLE 配网。

- **M0/M1**：UI 状态机 + LVGL 状态屏；下行 TTS 播放（环形缓冲 + 独立播放任务）+ ES8311；`set_volume`。`VR_BOARD_UI_ENABLE` 控制是否驱动 ST7789 屏。
- **M2 上行**：全双工 I2S（TX 播放 + RX 麦克风共用控制器），`audio_capture.c` 采 16k/16bit/mono 20ms 帧，发 `audio_start{codec}`/二进制帧/`audio_end`。
- **M3 对话模式（半双工）**：`conversation.c` 状态机 + `vad.c` 能量 VAD 自动断句；播放时关麦防回声；BOOT 短按 = 对话开关。
- **M4 LVGL 中文 UI + 触摸**：Source Han Sans CJK + 大号对话按钮 + 滚动字幕；CST816 触摸点按驱动对话开关。
- **M5 BLE 配网**：`network_provisioning`（BLE Security1 + PoP），无凭据/BOOT 长按 3s 进配网（service `PROV_<device_id>`）。
- **M7 上行 Opus（可选）**：`VR_UPLINK_CODEC` 选 Opus 时 `78/esp-opus` 编码裸帧上行（默认 PCM）。

真机烧录受 P0「下载握手失败」阻塞期间，验收以 `idf.py build` + host C 单测 + 后端 `fake_hardware.py`/TestClient 联调为准。

## 构建依赖

- ESP-IDF v5.x（`. ~/esp/esp-idf/export.sh`）
- 系统 `cmake` 和 `ninja`（macOS：`brew install cmake ninja`）

## 配置

推荐先在仓库根目录创建统一配置文件：

```bash
cp .env.example .env
# 编辑 .env 后，烧录脚本会自动读取 Wi-Fi、WebSocket、设备 ID 和共享鉴权 token。
scripts/flash_firmware.sh /dev/cu.usbmodemXXXX
```

也可以通过 menuconfig 单独配置：

```bash
cd firmware
idf.py set-target esp32s3
idf.py menuconfig
# 进入 "Voice Robot M0 Configuration" 子菜单：
#   VR_WIFI_SSID        = 你的 Wi-Fi
#   VR_WIFI_PASSWORD    = Wi-Fi 密码
#   VR_WS_URL           = 后端地址，例如 ws://<电脑局域网IP>:8090/ws
#   VR_DEVICE_ID        = 任意非 "web-console" 字符串，如 esp32-001
#   VR_AUTH_TOKEN       = 与服务端 VOICE_ROBOT_AUTH_TOKEN 一致
#   VR_BOARD_UI_ENABLE  = 是否启用 1.83 寸 ST7789 LCD 状态屏
```

保存退出（`Esc`、`Y` 保存）。

### LCD 状态屏

`VR_BOARD_UI_ENABLE=n` 时，UI 仅输出串口日志，不初始化 LCD，适合先验证联网和 `/ws`。

`VR_BOARD_UI_ENABLE=y` 时，固件使用微雪 ESP32-S3-Touch-LCD-1.83 官方引脚初始化 ST7789：

- SPI2：SCLK=GPIO6，MOSI=GPIO7，CS=GPIO5，DC=GPIO4
- LCD RST=GPIO38，背光=GPIO40
- 分辨率：240x284，RGB565，反色开启

当前 M0 屏幕渲染是状态色块，不依赖 LVGL 和网络下载的 BSP 组件；后续 M1/M2 再补文本、触摸和完整页面。

## 烧录与监视

```bash
idf.py -p /dev/cu.usbmodemXXXX flash monitor
```

预期：

```
I (xxxx) wifi: connecting to ssid="..."
I (xxxx) wifi: got ip: 192.168.x.y
I (xxxx) ws: starting websocket client to ws://...
I (xxxx) ws: connected to ws://...
I (xxxx) ws: → {"type":"auth","device_id":"esp32-001","token":"<redacted>"}
I (xxxx) ws: ← type=auth_ok
I (xxxx) ws: authenticated as device_id=esp32-001
I (xxxx) ui: [idle] Online | Tap to talk |
I (xxxx) ws: → {"type":"heartbeat"}    （每 15 秒一次）
```

退出 monitor：`Ctrl + ]`。

## 后端联调

1. 后端在你电脑上跑（绑 `0.0.0.0:8090` 或局域网 IP）。
2. ESP32 与电脑在**同一 Wi-Fi**。
3. `VR_WS_URL` 填电脑的局域网 IP（`ifconfig | grep 'inet '`），不要用 `127.0.0.1`（那是板子自己）。
4. 烧录后打开 `/console`，「硬件: 1 在线」、对话页「硬件设备」下拉出现 `esp32-001`。

## M1 下行播放现状

- 下行格式：16kHz、16bit、mono、PCM s16le。
- 协议映射：`tts_start` 开始一轮播放，二进制帧承载 PCM 分片，`tts_end` 表示服务端已发送完本轮音频。
- 固件结构：WebSocket 回调只负责把二进制帧放入播放缓冲；实际 `i2s_channel_write` 在独立播放任务中执行，避免阻塞 WebSocket 事件线程。
- 打断：收到 `tts_end(reason="barge_in")` 或本地打断事件时，固件会停止当前播放状态并清空尚未播放的 PCM 缓冲。
- 硬件路径：当前使用 ESP-IDF 自带 I2S 驱动初始化 TX 通道，并按 Waveshare 1.83 寸板引脚配置 MCLK=GPIO16、BCLK=GPIO9、WS=GPIO45、DOUT=GPIO8、PA_EN=GPIO46。
- Codec：已用 ESP-IDF 自带 I2C master API 增加 ES8311 最小初始化，配置为 12.288MHz MCLK、16kHz、16bit、I2S 从机，并设置 DAC 音量与取消静音。

限制：

- 为了保持离线可构建，当前没有通过 ESP-IDF component manager 拉取 Waveshare BSP、`esp_codec_dev` 或 ES8311 官方组件。
- ES8311 初始化序列参考 Espressif 官方 ES8311 组件整理为最小寄存器写入，已通过编译，但仍需要真机确认声音、音量、通道和是否有爆音。
- 现在 I2C bus 由 ES8311 模块创建；后续接入 CST816 触摸/PMU/ES7210 时，需要抽成共享 I2C bus 管理模块。

## 常见问题

- **`Failed to connect ... Wrong boot mode`**：按住 BOOT，按一下 RST，松开 BOOT，重跑 flash。
- **`got ip` 但 ws 连不上**：检查 `VR_WS_URL`（IP 能 ping 通吗？端口对吗？）和电脑防火墙。
- **`auth failed`**：`VR_AUTH_TOKEN` 与后端 `settings.auth_token` 不一致。
- **LCD 无显示但串口 UI 正常**：确认 `VR_BOARD_UI_ENABLE=y`，并确认板子是 Waveshare ESP32-S3-Touch-LCD-1.83；本板没有 TCA9554，LCD_RST/LCD_BL 都是直连 GPIO。
- **LCD 背景/表情正常但文字不显示**：先区分串口 `ui:` 日志和 LCD 实际渲染。若 CJK 字体 `bitmap_format=1/2`，必须启用 `CONFIG_LV_USE_FONT_COMPRESSED=y`；详见 [`../docs/knowledge/esp32-s3-lvgl-cjk-font-runbook.md`](../docs/knowledge/esp32-s3-lvgl-cjk-font-runbook.md)。
- **2MB flash 容量警告**：本工程已用紧凑分区表（`partitions.csv`）+ 关闭 BT/AppTrace。当前 M0 构建后 app 分区仍有较多余量；M2 真上音频时继续关注固件大小。

## 后续阶段

- M1：接收下发播报。订阅 `tts_start` / 二进制 PCM / `tts_end`，将 PCM 写入 ES8311/ES7210 音频链路，并在 LCD 上展示播放状态。
- M2：触摸/BOOT PTT 录音上传。使用官方硬件引脚：I2S MCLK=GPIO16，BCLK=GPIO9，WS=GPIO45，DOUT=GPIO8，DIN=GPIO10，PA_EN=GPIO46；触摸 CST816 走 I2C GPIO15/GPIO14。
