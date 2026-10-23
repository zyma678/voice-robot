# 0002. 全双工打断（放音中插话）经 AEC 实现，分三期推进

- 状态：已接受
- 日期：2026-06-10
- 相关：PRD §11.9（可打断）/§11.13（对话模式半双工 MVP）、`docs/design/plans/2026-06-06-esp32-firmware-plan.md`（全双工里程碑）、固件 `firmware/main/conversation.c`、Web `web_console/app.js`（真机模拟）

## 背景

当前对话模式是**半双工**：`conversation.c` 在 `TTS_STARTED` 时 `capture_set(false)`、`tts_end` 后才 `enter_listening`。原因是放音时开麦，喇叭声会被麦克风拾取，导致 VAD 自触发、ASR 把机器人自己的话也识别进去。

后端已具备打断能力（VAD start 发 `barge_in` → server 取消当前 turn → 回 `tts_end{reason:barge_in}`，PRD §11.9 要求 200ms 内止播）。缺的只是端侧「放音时还能干净地听用户」——本质障碍是**声学回声**。

## 备选方案

- **A：维持半双工**。优点：无回声问题、已上线。缺点：放音中无法插话，交互生硬。
- **B：唤醒词打断**（esp-sr WakeNet）。放音期间只跑唤醒词检测，检测到才打断。优点：WakeNet 对自身播放鲁棒、几乎不自触发，省去双讲调优，资源小。缺点：只能靠唤醒词打断，不能随口插话。
- **C：AEC 全双工**（esp-sr AFE）。麦克风全程开，AEC 把播放参考信号从麦克风输入里减掉，VAD/ASR 只看消回声后的近端语音。
  - 参考信号两种来源：(C1) ES7210 硬件回采通道；(C2) **软件数字参考**——TTS 的 PCM 是设备自己写进 I2S 的，手里已有完美远端参考，把「麦克风近端 + 刚播放的 PCM 参考」交织喂给 AFE 即可，省掉硬件 loopback。
  - 优点：真正随口插话。缺点：需时延对齐（写 I2S → 麦克风听到的固定延迟），双讲/残余回声调优成本高；esp-sr 模型占 flash/PSRAM。

## 决策

**目标采用 C（AEC 全双工），参考信号优先用 C2 软件数字参考**；但**分三期推进**，先用低成本路径验证逻辑与协议，再逐步上硬件 AEC：

1. **全双工 0.5（软件验证）**：Web「真机模拟」放音期不关麦 + 浏览器自带 `echoCancellation` 充当 AEC，验证 barge-in 状态机/协议。**本期先做。**
2. **全双工 1.0（唤醒词打断，方案 B）**：固件集成 esp-sr WakeNet，放音中靠唤醒词打断；按需扩 flash 到 8/16MB。
3. **全双工 2.0（自由插话，方案 C）**：esp-sr AFE + 数字参考 AEC + 双讲调优，真正随口打断。

状态机改动（2.0）：`PLAYING` 保持采集但喂 AFE；新增 `PLAYING → LISTENING` barge-in 迁移（停本地 I2S + 清队列 + 发 `barge_in` + `audio_start`）；放音起始加 ~200ms 保护窗避开 AEC 收敛瞬态；消回声后过一道较高阈值 post-AEC VAD 抗残余回声/双讲。

## 后果

- 正面：交互可随口打断，体验接近自然对话；后端协议无需改（打断链路已就绪）；第 1 期零硬件依赖即可验证。
- 负面 / 代价：2.0 需要 esp-sr（flash 8/16MB + PSRAM）、AEC 时延对齐与双讲调优是主要工程量与风险点；中间态 1.0 只能唤醒词打断。
- 后续关注：第 1 期在浏览器验证的阈值/时序，迁到真机 AEC 时需重新标定；保护窗与 post-AEC VAD 阈值需真机实测；flash 分区扩容需与烧录流程同步。

## 实施补充（2026-06-10）：2/3 期无硬件可开发的边界

经评估，2/3 期可在**无真机**下先开发并验证「逻辑层」，但**端侧 DSP 本体必须真机验收**——不得用模拟器的浏览器 AEC 冒充 esp-sr 通过的证据。

- **可无硬件开发 + 验证**（host C 单测 + 模拟器交互）：
  - `conversation.c` 全双工状态机：`PLAYING` 态保持采集、保护窗、`PLAYING→LISTENING` barge-in 迁移、外部 `BARGE_DETECTED` 事件（WakeNet 的逻辑入口）。检测源对状态机是**信号无关**的——host 注入帧/事件即可测。
  - 模拟器交互模型：「打断方式」半双工 / 全双工（浏览器 AEC）/ 唤醒词（按钮模拟 WakeNet）三态，验证「全双工随口打断」与「唤醒词模式下任意说话不打断、仅唤醒词打断」的差异。
- **必须真机（不假装验证）**：WakeNet 检出率 / 误触发（自唤醒）/ CPU 占用；esp-sr AFE 回声消除收敛、I2S↔麦克风时延对齐、双讲、残余回声阈值标定。
- **esp-sr 组件**：默认关（`CONFIG_VR_FULL_DUPLEX=n`），仅在有真机时启用并标定；运行态 DSP 一律真机验收。固件默认仍走已验证的半双工路径。

### esp-sr 编译接入：决定**推迟到真机阶段**（2026-06-10）

原计划「编译期验证 esp-sr 能 build」。评估后推迟，理由：
- esp-sr 需要专用 `model` 分区 + menuconfig 选模型（WakeNet/AFE）+ 构建期生成并烧录 `srmodels.bin`，且模型下载体量大；这些都只有**对着真机**才有意义，也只有真机能验证（模型选型直接影响 flash 布局与检出效果）。
- 现在拉入只换来「能编译」，却要动分区、引入大依赖与易碎的模型生成步骤，把当前已绿的构建置于风险中——与本仓刚确立的「不假装验证」原则相悖。
- **逻辑接入点已就绪且可测**：`conversation.c` 的 `VOICE_CONV_EVENT_BARGE_DETECTED` 即 WakeNet 的逻辑入口，真机阶段加 esp-sr 时只需在一个检测任务里命中唤醒词后调用 `voice_conversation_hw_post(VOICE_CONV_EVENT_BARGE_DETECTED)`，改动局部。
- 真机阶段任务清单：加 `espressif/esp-sr` 依赖 → 分区表加 `model` 分区、flash 扩 8/16MB → menuconfig 选 WakeNet 模型 → 新增 `wake_detector` 任务喂麦克风帧给 WakeNet、命中即 post BARGE_DETECTED → `idf.py build` + 真机检出/误触发标定。
