# 方案：TTS 切 V3 单向流式 WebSocket + ASR→LLM→TTS 链路流畅性优化

最后更新：2026-06-09

## 背景与目标
把 TTS 从 V1 HTTP 非流式（整段文本合成完才返回首帧）改造为火山「V3 单向流式 WebSocket」（`wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream`），整段文本一次性送入、服务端自动分句、边合成边吐 PCM 帧，服务端收到一帧就转发一帧。把首字延迟从「整段合成」降到「首句合成首帧」，让 ASR→转文本→LLM→文本→TTS 播放更流畅。规格源：`docs/knowledge/integrations/HTTP-Chunked-V3.md`。

## 关键决策
- **整段送入单条 V3 流式会话**，不自己切句（服务端分句）。`synthesize(text)` 仍是 `AsyncIterator[bytes]`，对 `_run_turn` 透明。
- **仅新版控制台 X-Api-Key 鉴权**（与 ASR 1.0 对齐），默认 `X-Api-Resource-Id=seed-tts-1.0`；彻底移除旧版 app_id/access_token/cluster/base_url。
- **不并发 LLM-delta 与 TTS**：保持事件顺序不变，避免破坏顺序断言与 barge-in 取消语义。流畅性收益来自 V3 流式首帧 + 打字机 sleep 从 0.04 降到 0.005。
- **默认采样率 24000**（V3 默认、音质更好）。前端播放/归档全链路按 `tts_sample_rate_hz` 参数化，控制台不受影响；真实硬件需带外按该采样率配置 DAC。
- **保持单次合成可重听**：整轮流式 PCM 帧累计拼接后仍走 `attach_tts_audio` 归档。

## V3 协议要点（大端 + 4 字节 header）
- 鉴权头：`X-Api-Key`、`X-Api-Resource-Id`、`X-Api-Request-Id`(uuid)。
- 上行 SendText：message type=0b0001, flags=0b0000, JSON, **不压缩**；payload `{"user":{"uid":..},"req_params":{"text":..,"speaker":voice,"audio_params":{"format":"pcm","sample_rate":24000}}}`。
- 上行 FinishConnection：flags=0b0100, event=2, payload `{}`。
- 下行帧：音频 message type=0b1011（raw PCM）、JSON 控制帧 0b1001、错误 0b1111；结构 `header(4)+event(4B)+[session_id_len(4B)+session_id]+payload_size(4B)+payload`。event：350 SentenceStart / 351 SentenceEnd / 352 TTSResponse（音频）/ 152 SessionFinished（{"status_code":20000000}）/ 52 ConnectionFinished。错误帧 `header(4)+error_code(4B)+payload`。

## 实施结果（已落地）
- `voice_robot/services/tts.py`：二进制 codec 纯函数 + `VolcTTSConfig`(api_key/resource_id/ws_url/voice_type/sample_rate_hz=24000/encoding) + `VolcTTSService.synthesize`（建连→SendText→reader 循环 yield 音频→152 结束→`finally` 优雅关连接）；删 V1 HTTP/base64/urllib；`LocalTTSService` 保留。
- `voice_robot/server.py`：`_build_runtime_tts_service` 读 tts_api_key/voice_type/sample_rate/resource_id/ws_url/encoding，缺 api_key 或 voice→LocalTTSService；`_run_turn` 打字机 sleep 0.04→0.005，其余不动。
- `voice_robot/runtime_config.py`：tts resolver 换 api_key/resource_id(默认 seed-tts-1.0)/ws_url，sample_rate 默认 24000；save/load 字段切换。
- `voice_robot/admin_api.py`：`SpeechSettingsUpdate`/`_resolved_speech_settings`/`configured`(tts_api_key and tts_voice)/`/status`(speech_tts_ws_url)/PUT 同步。
- `web_console/index.html`+`app.js`：Volc TTS 区改 ws url/resource id/X-Api-Key/voice/sample rate(24000)；render/load/save/enterEdit/loadStatus 同步。
- 测试：`tests/test_speech_services.py`（删 V1 TTS，新增 codec round-trip + 流式 synthesize + 错误帧）、`tests/test_ws_realtime_speech_mock.py`（FakeVolcTTS 新字段 + save 字段）、`tests/test_admin_api.py`、`tests/test_web_console_assets.py`。

## 验证
- `.venv/bin/python -m pytest -q`：191 passed。
- `node --check web_console/app.js`：通过。

## 待办风险
- 真实硬件固件需按 `tts_sample_rate_hz`（默认 24000）配置播放采样率，否则按 16000 播放会变调；控制台 Web 端已参数化不受影响。
