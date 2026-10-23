# 0001. ASR 上游失败只中止当前语音段，不拖垮 `/ws` 连接

- 状态：已接受
- 日期：2026-06-09
- 相关：`voice_robot/server.py`（`recv_loop` / `_fail_audio_segment`）、`voice_robot/services/asr.py`（`VolcASRStreamSession`）、测试 `tests/test_ws_realtime_speech_mock.py::test_websocket_streaming_asr_feed_failure_does_not_close`、进展 `docs/progress/WORK-IN-PROGRESS.md`

## 背景

控制台语音以 `codec="pcm_s16le"` 流式上送，服务端在 `audio_start` 时 `open_stream(...)`，并在收到每个音频帧时**直接在 `recv_loop` 里**调用 `asr_session.feed(...)`。`feed()` 在第一帧时才懒连接火山 ASR 上游，一旦连接/鉴权/流失败就抛 `RuntimeError`。

此前该异常无人捕获，会冲出 `recv_loop` → 拆掉整个 anyio task group → `finally` 中 `websocket.close()` 以**默认 code 1000** 关闭连接。现象：**每次语音输入都「WebSocket disconnected (1000)」且无任何错误原因**；文字输入因不走 ASR 而正常。本质是一次（很可能可恢复的）上游故障被升级成了整条会话的硬断开，且把真实错误吞掉了。

约束：`/ws` 是 Web 控制台与硬件设备共用的长连接；`feed()` 必须留在 `recv_loop` 内以保证音频帧顺序；TTS 失败路径已有「报 `error` 不断连」的先例（在受 `cancel_scope` 保护的 turn 任务里）。

## 备选方案

- **方案 A：维持现状（异常冒泡）。** 实现最简，但任一上游抖动都会断开整条连接，用户只看到无信息的 close 1000，无法自助定位，体验与可观测性都差。
- **方案 B：把流式 `feed()` 退化为「整段缓冲 + audio_end 时一次性识别」**，复用已被保护的 turn 任务。改动小，但牺牲实时增量字幕（`asr_partial`），且失败时要重连一次、增加延迟。
- **方案 C（采纳）：在 `recv_loop` 内对 ASR 操作做段级容错。** 给 `audio_start` 的构建/`open_stream` 与流式 `feed()` 加 try/except，失败统一走 `_fail_audio_segment(exc)`：关闭会话、置 `asr_session_failed`、向客户端发 `error` 控制消息并审计 `speech_turn_error`；该段后续音频帧静默丢弃直到 `audio_end`，连接保持可用。

## 决策

采用**方案 C**：ASR 上游失败只中止**当前这一段语音**，以 `error` 消息把真实原因回传客户端，绝不拆连接。理由：把「可恢复的上游故障」与「连接生命周期」解耦，符合系统边界容错原则，并与 TTS 失败路径行为一致；同时保留实时增量字幕能力。

## 后果

- **正面**：语音失败不再断连，连接可继续用于下一轮；真实错误（含 `asr.py` 内置的中文 advice，如 key 未授权 `volc.bigasr.sauc.duration`、空音频、等包超时）直达客户端，便于自助排障与观测。
- **负面 / 代价**：`recv_loop` 多了 `asr_session_failed` 段级状态与分支；「失败后静默丢弃尾帧」属刻意行为，阅读时需留意。
- **后续关注**：`_reader_loop` 的 `on_partial` 发送与主循环/turn 任务在同一 websocket 上并发 `send`，是另一类潜在风险，本决策未覆盖；若后续出现并发发送报错需单独处理。根因侧（火山流式 ASR 资源开通 / `resource_id` 配置）仍需在控制台「语音配置」核对，本决策只保证故障被正确暴露而非修复上游。
