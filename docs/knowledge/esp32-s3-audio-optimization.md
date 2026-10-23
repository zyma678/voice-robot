# ESP32-S3 音频播放优化知识库

> 适用：ESP32-S3 + ES8311 codec + I2S DAC + Volcano TTS（16kHz/16bit/mono PCM 流）  
> 对应代码：`firmware/main/audio_playback.c`、`firmware/main/es8311_codec.c`、`voice_robot/server.py`  
> 实测时间：2026-06-12 ~ 2026-06-13

---

## 一、问题总览与根因

| 症状 | 根因 | 修复点 |
|------|------|--------|
| 全程持续"滋滋"底噪 | I2S TX DMA 队列耗尽时循环播旧缓冲区 | `auto_clear=true` |
| 只播第一句就断 | 音频队列 96KB 占满 .bss，堆空间不足致 WS/TLS RX 缓冲饥饿，连接中途断开 | 队列缩至 32KB |
| 上线回归（device offline） | `dma_desc_num=8` 占用额外内部 RAM，Wi-Fi/WS TLS 发送缓冲饥饿，auth 帧发不出去 | `dma_desc_num` 回退 6 |
| 说话时削顶"电流声" | Volcano TTS PCM 接近满幅（0 dBFS），在可用音量下过驱 codec DAC / 功放 | 数字衰减 ×0.80 ≈ −1.9 dB |
| 切句边界咔哒噪声 | 服务端 nominal 16kHz 与 codec 硬件时钟微差，多句回复后队列缓慢抖向空，`auto_clear` 静音切换仍可听见一次 | 200ms 预缓冲（pre-prime） |
| ES8311 速度×3 慢 | REG02（Headphone 输出路径）未清零，多路混音/过滤链叠加延时 | `REG02=0x00` |
| 串口操作导致设备卡在 download mode | USB-Serial-JTAG 反复 RTS/DTR 操作触发固件进入下载模式锁死 | 物理拔插 USB 重置；调试时禁止程序化开关串口 |

---

## 二、关键参数（`audio_playback.c`）

```c
/* 音频队列：32KB ≈ 1s 缓冲，满足服务端实时分帧即可。
 * 此板内存紧张（启动时堆仅余 ~145KB），旧的 96KB 占 .bss 会让
 * WS/TLS RX 缓冲饥饿 → 连接中途掉 → 只播一句就断。*/
#define AUDIO_QUEUE_BYTES (32 * 1024)

/* 分块写 I2S：512 字节 = 16ms，平衡延时与调度开销。*/
#define AUDIO_TASK_CHUNK_BYTES 512

/* 数字衰减（Q8 定点，256=不衰减）：
 * 205/256 ≈ 0.80 ≈ −1.9 dB。
 * Volcano TTS 满幅 PCM 在可用音量下峰值过驱 DAC/功放 → "电流声"。
 * 给模拟链留余量，峰值不再削顶，响度感知变化不大。*/
#define AUDIO_OUTPUT_SCALE_Q8 205

/* 开播前预缓冲：~200ms = 6144 字节（16kHz/16bit）。
 * 服务端名义 16kHz 与 codec 硬件时钟存在微小差率，多句回复累积
 * 后队列会缓慢抖向空；在句间安静切换处会产生一次咔哒声。
 * 预留 200ms 缓冲垫，使队列全程不触底。*/
#define AUDIO_PREBUFFER_BYTES (6 * 1024)
```

---

## 三、I2S 通道配置要点

```c
i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);

// dma_desc_num 保持 6（≈96ms）。
// 此板内部 RAM 极紧，调高到 8 会让 Wi-Fi/WS TLS 发送缓冲饥饿，
// auth 帧静默发送失败 → device 永远不上线。
chan_cfg.dma_desc_num = 6;
chan_cfg.dma_frame_num = 256;

// auto_clear：队列耗尽时 DMA 输出静音而非循环旧缓冲区。
// TX 通道在设备整个生命周期保持 enable，不喂数据时 DMA 会重播旧采样
// → 持续"滋滋"底噪。此标志零 RAM 开销，可安全启用。
chan_cfg.auto_clear = true;

// 全双工：TX → ES8311 DAC（扬声器），RX ← codec ADC（麦克风）。
// 两者共用同一控制器的 MCLK/BCLK/WS。
i2s_new_channel(&chan_cfg, &s_i2s_tx, &s_i2s_rx);
```

---

## 四、预缓冲（Pre-prime）逻辑

```c
// 每次 TTS 开始时重置 s_primed = false。
// 队列积累到 AUDIO_PREBUFFER_BYTES 后才开始泄流。
// 如果 TTS 输入提前关闭（极短回复），立即解除等待，防止卡死。
if (active && !s_primed) {
    if (voice_audio_playback_queue_size(&s_audio_queue) >= AUDIO_PREBUFFER_BYTES ||
        !input_open) {
        s_primed = true;
    }
}
```

