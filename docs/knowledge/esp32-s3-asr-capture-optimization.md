# ESP32-S3 语音输入（采集 → ASR）优化知识库

> 适用：ESP32-S3 + ES7210 4 通道 ADC（麦克风）+ ES8311 codec（扬声器）+ I2S 全双工 + 火山 ASR（16kHz/16bit/mono PCM 流式上行）
> 板子：Waveshare ESP32-S3-Touch-LCD-1.83
> 对应代码：`firmware/main/es7210_codec.{c,h}`、`firmware/main/audio_capture.c`、`firmware/main/conversation.c`、`firmware/main/vad.c`、`firmware/main/ws_client.c`、`voice_robot/server.py`
> 实测时间：2026-06-13
> 配套文档：播放侧见 [`esp32-s3-audio-optimization.md`](./esp32-s3-audio-optimization.md)

把一句"对着设备说话→设备听懂→回复"打通，踩了 5 个独立的坑。**最大的教训：先把"上行音频"录下来离线分析，再动参数**——前期一直误以为是增益/信噪比问题、反复调增益和 VAD 阈值都无效，最后把上行 PCM 存成 wav 一看频谱，10 秒就锁定真因是 I2S 声道交错。

---

## 一、问题总览与根因

| 症状 | 根因 | 修复点 |
|------|------|--------|
| 说话完全无采集、VAD 不触发、服务端收不到 `audio_start` | 本板麦克风**不在 ES8311 上**，而接在独立 **ES7210 4 通道 ADC**（I2C 0x40），固件从未初始化它 → I2S RX 读到静音 | 新增 `es7210_codec` 驱动，ES8311 init 后调用 |
| 有采集但电平极低、VAD 够不到阈值 | ES7210 模拟 PGA 增益设了 `0x0a`(+30dB) 但**实际没生效**（读回 REG43 确认）→ 信号贴着 ADC 噪底 | PGA 拉满 `0x0e`(+37.5dB)；读回寄存器验证生效 |
| **ASR 始终识别为空**（电平/SNR 都正常也没用） | **I2S RX 双声道交错**：上行是 `[MIC,0,MIC,0,...]`（左=MIC1、右=静音），`slot_mode=MONO` 在全双工下未真正生效 | `audio_capture.c` 按双声道读、**只取左声道**（偶索引）de-interleave |
| ASR 准了但回复播不出/被打断 | 半双工声学自激：收到服务端 `tts_end`（仅"音频发完"）就立即开麦，扬声器播放队列+DMA 尾音还在响 → 拾到自己的声音触发 barge_in | `TTS_FINISHED` 改由**播放队列真正排空 + DMA 尾音放完**才触发 |
| 回复听不到 | 默认音量 25 在真机上太小 | 默认音量 25→80（运行时仍可 `set_volume`） |

---

## 二、调试方法论（本板最重要的可复用经验）

### 2.1 串口不可靠 → 用 WS→SSE 上报通道观测

本板是 **USB-Serial-JTAG**，抓稳态日志极不可靠（裸 open 触发复位、app USB 重枚举后 reader fd 失效、`--no-reset` 附加抓不到稳态日志）。解决：让固件把诊断行**经已连的 WebSocket 发到服务端**，服务端再转发到 SSE 监控端。

- 固件：`voice_ws_send_debug(const char *text)`（`ws_client.c`）发 `{"type":"device_debug","text":...}`，未连接时 no-op、不刷屏。
- 服务端：`protocol.py` 加 `ClientDeviceDebugMessage`；`server.py` recv_loop 收到 `device_debug` → `print` + `_publish_event(device_id, {"event_type":"device_debug",...})` 转发到该设备的 SSE。
- 监控：`curl -N http://<host>/api/devices/<id>/events`。

> 这条通道是通用观测基建，建议保留（轻量、零开销、不调用就不发）。具体的高频电平上报、寄存器 dump 属于一次性诊断，定位完即清理。

### 2.2 ASR 识别不出 → 把上行 PCM 存 wav 离线分析（决定性手段）

光看"电平/能量"会被骗——失真信号也有正常能量。一定要看**波形/频谱**。服务端旁路落盘：

```python
# server.py：recv_loop 收到 pcm bytes 时 append；audio_end 时写 wav
def _dump_uplink_wav(chunks):
    with wave.open(f"/tmp/uplink_{int(time.time())}.wav","wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(chunks))
```

纯标准库就能做关键判别（无需 numpy）：

