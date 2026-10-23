# BLE Mobile Gateway Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏现有 Wi-Fi 直连链路的前提下，增加 BLE 手机网关兜底的第一阶段能力。

**Architecture:** 后端 `/ws` 继续作为唯一云端入口，`auth` 扩展 `transport` 与 `gateway_id` 元数据。固件优先尝试 Wi-Fi，失败后启动 BLE Peripheral，先支持控制命令和状态通知；完整 BLE 音频链路留到第二阶段。

**Tech Stack:** FastAPI + Pydantic + pytest；ESP-IDF + NimBLE；WebSocket `/ws`；Waveshare ESP32-S3-Touch-LCD-1.83。

---

### Task 1: 后端识别网关传输元数据

**Files:**
- Modify: `voice_robot/protocol.py`
- Modify: `voice_robot/server.py`
- Test: `tests/test_protocol.py`
- Test: `tests/test_devices_api.py`

- [x] **Step 1: Write failing protocol tests**

Add tests that parse:

```python
{"type": "auth", "device_id": "esp32-001", "token": "<your-token>", "transport": "ble_mobile_gateway", "gateway_id": "phone-dev"}
```

Expected model fields:

```python
msg.transport == "ble_mobile_gateway"
msg.gateway_id == "phone-dev"
```

- [x] **Step 2: Write failing device registry test**

Open `/ws`, authenticate with gateway metadata, then call `/api/devices`.

Expected device entry:

```python
{
    "device_id": "esp32-ble",
    "kind": "hardware",
    "transport": "ble_mobile_gateway",
    "gateway_id": "phone-dev",
}
```

- [x] **Step 3: Implement protocol fields**

Add optional auth fields:

```python
transport: Literal["wifi_direct", "ble_mobile_gateway"] = "wifi_direct"
gateway_id: str | None = None
```

- [x] **Step 4: Store registry metadata**

Pass `transport` and `gateway_id` into `_register_connection`, include them in `/api/devices`, and leave existing command dispatch keyed by real `device_id`.

- [x] **Step 5: Verify**

Run:

```bash
.venv/bin/pytest tests/test_protocol.py tests/test_devices_api.py -q
```

Expected: all selected tests pass.

### Task 2: 固件 BLE 控制兜底骨架

**Files:**
- Create: `firmware/main/ble_gateway.h`
- Create: `firmware/main/ble_gateway.c`
- Modify: `firmware/main/CMakeLists.txt`
- Modify: `firmware/main/Kconfig.projbuild`
- Modify: `firmware/main/app_main.c`

- [x] **Step 1: Add build switch**

Add `VR_BLE_FALLBACK_ENABLE`, default `y`, so BLE fallback can be disabled if app size exceeds the 2MB flash budget.

- [x] **Step 2: Create BLE gateway module**

Expose:

```c
esp_err_t voice_ble_gateway_start(void);
```

First version behavior:
- initialize NimBLE;
- advertise as `VR-<CONFIG_VR_DEVICE_ID>`;
- expose one control write characteristic and one status notify characteristic;
- accept JSON command `{"type":"set_volume","volume":N}`;
- clamp `N` to `0-100` and call `voice_es8311_set_volume(N)`.

- [x] **Step 3: Keep firmware alive on Wi-Fi failure**

Change `app_main.c` so Wi-Fi failure posts UI error, starts BLE fallback, then keeps the main task alive instead of returning from `app_main()`.

- [x] **Step 4: Verify firmware build**

Run:

```bash
source /esp/esp-idf/export.sh
idf.py -B build_flash_live build
```

Expected: build succeeds and app partition still has enough free space.

### Task 3: 本地 BLE 调试工具

**Files:**
- Create: `tools/ble_gateway_client.py`

- [x] **Step 1: Implement a small client wrapper**

The tool should accept:

```bash
python tools/ble_gateway_client.py --device esp32-001 --volume 10
```

Behavior:
- scan BLE devices whose name is `VR-esp32-001`;
- connect;
- write `{"type":"set_volume","volume":10}` to the control characteristic;
- print notification/status messages if supported.

- [x] **Step 2: Keep dependencies optional**

If `bleak` is missing, print:

```text
bleak is required: pip install bleak
```

and exit with code `2`.

### Task 4: Documentation and progress

**Files:**
- Modify: `docs/prd/esp32-s3-technical-solution.md`
- Modify: `docs/progress/WORK-IN-PROGRESS.md`

- [x] **Step 1: PRD**

Record Wi-Fi priority, BLE phone gateway fallback, first-stage scope, and second-stage audio scope.

- [x] **Step 2: Progress**

Record what was implemented, what was verified, and what still requires phone-side or Mac-side BLE testing.
