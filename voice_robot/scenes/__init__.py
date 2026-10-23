"""可扩展的「意图场景（Intent Scene）」框架。

聊天链路里识别用户的模糊意图（如「想学英语」），进入一个带专属人设的对话
场景，并能通过语音指令退出；进出各有固定语音提示。英语学习是第一个场景实例，
后续新增场景只需在 `defaults.BUILTIN_SCENES` 加一条 + 默认人设，不改主链路。

设计见 `docs/design/plans/2026-06-23-english-learning-scene.md`、ADR 0008。
"""
