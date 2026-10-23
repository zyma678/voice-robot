# Voice Robot Web 控制台实施计划

> **面向 Agent 执行者：** 必须使用子技能：`superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐步实施。本计划使用复选框（`- [ ]`）跟踪步骤。

**目标：** 在与后端包目录分离的位置新增一个“运维优先”的 Web 管理/调试控制台，并由现有 FastAPI 应用提供访问入口。

**架构：** 前端静态资源放置在 `web_console/`；新增一个轻量只读管理 API 模块用于运行态与记忆信息查看；在 `voice_robot.server` 挂载控制台入口。浏览器侧通过 `fetch` 获取管理数据，使用原生 `WebSocket` 完成 `/ws` 调试。

**技术栈：** FastAPI、StaticFiles、HTML、CSS、原生 JavaScript、pytest。

---

## 文件结构

- 新建：`web_console/index.html`
- 新建：`web_console/app.js`
- 新建：`web_console/styles.css`
- 新建：`voice_robot/admin_api.py`
- 修改：`voice_robot/server.py`
- 新建：`tests/test_admin_api.py`
- 新建：`tests/test_web_console_assets.py`

## 任务 1：只读管理 API

**涉及文件：**
- 新建：`voice_robot/admin_api.py`
- 新建：`tests/test_admin_api.py`
- 修改：`voice_robot/server.py`

- [ ] **Step 1：先写失败测试（admin API）**
- [ ] **Step 2：执行测试并确认失败**
- [ ] **Step 3：实现 `/api/status` 与记忆只读接口**
- [ ] **Step 4：执行 admin API 测试**
- [ ] **Step 5：提交**

## 任务 2：静态控制台页面骨架

**涉及文件：**
- 新建：`web_console/index.html`
- 新建：`web_console/styles.css`
- 新建：`tests/test_web_console_assets.py`
- 修改：`voice_robot/server.py`

- [ ] **Step 1：先写失败测试（静态资源）**
- [ ] **Step 2：执行测试并确认失败**
- [ ] **Step 3：实现运维优先布局并完成静态挂载**
- [ ] **Step 4：执行静态资源测试**
- [ ] **Step 5：提交**

## 任务 3：浏览器运行态 / 调试逻辑

**涉及文件：**
- 新建：`web_console/app.js`
- 修改：`web_console/index.html`
- 修改：`web_console/styles.css`

- [ ] **Step 1：若可行补充前端行为测试；否则通过后端联调测试覆盖**
- [ ] **Step 2：实现运行态拉取、记忆视图和原生 WebSocket 调试器**
- [ ] **Step 3：执行完整测试集**
- [ ] **Step 4：手工冒烟：启动 uvicorn，打开 `/console`，连接 `/ws`，发送一轮 mock 文本音频**
- [ ] **Step 5：提交**

## 自检

- 覆盖映射：
  - 运维优先布局 -> 任务 2 / 任务 3
  - 前后端目录分离 -> 任务 2
  - 只读状态/记忆 API -> 任务 1
  - FastAPI 提供 `/console` -> 任务 2
  - `/ws` 调试器 -> 任务 3
- 除任务名称外不留占位内容
- 实施范围控制在同一子系统，并复用现有 W1 后端能力