`s_primed` 需要在以下三处重置为 `false`：
- `voice_audio_playback_on_tts_start()`（新 TTS 开始）
- `voice_audio_playback_on_barge_in()`（用户打断）
- 若有其他强制中断路径也需同步重置

---

## 五、数字衰减实现

```c
#if AUDIO_OUTPUT_SCALE_Q8 != 256
int16_t *samples = (int16_t *)chunk;
for (size_t i = 0; i < len / sizeof(int16_t); i++) {
    samples[i] = (int16_t)((samples[i] * AUDIO_OUTPUT_SCALE_Q8) >> 8);
}
#endif
```

- 在 `i2s_channel_write` 之前原地修改，不需要额外缓冲区
- `AUDIO_OUTPUT_SCALE_Q8 = 256` 时编译器会整体删除此循环（零开销）
- 调大 `AUDIO_OUTPUT_SCALE_Q8`（逼近 256）= 响度更大但更易削顶；调小 = 更保守

---

## 六、服务端配合：实时分帧

`voice_robot/server.py` 中的 `_send_tts_audio_bytes`：

```python
frame_bytes = 1280          # 16kHz × 2byte × 40ms = 1280 字节/帧
interval_s  = frame_bytes / (16000 * 2)   # = 0.04s

# 每帧发送后 sleep interval_s，模拟真实播放速率。
# 如果不限速，服务端会在 <1s 内把整个 TTS dump 到队列，
# 32KB 队列瞬间溢出，大量帧被 dropped。
```

客户端队列只需缓冲"网络抖动 + 时钟微差"，而不是整条回复，所以 32KB 绰绰有余。

---

## 七、ES8311 Codec 关键寄存器

| 寄存器 | 值 | 说明 |
|--------|-----|------|
| REG02 | `0x00` | 关闭 Headphone 输出额外混音链，防止播放速度×3 慢的问题 |
| 默认音量 | 25（`voice_es8311_set_volume(25)`） | 对应约 −6dB 线路增益，配合数字衰减使用 |

---

## 八、内存预算（参考值，2026-06-13）

| 项目 | 大小 |
|------|------|
| 启动后堆余量 | ~145KB |
| `s_queue_storage`（.bss） | 32KB |
| I2S DMA（dma_desc_num=6, frame=256） | ~12KB 内部 RAM |
| Wi-Fi/WS TLS 缓冲区（运行时峰值） | ~80KB |
| LVGL 双缓冲 + 堆对象 | ~40KB |

> 三者相加逼近上限。任何 .bss / DMA 扩张都要从整体堆预算里扣。

---

## 九、调试常见陷阱

### 9.1 设备卡在 download mode
- 原因：程序化反复操作 RTS/DTR 触发 USB-Serial-JTAG 进入烧录模式
- 处置：物理拔插 USB 线（等待约 2 秒再插）
- 串口设备名可能从 `/dev/cu.usbmodemXXXX` 变为 `/dev/cu.usbmodemXXXX`，需更新 flash 命令

### 9.2 device 上线后 hardware_online 一直 = 0
- 常见根因：DMA 描述符过多，内部 RAM 不足，TLS auth 帧静默丢失
- 验证方法：从 PC 直接用 `wscat` 连服务端正常；仅真机失败 → RAM 竞争
- 修复：减小 `dma_desc_num`（不超过 6）

### 9.3 只播第一句就断
- 串口看 LVGL buffer allocation failed + server 侧 `websocket.close` 异常
- 根因：大 .bss 音频队列抢走堆，WS TLS RX 缓冲饥饿 → 连接中断
- 修复：缩小 `AUDIO_QUEUE_BYTES`

### 9.4 `audio queue full` 日志频繁
- 服务端未限速 → dump 整条 TTS 到队列 → 32KB 瞬间满
- 修复：服务端 `_send_tts_audio_bytes` 按实时速率分帧（见 §六）

---

## 十、调整建议速查

| 想要… | 调整 |
|-------|------|
| 响度更大 | `AUDIO_OUTPUT_SCALE_Q8` 调大（最大 256）；或 `voice_es8311_set_volume` 调大 |
| 消"滋滋"底噪 | 确认 `auto_clear=true` 已设置 |
| 减少句间咔哒声 | 适当加大 `AUDIO_PREBUFFER_BYTES`（每 32ms = 1024 字节） |
| 首字延迟更低 | 减小 `AUDIO_PREBUFFER_BYTES`（权衡句间稳定性） |
| 更多 RAM 给其他组件 | 减小 `AUDIO_QUEUE_BYTES`（下限约 8KB；服务端实时分帧时够用） |
| 切换到 8kHz 或 48kHz | 同步改 `AUDIO_SAMPLE_RATE_HZ`、ES8311 init 函数、服务端 TTS 采样率 |
