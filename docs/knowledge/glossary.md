# 术语表 / Glossary

> 项目内常用名词与缩写。新增领域概念时补充到这里，保持团队与 Agent 用语一致。

| 术语 | 含义 |
| --- | --- |
| ASR | 自动语音识别（Automatic Speech Recognition），把音频转文本。见 `integrations/ASR.md` |
| TTS | 语音合成（Text-to-Speech），把文本转音频。见 `integrations/TTS.md` |
| Barge-in | 打断：用户在机器播报过程中开口，系统应停止播报并转入聆听 |
| `/ws` | 设备与后端之间的 WebSocket 实时语音协议端点，字段定义在 `voice_robot/protocol.py` |
| BLE Mobile Gateway | BLE 移动网关：设备无直连网络时，经手机蓝牙中转接入后端（`transport=ble_mobile_gateway`） |
| Web Console | Web 管理/调试控制台，`/console`，`device_id == "web-console"` |
| Ark / Doubao | 火山引擎大模型推理服务，后端 LLM 默认接入对象 |
| SDD | 规约驱动开发（Spec-Driven Development），本项目用 DoubaoADK 的 `adk-sdd-*` 技能落地 |
| Constitution | 项目宪法，SDD 的不可变原则，位于 `.doubaoadk/memory/constitution.md` |
| ADR | 架构决策记录（Architecture Decision Record），见 `decisions/` |
| W1 | 当前原型阶段代号（W1 prototype） |
