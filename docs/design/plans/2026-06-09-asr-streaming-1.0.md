# 流式语音识别 1.0 重构方案（Volc 双向流式 WebSocket）

最后更新：2026-06-09

## 背景与目标
把 ASR 模块从「录音文件识别 2.0」（AUC，submit/query 文件式 HTTP 接口）重构为「流式语音识别 1.0」（火山双向流式 WebSocket 二进制协议）。规格来源：`docs/knowledge/integrations/ASR.md`。

两项关键决策：
1. **端到端实时流式**：`audio_start` 建立会话 → PCM 分片实时下发 → 增量 `asr_partial` 上屏 → `audio_end` 发末包取最终文本 → `asr_result` → 原 `_run_turn`（LLM+TTS）。
2. **仅新版 X-Api-Key 鉴权**：移除旧版 app_id+access_token、TTS 凭证兜底、cloudflared 公网隧道等 AUC 专属逻辑。默认资源 ID `volc.bigasr.sauc.duration`。

## 协议要点（sauc/bigmodel）
- 接口：`wss://openspeech.bytedance.com/api/v3/sauc/bigmodel`
- Header：`X-Api-Key`、`X-Api-Resource-Id`、`X-Api-Request-Id`(UUID)、`X-Api-Connect-Id`(UUID)、`X-Api-Sequence: -1`
- 二进制帧（大端 + 可选 gzip）：
  - header 4 字节：version=1/header size=1；message type + flags；serialization + compression；reserved
  - message type：full client 0b0001 / audio only 0b0010 / full server 0b1001 / error 0b1111
  - flags：无 0b0000 / 正 seq 0b0001 / 末包 0b0010 / 负 seq 末包 0b0011
  - full client request：JSON（audio.format=pcm/codec=raw/rate/bits=16/channel=1；request.model_name=bigmodel + enable_* + show_utterances + result_type=full；不传 language）
  - full server response：result.text 增量更新；末包 flag 标记最终结果

## 实施结果（已落地）
- `voice_robot/services/asr.py`：二进制 codec 纯函数 + `ASRStreamSession`/`VolcASRStreamSession` + `VolcASRService.open_stream/transcribe` + `LocalASRService` 流式兜底；删除 AUC submit/query/poll、WAV 封装、`AudioURLPublisher`。reader 用 `asyncio.ensure_future` 后台任务，`finish` 用 `anyio.fail_after` 防等包超时。
- `voice_robot/server.py`：`_build_runtime_asr_service` 仅 X-Api-Key + resource_id + ws_url；recv_loop 在 `audio_start` 开会话、PCM 分片 `feed`、`on_partial` 转 `asr_partial`、opus 走 `_BufferingASRSession` 缓冲回退；`audio_end` 把会话交给 turn，`finish` 后 `_run_turn`，`finally` `aclose`。删 `_publish_asr_audio` 与隧道依赖。
- `voice_robot/runtime_config.py`：新增 `resolve_speech_asr_ws_url`；`asr_resource_id` 默认改 `volc.bigasr.sauc.duration`；`save_speech_settings`/`load_speech_settings` 仅保留 `asr_api_key/asr_resource_id/asr_ws_url/asr_sample_rate_hz`。
- `voice_robot/admin_api.py`：`SpeechSettingsUpdate` 精简并删除 wav/raw/16000 硬校验；`_resolved_speech_settings`/`/status`/`PUT /settings/speech` 同步 `asr_ws_url`；`configured = asr_api_key and asr_resource_id`（或 TTS）。
- `web_console/index.html` + `app.js`：表单改为「流式语音识别 1.0」（ASR ws url / resource id / X-Api-Key）；`asr_partial` 改覆盖式上屏；采集逻辑不变（PCM16k + 客户端 VAD）。
- `pyproject.toml`：新增 `websockets>=13.0`。
- 测试：`tests/test_speech_services.py`（codec round-trip + 流式会话单测）、`tests/test_ws_realtime_speech_mock.py`（open_stream mock + `asr_partial→asr_result` e2e + 兼容缓冲路径）、`tests/test_admin_api.py`、`tests/test_web_console_assets.py` 同步更新。

## 验证
- `pytest -q`：187 passed。
- `node --check web_console/app.js`：通过。
