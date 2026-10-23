# ESP32-S3 固件实施方案

日期：2026-06-06（最后更新：2026-06-10）
范围：从零实现 ESP32-S3 端固件，与已就绪的后端 `/ws`/`/api/devices` 协议对接。

> **2026-06-10 进度**：M0/M1 已完成；M2 上行音频、M3 对话模式+VAD、M4 LVGL 中文 UI+触摸、M5 BLE 配网均已落地（编译通过 + host C 单测），M6 Web 调试增强进行中，M7 Opus 可选后置。详见末尾「阶段拆分」里各 M 标注。

## 已确认决策
1. 开发框架：**ESP-IDF**（不用 Arduino，不用 PlatformIO 包装）。
2. 首期范围：**全链路一次上**——Wi-Fi 连云、auth、heartbeat、按键/VAD 触发录音、Opus 上行、收 PCM 下行播放、接收 say/text 下发并播报。
3. 网络：**外网部署**，设备通过公网或内网穿透访问后端。
4. 硬件交互：**LCD + 触摸作为主操作界面**，BOOT 键只做兜底 PTT/重连入口；用户不应依赖串口日志判断设备状态。

## 与本仓库后端的协议复述（已存在，不改）
- 端→云鉴权：`{"type":"auth","device_id":"esp32-XXXX","token":"<auth_token>"}`，禁止多余字段。
- 端→云语音：`audio_start{user_id,codec:"opus"|"pcm_s16le",sample_rate_hz:16000}` → 连续二进制音频帧 → `audio_end`。
- 端→云文本：`text_input{user_id,text}`、`barge_in{reason}`、`heartbeat`。
- 云→端事件：`auth_ok / asr_result / llm_prompt / llm_reply_start|delta|end / llm_reply / tts_start / 二进制 PCM / tts_end{reason,latency_ms}`。
- 云→端定向下发：控制台经 `POST /api/devices/{id}/say` 触发本端一次回合（端侧表现等同收到 text_input 后的 LLM/TTS 回放）。
- 设备身份：`device_id != "web-console"` 即被识别为硬件。

## 硬件外设基线（按官方 BSP）
- 开发板：Waveshare ESP32-S3-Touch-LCD-1.83，ESP32-S3R8，8MB PSRAM，16MB Flash。
- 显示：ST7789(P)，SPI2，RGB565，240x284，SCLK=GPIO6，MOSI=GPIO7，CS=GPIO5，DC=GPIO4，RST=GPIO38，背光=GPIO40(LEDC PWM)。
- 触摸：CST816(S)，I2C SDA=GPIO15，SCL=GPIO14，TP_INT=GPIO13，TP_RST=GPIO39。
- 音频：ES8311 + ES7210，I2S MCLK=GPIO16，BCLK=GPIO9，WS=GPIO45，DOUT=GPIO8，DIN=GPIO10，PA_EN=GPIO46 高电平有效。
- 按键：BOOT=GPIO0，仅作为备用操作入口。
- 重要约束：1.83 寸板官方 BSP **没有 TCA9554/IO expander**，LCD_RST、LCD_BL、TP_RST、PA_EN 都是直连 GPIO，不实现扩展器驱动。
- 组件依赖：优先在 `main/idf_component.yml` 使用 `waveshare/esp32_s3_touch_lcd_1_83`，避免手写 LCD、触摸、codec 引脚初始化。