```python
import wave, array, math
a = array.array('h'); a.frombytes(wave.open(path,'rb').readframes(-1))
# 1) 过零率：人声常 1k-4k/s；远高（>6k）= 含大量高频跳变/噪声
# 2) 相邻差/均abs：人声 <2；远高 = 字节错位/高频失真
# 3) Goertzel 扫频段找能量峰
# 4) 奇/偶样本能量对比：判断声道交错（见 §4）
```

**本次锁定真因的两个铁证**：① 频谱出现 **550Hz + 7450Hz 对称双峰**（和 = 8000 = 奈奎斯特）= 典型 Fs/2 镜像；② **奇数样本全为 0**（均 abs=0），偶数才是真信号 → 即 `[s,0,s,0]` 交错。

---

## 三、ES7210 麦克风 ADC

本板麦克风接在 ES7210（I2C **0x40**），与 ES8311（0x18）**共用同一条 I2S DIN(GPIO10) 和 I2C 总线**。ESP32 是 I2S master（发 MCLK 16/BCLK 9/WS 45），ES7210 跑 **slave** 模式，从 4.096MHz MCLK（256×16kHz）派生时钟。

驱动 `es7210_codec.c` 寄存器序列照搬 espressif `esp_codec_dev` 官方 es7210 驱动，特化为：slave / 单 MIC1 / I2S-normal / 16bit / 16kHz。要点：

- **PGA 增益** REG43(MIC1) bit[3:0]：`0x00`=0dB，+3dB/档，`0x0a`=+30dB … `0x0e`=+37.5dB（最大）。模拟增益在 ADC **之前**放大，能把信号抬离量化噪底、改善 SNR（这是数字增益做不到的）。
- **务必读回寄存器验证生效**：本次设 `0x0a` 实测没生效（信号贴噪底），改 `0x0e` 后读回 REG43=`0x1e`（=channel-enable 0x10 | gain 0x0e）才确认 PGA 真起作用、信号涨了 ~13 倍。
- slave 模式下 `config_sample`/`set_fs` 不写时钟分频（时钟由 master 给）。

---

## 四、I2S RX 双声道交错（本次最核心的坑）

### 现象
ES7210 按**标准 I2S 输出双声道帧**（MIC1 在左 slot，右 slot 静音），但 ESP32 I2S RX 即使配 `I2S_SLOT_MODE_MONO`，在**全双工**下（TX/RX 共用同一个 `std_cfg`）mono 未真正生效，DMA 实际给出 `[L,R,L,R,...]` 交错数据。代码当连续单声道读 → 得到 `[MIC,0,MIC,0,...]` → 波形彻底失真 → ASR 识别为空。

### 诊断特征（牢记）
- 频谱：真实频率 `f` 与其镜像 `Fs/2 − f` 出现**对称双峰**（`[s,0]` 调制的数学结果）。
- 时域：**奇（或偶）数样本恒为 0**，另一半才是真信号。
- 能量/电平看起来完全正常（所以纯看 avg/peak 会误判）。

### 修复（`audio_capture.c`）
按双声道读、de-interleave 只取 MIC1 所在声道：

```c
/* RX 实为 2-slot 交错 [L,R]，ES7210 把 MIC1 放左 slot、右 slot 静音。
 * 读 2× 数据，只保留左声道，恢复真正的 16kHz mono。*/
#define CAPTURE_FRAME_BYTES (CAPTURE_FRAME_SAMPLES * 2 * sizeof(int16_t))
...
size_t n = (read / sizeof(int16_t)) / 2;
for (size_t i = 0; i < n; i++) frame[i] = stereo[2 * i];  /* 偶索引 = 左 = MIC1 */
```

> 哪个声道是 MIC：用 §2.2 的奇/偶能量对比确定（本板实测偶索引=信号）。

---

## 五、增益与 VAD 调参

`vad.c` 用**平均绝对幅度**（mean |sample|）做能量 VAD：连续 ≥`start_threshold` 计 voiced，3 帧触发起点；<`end_threshold` 计静音，连续 `end_silence` 判结束。

最终参数（ES7210 PGA 满档 +37.5dB、数字增益 ×1 纯模拟、de-interleave 后）：

| 项 | 值 | 说明 |
|----|----|------|
| `ES7210_MIC1_GAIN` | `0x0E`(+37.5dB) | 模拟域增益拉满，SNR 最优 |
| `CAPTURE_SW_GAIN` | `1` | 不要数字增益：×2 时 peak 已到 27k/32k 削顶，且底噪被一起放大撞上 VAD 阈值 |
| `VR_VAD_START_THRESHOLD` | `250` | 实测说话 avg≈800、底噪 avg≈15，SNR≈35dB，阈值取中间偏低 |
| `VR_VAD_END_THRESHOLD` | `120` | 高于底噪、远低于说话，保证能可靠收尾 |
| `VR_VAD_END_SILENCE_MS` | `700` | 尾静音判结束 |

