# Voice Robot W1 实施计划

> **面向 Agent 执行者：** 必须使用子技能：`superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐步实施。本计划使用复选框（`- [ ]`）跟踪步骤。

**目标：** 基于需求文档，完成 W1 最小闭环：FastAPI WebSocket 接收音频/文本输入，串联 ASR -> LangGraph 风格 Agent -> TTS，并将结构化响应流式回传客户端。

**架构：** 在 `voice_robot/` 下实现边界清晰的小型 Python 包：协议模型、配置、服务适配器、Agent 图编排、记忆存储/检索、FastAPI 应用。外部火山引擎 ASR/TTS 与 LLM 通过异步适配层抽象，同时提供确定性的本地 fallback，确保无凭据情况下也可本地开发与测试。

**技术栈：** Python 3.11+、FastAPI、Pydantic v2、pytest、pytest-asyncio、httpx、websockets；安装依赖后可选集成 LangGraph/LangChain。

---

## 需求来源

原始需求文档已沉淀至：`docs/prd/esp32-s3-technical-solution.md`

W1 范围：
- 跑通最小闭环：FastAPI + ASR + LLM/Agent + TTS。
- 抽象并共享 WebSocket 协议，供后续 Web UI 与 ESP32-S3 固件复用。
- 为 W2 Web UI、W3 流式/VAD/记忆增强、W4 硬件适配保留扩展点。

W1 不包含：
- React Web UI。
- ESP32-S3 固件实现。
- 火山引擎 SDK 的完整线上集成（W1 仅接口占位 + fallback）。
- 生产级鉴权/设备注册体系。

## 文件结构

- 新建：`pyproject.toml` - 包元信息、依赖、pytest 配置
- 新建：`README.md` - 本地运行说明与 WebSocket 协议示例
- 新建：`voice_robot/__init__.py` - 包标记
- 新建：`voice_robot/config.py` - 环境变量配置
- 新建：`voice_robot/protocol.py` - WebSocket 协议模型
- 新建：`voice_robot/services/asr.py` - ASR 适配接口与本地 fallback
- 新建：`voice_robot/services/tts.py` - TTS 适配接口与本地 fallback
- 新建：`voice_robot/agent/state.py` - Agent 状态模型
- 新建：`voice_robot/agent/tools.py` - 儿童友好工具（时间/口算/故事）
- 新建：`voice_robot/agent/graph.py` - 可确定执行的 Agent 流程
- 新建：`voice_robot/memory/file_store.py` - 画像、会话、记忆落盘
- 新建：`voice_robot/memory/file_retriever.py` - 关键词/重要度/时间排序检索
- 新建：`voice_robot/server.py` - FastAPI 与 `/ws`
- 新建：`tests/test_protocol.py`
- 新建：`tests/test_memory.py`
- 新建：`tests/test_agent.py`
- 新建：`tests/test_server.py`

## 任务 1：项目骨架与协议层

**涉及文件：**
- `pyproject.toml`
- `README.md`
- `voice_robot/__init__.py`
- `voice_robot/config.py`
- `voice_robot/protocol.py`
- `tests/test_protocol.py`

- [ ] **Step 1：先写协议失败测试**
  - 覆盖 `ClientControlMessage` / `ServerControlMessage` 校验与序列化。
- [ ] **Step 2：执行协议测试并确认失败**
  - 命令：`pytest tests/test_protocol.py -q`
- [ ] **Step 3：实现包配置与协议模型**
  - 包含消息类型约束、默认字段、`encode_server_message`。
- [ ] **Step 4：再次执行协议测试**
  - 命令：`pytest tests/test_protocol.py -q`
- [ ] **Step 5：提交（若仓库启用 git）**

## 任务 2：文件记忆存储与检索

**涉及文件：**
- `voice_robot/memory/file_store.py`
- `voice_robot/memory/file_retriever.py`
- `voice_robot/memory/__init__.py`
- `tests/test_memory.py`

- [ ] **Step 1：先写失败测试（记忆存储与检索）**
  - 覆盖 profile 保存、memory index 写入、TopK 检索排序。
- [ ] **Step 2：执行测试并确认失败**
  - 命令：`pytest tests/test_memory.py -q`
- [ ] **Step 3：实现文件落盘与检索评分**
  - 存储：`profiles/`、`memories/<user>/index.json`
  - 检索：关键词命中 + importance 加权（含 resolved 降权）
- [ ] **Step 4：执行记忆测试**
  - 命令：`pytest tests/test_memory.py -q`
- [ ] **Step 5：提交（若仓库启用 git）**

## 任务 3：Agent 流程与儿童安全策略

**涉及文件：**
- `voice_robot/agent/__init__.py`
- `voice_robot/agent/state.py`
- `voice_robot/agent/tools.py`
- `voice_robot/agent/graph.py`
- `tests/test_agent.py`

- [ ] **Step 1：先写失败测试（Agent 行为）**
  - 覆盖：儿童友好回复、敏感内容拦截、口算工具调用。
- [ ] **Step 2：执行测试并确认失败**
  - 命令：`pytest tests/test_agent.py -q`
- [ ] **Step 3：实现可确定流程的 Agent**
  - 敏感关键词拦截 -> 安全引导回复
  - 普通请求 -> 记忆检索 -> 规则/工具/故事生成
  - 回复长度与儿童风格约束
- [ ] **Step 4：执行 Agent 测试**
  - 命令：`pytest tests/test_agent.py -q`
- [ ] **Step 5：提交（若仓库启用 git）**

## 任务 4：ASR/TTS 适配与 `/ws` 闭环

**涉及文件：**
- `voice_robot/services/__init__.py`
- `voice_robot/services/asr.py`
- `voice_robot/services/tts.py`
- `voice_robot/server.py`
- `tests/test_server.py`

- [ ] **Step 1：先写失败测试（WebSocket 闭环）**
  - 覆盖：`auth_ok`、`asr_result`、`llm_reply`、`tts_start/tts_end`。
- [ ] **Step 2：执行测试并确认失败**
  - 命令：`pytest tests/test_server.py -q`
- [ ] **Step 3：实现本地 ASR/TTS fallback 与 `/ws`**
  - ASR fallback：UTF-8 文本解码
  - TTS fallback：返回可播放 bytes
  - `/ws`：auth、heartbeat、audio_start、audio_end、错误处理
- [ ] **Step 4：执行完整测试集**
  - 命令：`pytest -q`
- [ ] **Step 5：提交（若仓库启用 git）**

## 任务 5：文档与运行验证

**涉及文件：**
- 修改：`README.md`
- 新建：`scripts/ws_smoke.py`

- [ ] **Step 1：新增可执行 smoke 脚本**
  - 连接 `ws://127.0.0.1:8080/ws`，跑通 auth -> audio -> llm -> tts。
- [ ] **Step 2：更新 README 验证指令**
  - `pytest -q`
  - `uvicorn voice_robot.server:app --host 127.0.0.1 --port 8080`
  - `python scripts/ws_smoke.py`
- [ ] **Step 3：执行测试**
  - 命令：`pytest -q`
- [ ] **Step 4：提交（若仓库启用 git）**

## 自检

需求覆盖映射：
- W1 最小闭环（FastAPI + ASR + LLM/Agent + TTS）：任务 1 / 3 / 4
- 统一 WebSocket 协议：任务 1 / 4
- 文件记忆基础能力：任务 2
- 儿童安全策略：任务 3
- 冒烟验证与可复现文档：任务 5

执行约束：
- 当前目录可能不是 git 仓库；若 `git` 不可用，提交步骤标记为不适用并在进展文档说明。
- 依赖缺失时先执行：`python -m pip install -e ".[dev]"`