## 固件目录（新增 firmware/）
```
firmware/
├── CMakeLists.txt
├── sdkconfig.defaults          # 开启 Wi-Fi/Websocket/Opus/PSRAM/LVGL
├── partitions.csv
├── main/
│   ├── CMakeLists.txt
│   ├── app_main.c              # 入口，FreeRTOS 任务编排
│   ├── wifi_client.c/.h        # NVS 读 SSID/PWD，sta 模式连网
│   ├── ws_client.c/.h          # esp_websocket_client 连 /ws，鉴权+心跳
│   ├── audio_capture.c/.h      # I2S 麦克风 16k/16bit 单声道
│   ├── audio_playback.c/.h     # I2S 喇叭播放 PCM 16k
│   ├── opus_codec.c/.h         # libopus 编码（上行）；下行 PCM 直接播
│   ├── vad.c/.h                # 简易能量门限 VAD（webrtc-vad 后续替换）
│   ├── ws_protocol.c/.h        # 序列化/解析 JSON 控制消息
│   ├── display_lcd.c/.h        # LCD/背光初始化，基于 BSP/esp_lcd
│   ├── touch_input.c/.h        # 触摸初始化，点击/长按/滑动事件
│   ├── ui_screen.c/.h          # 屏幕状态机、页面渲染、字幕滚动
│   ├── ui_events.c/.h          # 网络/WS/音频/触摸事件统一投递到 UI task
│   ├── ui_theme.c/.h           # 颜色、字体、图标尺寸、亮度默认值
│   └── ui_button.c/.h          # BOOT 备用按键事件（按下/松开/双击）
└── components/
    ├── opus/                    # esp-opus 组件
    └── waveshare_bsp/           # 或通过 idf_component.yml 拉取官方 BSP
```

## 硬件 UI 设计

### UI 目标
- 不接串口也能判断设备是否可用：启动、联网、鉴权、在线、录音、思考、播放、错误都必须有屏幕反馈。
- 让儿童可以直接使用：默认界面只有一个大操作区，文字短、状态明确，避免复杂菜单。
- 调试友好：错误页展示可读原因和最后一次 log id/错误码摘要，便于和 Web 控制台日志对应。
- 与现有 `/ws` 协议解耦：UI 只消费固件内部事件，不改后端协议。

### 页面与状态
- **启动页**：展示设备名、固件版本、device_id 后 6 位；初始化 LCD/触摸/音频/BSP 失败时进入错误页。
- **联网页**：展示 Wi-Fi 连接中/已连接/失败，显示 SSID、IP、重试状态。
- **云端连接页**：展示 `/ws` 连接、auth、heartbeat 状态；鉴权失败时给出 token/server 配置提示。
- **在线待机页**：主屏常驻大号麦克风按钮，展示用户 ID、云端在线状态、麦克风/VAD 状态和音量条。
- **录音页**：展示录音计时、音量条、结束/取消提示；触摸松开或 VAD 结束后进入思考页。
- **思考页**：展示 ASR 文本摘要和 LLM 生成中状态；收到 `llm_reply_delta` 后开始滚动字幕。
- **播放页**：展示 Bot 回复字幕、播放进度和停止/打断按钮；播放中触摸主按钮立即 `barge_in` 并清空播放队列。
- **错误页/Toast**：WS 断开、鉴权失败、ASR/TTS/音频设备错误都要显示短错误；可点按重连，长按进入设置。
- **设置页**：展示 server url、token 是否已配置、音量、VAD 阈值、屏幕亮度、固件版本；首期只做查看和少量本地可调项，复杂配置仍在 Web 控制台完成。

### 输入交互
- 触摸主按钮：
  - 待机时点按：开始录音，再点按结束。
  - 待机时长按：按住说话，松手结束。
  - 播放中点按：发送 `barge_in{reason:"touch_interrupt"}`，本地立即停止 I2S 输出。
- BOOT 键：
  - 短按：备用 PTT，适合触摸失效时继续使用。
  - 双击：主动重连 WebSocket。
  - 长按：进入设置/诊断页。
- 屏幕手势：
  - 左右滑动：在待机、设置、诊断页之间切换。
  - 错误页点按：执行一次重试。

### 后端事件到 UI 的映射
- `auth_ok`：进入在线待机页，清除鉴权错误。
- `asr_result{text}`：录音页/思考页展示用户文本。
- `llm_prompt{...}`：诊断页记录本轮 prompt 摘要，不在主界面展示完整 prompt。
- `llm_reply_start`：进入思考页，清空上一轮字幕。
- `llm_reply_delta{text}`：增量追加 Bot 字幕。
- `llm_reply_end` / `llm_reply{text}`：固化最终字幕。
- `tts_start`：进入播放页，显示播放状态。
- 二进制 PCM：进入播放缓冲，保持播放页。
- `tts_end{reason}`：`natural_end` 回待机；`barge_in` 显示短提示后进入录音/待机；错误原因进入错误 Toast。
- `error{message}`：进入错误 Toast，并写入诊断页最近错误。

