# 火山语音接入配置（ASR / TTS）

> 当前生效的接入事实与排错。配置变化时更新本文件。
> 最后更新：2026-06-09（语音链路实账号打通）

## 当前生效配置

鉴权统一用新版控制台 `X-Api-Key`（ASR 与 TTS 可共用同一个 key）。配置项落在 `runtime/config/console_settings.json` 的 `speech.*`，可在 Web 控制台「语音配置」页编辑。

| 用途 | 关键参数 | 当前值 |
| --- | --- | --- |
| ASR | `asr_resource_id` | `volc.bigasr.sauc.duration` |
| ASR | `asr_ws_url` | `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel` |
| ASR | `asr_sample_rate_hz` | `16000` |
| TTS | `tts_resource_id` | `seed-tts-1.0` |
| TTS | `tts_ws_url` | `wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream` |
| TTS | `tts_sample_rate_hz` | `16000`（与当前硬件 DAC 播放配置保持一致） |

## 火山控制台需开通的产品

- **ASR**：开通「**流式语音识别 2.0**」。
  - 小时版 → resource id `volc.bigasr.sauc.duration`（当前使用）
  - 并发版 → resource id `volc.bigasr.sauc.concurrent`
- **TTS**：开通「**语音合成 1.0**」（seed-tts），resource id `seed-tts-1.0`，经 V3 单向流式 WebSocket 接入。

> 命名提醒：项目内部代码与早期计划（`docs/design/plans/2026-06-09-asr-streaming-1.0.md`、`services/asr.py` 注释、控制台文案）把该 ASR 接入称作「流式语音识别 1.0」，与火山控制台的「**流式语音识别 2.0**」是**同一套 sauc 双向流式接入**。以本文件为准，旧文案的「1.0」是误称。

## 排错：握手 HTTP 403

现象：语音输入时后端报 `volc sauc asr connect failed: server rejected WebSocket connection: HTTP 403`。

- **典型根因（已遇到，2026-06-09）**：`asr_resource_id` 残留旧的「录音文件识别」资源 `volc.seedasr.auc`（AUC 文件式接口），却被发到流式 `sauc/bigmodel` 端点 → resource 与 endpoint 不匹配，火山在握手阶段直接 403 拒绝。把 resource id 改成 `volc.bigasr.sauc.*` 即可。
- 排查顺序：
  1. `asr_resource_id` 是否为 `volc.bigasr.sauc.duration` / `.concurrent`（不是 `volc.seedasr.auc`）。
  2. 火山账号是否已开通「流式语音识别 2.0」对应版本（小时/并发要与 resource id 一致）。
  3. `X-Api-Key` 是否有效（TTS 能用通常说明 key/网络没问题，问题多在 ASR resource）。
- **失败不会断连**：ASR 上游失败只中止当前语音段并回一条 `error` 消息，`/ws` 连接保持可用（见 [`../decisions/0001-asr-failure-must-not-drop-ws.md`](../decisions/0001-asr-failure-must-not-drop-ws.md)）。
