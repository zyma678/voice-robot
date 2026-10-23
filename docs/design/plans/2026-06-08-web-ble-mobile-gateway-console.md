# Web BLE Mobile Gateway Console Plan

**Goal:** 在不依赖原生手机 App 和真机最终验收的前提下，先在 Web 控制台中实现“手机网关”调试原型，验证 BLE Central + 云端 WebSocket Gateway 的第一阶段行为。

**Scope:** 只覆盖 BLE 控制/状态与后端设备注册可观测性；不承载完整语音上行/下行。

## Tasks

- [x] 在 `web_console/index.html` 中新增 `手机网关` Tab 和独立工作区。
- [x] 在 `web_console/app.js` 中新增 Web Bluetooth 连接逻辑，连接固定 GATT service / control / status UUID。
- [x] 在 `web_console/app.js` 中新增云端 WebSocket gateway 连接逻辑，auth 时携带：
  - `device_id`
  - `token`
  - `transport=ble_mobile_gateway`
  - `gateway_id`
- [x] 支持手动发送 `set_volume` 到 BLE control characteristic。
- [x] 支持接收后端 `set_volume` 命令并自动转发到 BLE。
- [x] `say` 命令第一阶段仅记录日志，不接音频链路。
- [x] 记录网关日志、BLE 状态、云端状态和最近一次 status notify。
- [x] 为前端资产新增自动化测试断言。
- [x] 运行 `node --check web_console/app.js` 与 `.venv/bin/pytest tests/test_web_console_assets.py -q`。

## Remaining

- [ ] 在统一真机验收阶段，使用实际 ESP32 进入 BLE fallback，验证 Web Bluetooth 能连接 `VR-esp32-001`。
- [ ] 验证后端 `/api/devices` 中同一 `device_id` 能正确展示 `transport=ble_mobile_gateway` 与 `gateway_id`。
- [ ] 验证云端 `POST /api/devices/{device_id}/volume` 经浏览器网关转发后，硬件端音量确实改变。
- [ ] 第二阶段单独设计 BLE 音频分片与手机原生 App 方案。