### 技术选型
- 首期推荐使用官方 BSP + LVGL：BSP 负责 ST7789/CST816/ES8311/ES7210/SD 初始化，LVGL 负责按钮、标签、滚动字幕、简单页面栈。
- M0 若要更快验证，可先用 BSP LCD + 轻量自绘状态文本；从 M1 起引入 LVGL，避免后续字幕滚动、设置页和触摸控件重复造轮子。
- 240x284 小屏布局原则：一屏只突出一个主状态；按钮高度不小于 64px；正文最多 3-4 行，长文本滚动或截断。
- UI task 独立运行，所有网络/音频任务通过队列投递事件；UI 渲染不能阻塞 I2S 采集、I2S 播放和 WebSocket 收发。

## 阶段拆分（2026-06-10 修订：对话模式 + 配网 + LVGL，对应 PRD §11.13-11.15）
- **M0 / 上线 + 基础 UI**（已完成）：初始化 LCD → 启动页 → 连 Wi-Fi → 连 `/ws` → auth → heartbeat。控制台「硬件: 1 在线」。
- **M1 / 收 say 下发 + 播放**（已完成）：消费 `llm_reply_* / tts_start / 二进制 PCM / tts_end`，二进制帧写环形缓冲，独立 playback task 写 I2S；ES8311 最小初始化 + `set_volume`。
- **M2 / 上行音频采集 + PCM 上传** ✅（2026-06-10）：`audio_capture.c` 全双工 I2S RX 16k/16bit/mono（DIN GPIO10，与 TX 共用控制器），20ms/帧；`ws_client` 发 `audio_start{codec:"pcm_s16le",sample_rate_hz:16000}` → 二进制帧 → `audio_end`。独立采集任务，禁用时空转。host 单测：依赖 `audio_playback` RX 句柄。
- **M3 / 对话模式状态机 + 能量 VAD（半双工）** ✅（2026-06-10）：`conversation.c` 状态机 OFF↔LISTENING↔THINKING↔PLAYING，点按 toggle 进出；`vad.c` 能量门限（start 600/end 350，3 帧起音，700ms 尾静音断句，120ms pre-roll 补首音）；半双工：PLAYING/THINKING 关麦，`tts_end` 后自动恢复 LISTENING；thinking 看门狗（默认 20s）防卡死；断线/关闭自动发 `barge_in` 并退出。纯逻辑回调注入，host 单测 `test_conversation.c`/`test_vad.c` 覆盖。
- **M4 / LVGL 中文 UI + CST816 触摸** ✅（2026-06-10）：`display_lcd.c` 重构为 LVGL（Source Han Sans SC 16 CJK + Montserrat 14/28）；待机大号圆形对话按钮、CJK 大状态字、ASCII 详情行、循环滚动字幕；`esp_lcd_touch_cst816s` 触摸点击驱动 toggle；`i2c_bus.c` 共享 I2C（codec + 触摸）；DRAM 紧张把播放队列从 128KB 降到 96KB。
- **M5 / 设备端 BLE 配网** ✅（2026-06-10）：`network_provisioning`（BLE，Security1 + PoP `VR_PROV_POP`），启动状态机：force 标志/无凭据→BLE 配网（service `PROV_<device_id>`）；已配网/menuconfig 凭据→直连；BOOT 长按 3s 置 force 标志并重启进配网；配网屏 `VOICE_UI_SCREEN_PROVISIONING`。
- **M6 / Web 调试增强** ✅（2026-06-10）：后端 `POST /api/devices/{id}/conversation` 下发 `set_conversation{enabled}`，固件 `ws_client` 处理远程开关；Web 控制台硬件控制区加「对话模式」按钮（切设备重置本地态）；`tests/test_devices_api.py` 加 3 条端到端用例，`test_web_console_assets.py` 加按钮断言。BLE 网关骨架真机链路待真机解阻塞后验收。
- **M7 / 上行 Opus** ✅（2026-06-10，默认关）：固件 `opus_encoder.c` 用 `78/esp-opus`（libopus 移植）编码 16k/mono/16kbps 20ms 帧，`ws_client` 帧化为 `[u16be len][packet]` 流，`audio_start{codec:"opus_raw"}`；Kconfig `VR_UPLINK_CODEC` 选择（默认 PCM）。后端 `RawOpusDecoder16k`：纯 Python 把裸 Opus 包封 Ogg/Opus 容器 → 复用 ffmpeg 解码（无新增原生依赖），`tests/test_opus_codec.py` 含真 Opus 包 ffmpeg 往返。分区扩到 4MB 容纳编码器（板子 16MB flash）。**默认保持 PCM**：PCM 走流式 ASR 边说边识别、延迟更低、Wi-Fi 带宽充裕；Opus 为弱网/BLE 音频链路准备，一个 menuconfig 开关即切换。
- **全双工打断（分三期，决策见 ADR 0002）**：
  - **0.5 软件验证**（2026-06-10 进行中）：Web 真机模拟放音期不关麦 + 浏览器 `echoCancellation` 充当 AEC，验证 barge-in 状态机/协议；零硬件依赖。
  - **1.0 唤醒词打断**：固件集成 esp-sr WakeNet，放音中靠唤醒词打断；按需扩 flash 8/16MB。
  - **2.0 自由插话**：esp-sr AFE + 数字参考 AEC（用设备手里的 TTS PCM 作远端参考）+ 双讲调优；`conversation.c` 新增 `PLAYING→LISTENING` barge-in 迁移 + 放音起始保护窗 + post-AEC VAD。
  - **无硬件先行切片（2026-06-10，ADR 0002 实施补充）**：1.0/2.0 的**逻辑层**先做并用 host 单测 + 模拟器验证——`conversation.c` 全双工状态机（duplex 配置、保护窗帧、`BARGE_DETECTED` 事件作为 WakeNet 逻辑入口，`Kconfig VR_FULL_DUPLEX` 默认关）；模拟器「打断方式」half/full/wake 三态。**端侧 DSP（WakeNet 检出/AFE 收敛/时延对齐/双讲/阈值）必须真机验收，不用浏览器 AEC 冒充**。esp-sr 组件默认关，仅验证可 build。