**两条教训**：
1. **数字增益不改善 SNR**（信号噪声一起放大），还会削顶（损害 ASR）。优先用模拟 PGA。
2. **底噪不能贴近 `end_threshold`**：曾经 ×2 时底噪 avg≈150 ≈ end 150，VAD 判不出静音、收不了尾 → utterance 不结束、持续上行直到超时 → **WS 断连**（服务端 `ConnectionClosedError`、设备 reconnecting）。务必拉开底噪与阈值的间距。

---

## 六、半双工声学自激与"播放排空"

半双工（`CONFIG_VR_FULL_DUPLEX` 未开）下，播放时不采集。但服务端 `tts_end` 只表示**音频发送完毕**，设备此时**播放队列 + I2S DMA 尾音还在出声**。若收到 `tts_end` 就立即回监听态开麦，麦克风会拾到设备自己的声音 → 触发新 `audio_start`/barge_in → 打断正在播的回复，形成自激循环（表现为 `tts_start` 多于 `tts_end`）。

修复：把回监听态（`TTS_FINISHED`）的触发**从"收到 tts_end"改为"播放真正结束"**：
- `ws_client.c` 收到 `tts_end` 只调 `voice_audio_playback_on_tts_end()`（标记 input 结束），**不再** post `TTS_FINISHED`。
- `playback_task` 检测到队列排空（`!input_open && size==0`）后，**延迟 ~200ms 等 DMA 尾音放完**，再 `voice_conversation_hw_post(VOICE_CONV_EVENT_TTS_FINISHED)`。
- conversation 的 thinking-timeout 作兜底（万一播放没启动）。

> DMA 尾音时长 ≈ `dma_desc_num × dma_frame_num / Fs`（本板 6×256/16k ≈ 96ms），故 200ms 保护窗足够。

---

## 七、音量

`es8311_codec.c` 启动默认音量曾设 25，真机上几乎听不到（一度误判成"没播放"）。默认改 **80**（`s_volume` 初值 + init 调用同步），运行时仍可 `set_volume` 调。新增 `voice_es8311_get_volume()` 供 UI 显示。

---

## 八、调参速查表

| 想解决 | 调什么 |
|--------|--------|
| 说话无采集 | 确认 ES7210 已 init（`es7210=1`）、I2C 0x40 ACK |
| 采集电平低 | ES7210 PGA `ES7210_MIC1_GAIN` 拉到 `0x0e`；**读回 REG43 验证** |
| ASR 识别为空但电平正常 | **先 dump 上行 wav 看频谱/奇偶样本**，查 I2S 声道交错/格式，而不是调增益 |
| VAD 不触发 | 降 `VR_VAD_START_THRESHOLD`；确认 de-interleave 后电平 |
| 录音收不了尾 / WS 断连 | 抬高 `VR_VAD_END_THRESHOLD` 拉开与底噪间距；别让底噪 ≈ end 阈值 |
| 削顶/失真 | 降 `CAPTURE_SW_GAIN`（优先纯模拟 ×1） |
| 回复被自己打断 | `TTS_FINISHED` 由播放排空触发 + DMA 尾音保护窗 |
| 听不到回复 | 升默认音量 |

---

## 九、关键陷阱

1. **"有能量"≠"是语音"**：失真/交错信号的 avg/peak 看起来正常，VAD 也能过，但 ASR 识别为空。判断音频质量必须看波形/频谱，最快是 dump wav。
2. **全双工 I2S 的 mono 不一定生效**：TX/RX 共用 `std_cfg`，`I2S_SLOT_MODE_MONO` 可能仍按 2-slot 跑，RX 拿到交错数据。先验证实际 slot 行为（奇偶样本），别信配置名义值。
3. **数字增益是 SNR 的陷阱**：放大信号也放大噪声，还削顶。SNR 靠模拟 PGA。
4. **寄存器要读回**：写了不等于生效（本次 PGA `0x0a` 写了没生效）。关键寄存器读回确认。
5. **`tts_end` ≠ 播放结束**：服务端语义是"发完"，设备还有队列+DMA。半双工据此开麦会自激。
6. **底噪贴阈值会导致 WS 断连**：收不了尾 → 超长 utterance → 连接超时断开，别只看"能不能触发"，也要看"能不能结束"。