- **后续增强**：设置页本地调 VAD 阈值/亮度、Opus 默认化（弱网实测后）。

## 新增/变更文件清单（2026-06-10）
- 固件新增：`audio_capture.c/.h`、`conversation.c/.h`、`vad.c/.h`、`i2c_bus.c/.h`、`opus_encoder.c/.h`。
- 固件改动：`audio_playback.c`（全双工 I2S + RX 句柄，队列 96KB）、`display_lcd.c`（LVGL 重构）、`wifi_client.c`（BLE 配网）、`ws_client.c`（上行 + opus 帧化 + set_conversation）、`es8311_codec.c`（共享 I2C）、`app_main.c`（capture/conv 启动 + BOOT 长/短按）、`ui_state.c/.h`（对话/配网屏 + 事件）、`Kconfig.projbuild`、`CMakeLists.txt`、`idf_component.yml`、`partitions.csv`（4MB）、`sdkconfig.defaults`。
- 固件测试：`test_vad.c`、`test_conversation.c` 新增，`test_ui_state.c` 扩展。
- 后端：`protocol.py`（`opus_raw` codec、`set_conversation`）、`server.py`（conversation 端点 + opus_raw 解码路由）、`services/opus_codec.py`（Ogg muxer + `RawOpusDecoder16k`）、`tests/test_opus_codec.py`、`tests/test_devices_api.py`。
- Web：`web_console/index.html`、`web_console/app.js`（对话模式按钮）、`tests/test_web_console_assets.py`。

> 真机烧录 P0 阻塞（下载握手失败）期间，M2-M5 全部以「编译通过 + host 单测 + fake_hardware 联调」为完成标准，真机验收后置统一回归。

## 关键技术点
1. **音频时钟**：上行 16k/16bit/mono；下行也是 16k PCM s16le。ES8311 必须提供 MCLK(GPIO16)，PA_EN(GPIO46) 高电平有效；优先通过 BSP/esp_codec_dev 打开 codec。
2. **Opus 上行帧大小**：20ms (320 samples)，bitrate 16kbps，complexity 5。
3. **WebSocket**：`esp_websocket_client_t`，启用二进制帧；reconnect_timeout 默认 10s；保活通过应用层 `heartbeat` 而不是 ping。
4. **PSRAM**：开启 OCT-PSRAM；audio buffer/Opus encoder state/LVGL framebuffer/TTS PCM 缓冲放 PSRAM。
5. **device_id**：从 efuse MAC 派生（如 `esp32s3-${mac6}`），固化、唯一。
6. **token**：在 NVS 里持久化，首次烧录通过 menuconfig 或单独的 provisioning 工具写入。
7. **外网部署**：建议在后端前置 nginx/caddy 加 TLS（wss://），固件用 esp_crt_bundle 验签；或先用 WS 走内网穿透。
8. **打断时序**：收到 `tts_end{reason:"barge_in"}` 立刻停播放状态 + 清队列；`audio_start` 之前主动发 `barge_in`。
9. **LCD/触摸初始化顺序**：先初始化共享 I2C，再初始化触摸；LCD 走 SPI2_HOST 24MHz，ST7789 需要开启 invert_color；背光用 LEDC PWM，不要阻塞主流程。
10. **UI 事件队列**：网络、WebSocket、音频采集、音频播放、触摸都只投递事件，`ui_task` 统一状态迁移和渲染，避免跨任务直接操作 LVGL 对象。
11. **中文文本渲染**：准备小字号中文字体子集；长 prompt 不上屏，主界面只显示 ASR 文本和 Bot 回复摘要，完整调试仍看 Web 控制台。
12. **触摸批次差异**：默认 CST816(S)，如果 I2C 扫描或触摸初始化失败，再按具体批次验证 FT3168/FT5x06 路径。

## 测试与验证
- 固件单测：放在 `firmware/test/` 下用 unity 框架（M0/M1/M2 分别有最小回归）。
- 后端仓里加一个**模拟硬件脚本** `scripts/fake_hardware.py`：在固件没就绪前，用 Python 扮演硬件验证下发/监看链路。
- 端到端：真实设备烧录后，控制台「硬件设备」下拉选中→下发播报应能听到设备朗读；监看应实时显示 ASR/LLM/TTS 事件。
- UI 状态机：用模拟事件覆盖 `boot -> wifi -> auth -> idle -> record -> thinking -> playback -> idle`、`playback -> barge_in -> record`、`ws_error -> reconnect -> idle`。
- 真机 UI 验收：断网、token 错误、ASR/TTS 错误、触摸 PTT、BOOT 备用 PTT、播放中打断、亮度/音量调节都要能在屏幕上看到明确反馈。
- 性能验收：播放 PCM 时 UI 动画不能造成 I2S underflow；录音时 UI 刷新不能造成采集丢帧；字幕增量刷新不应阻塞 WebSocket 读写。

## 风险与节奏
- ESP-IDF + I2S + Opus + WS + LCD/触摸 UI 全栈一次写完调通工程量大；建议**严格按 M0→M4** 推进，每完成一阶段在控制台和真机屏幕端到端验证一次。
- M1 当前为了离线构建，优先使用 ESP-IDF 自带 `esp_lcd` / I2S 驱动；若没有引入 Waveshare BSP / `esp_codec_dev`，必须额外完成 ES8311 最小 I2C 初始化，否则可能出现 I2S 有数据但喇叭无声。
- 公网 wss 部署需要域名 + 证书，可以先用内网穿透（如 frp/ngrok）跑通再上 TLS。
- VAD 与打断时序最容易出 bug，预留单独 sprint 调优。
- LVGL/字体/音频缓冲都会吃内存；必须确认 PSRAM 初始化成功并监控 heap watermark。
- UI 状态与真实链路状态容易漂移；以 WebSocket/音频任务事件为唯一状态来源，禁止 UI 自己猜测后端阶段。

## 后续工作建议
1. 等你确认本方案后，**新建 `firmware/` 顶层目录与 M0 骨架**（上线+心跳+基础 LCD 状态页），在控制台看到设备在线且设备屏幕显示在线即可。
2. 不要把固件代码混进 `voice_robot/`（后端）或 `web_console/`（前端）目录，保持端云解耦。
3. 后续加固件 CI（idf.py build 编译验证），不强求烧录验证。
4. 固件开发前先用官方 BSP example 点亮屏幕、读触摸、播放测试音，确认硬件批次与 skill 中的引脚/驱动一致。
