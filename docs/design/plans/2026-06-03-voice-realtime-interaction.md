# Voice Realtime Interaction (Barge-In) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 FastAPI `/ws` 语音链路上接入“实时语音交互（HTTP+SSE）”，支持 `barge_in` 可打断；Web 控制台可配置语音 API key 并完成 Web 端语音联调；后续硬件端可复用同一套 `/ws` 协议。

**Architecture:** `/ws` 仍为统一端侧协议（Web/硬件复用），后端新增 `RealtimeSpeechService` 适配 Doubao 实时语音交互（HTTP 上行 + SSE 下行），并在 `/ws` 侧实现会话状态机与 cancel。语音 API key 通过 runtime config 落盘并在 Web 控制台可编辑/隐藏/显示。

**Tech Stack:** Python 3.11、FastAPI、pytest、JSONL 文件存储、原生 Web JS/CSS、HTTP streaming（SSE）客户端（httpx）、WebAudio + VAD + Opus Encoder (WASM)。

---

## Inputs (Specs)

- 设计：`docs/design/specs/voice-interaction-module-design.md`
- PRD 增量：`docs/prd/esp32-s3-technical-solution.md`（11.9）

## File Map (Create / Modify)

Create:
- `voice_robot/services/speech_realtime.py`：实时语音交互适配（HTTP+SSE），会话对象支持 `cancel()`、事件迭代器。
- `voice_robot/services/opus_codec.py`：Opus(16k) -> PCM16k s16le mono 解码（服务端）。
- `tests/test_speech_realtime.py`：SSE 事件解析与 cancel 行为的单元测试（MockTransport）。
- `tests/test_ws_barge_in.py`：`/ws` barge_in 打断的回归测试（不依赖真实上游）。
- `web_console/vendor/opus/`：浏览器 Opus encoder 的静态资源（js/wasm/worker）。

Modify:
- `voice_robot/runtime_config.py`：增加 speech 运行态配置读写与 mask/reveal 支持。
- `voice_robot/admin_api.py`：新增 `/api/settings/speech` 配置接口；status 增加 speech 配置概览字段。
- `voice_robot/protocol.py`：新增 `barge_in` 控制消息；`tts_end` 增加 `reason` 可选字段；可选 asr_partial。
- `voice_robot/server.py`：实现 barge_in、实时语音交互接入、状态机与 cancel；TTS 下行 reason。
- `web_console/index.html`：新增“语音配置”与“语音联调”区域（麦克风/推送式联调）。
- `web_console/app.js`：新增 speech settings 编辑/隐藏/显示；新增语音联调（mic capture + VAD + opus send + pcm playback + barge_in）。
- `web_console/styles.css`：补齐语音联调布局样式。
- `tests/test_admin_api.py`：speech settings API 覆盖；status 字段覆盖。
- `tests/test_web_console_assets.py`：新增静态入口断言。
- `docs/progress/WORK-IN-PROGRESS.md`：更新进展与测试结果。

---

### Task 1: Add Runtime Speech Settings (Config + Masking)

**Files:**
- Modify: `voice_robot/runtime_config.py`
- Modify: `voice_robot/admin_api.py`
- Test: `tests/test_admin_api.py`

- [ ] **Step 1: Add failing tests for speech settings endpoints**

Append to `tests/test_admin_api.py`:

```python
def test_speech_settings_roundtrip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()
    client = TestClient(app)

    # default: unset
    resp = client.get("/api/settings/speech")
    assert resp.status_code == 200
    assert resp.json()["configured"] is False
    assert resp.json()["api_key"] == ""
    assert resp.json()["api_key_masked"] == ""

    # update
    resp = client.put("/api/settings/speech", json={"api_key": "sk-speech-123", "base_url": ""})
    assert resp.status_code == 200
    assert resp.json()["configured"] is True
    assert resp.json()["api_key"] == ""
    assert resp.json()["api_key_masked"].startswith("sk")

    # reveal
    resp = client.get("/api/settings/speech?reveal=true")
    assert resp.status_code == 200
    assert resp.json()["api_key"] == "sk-speech-123"
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `.venv/bin/python -m pytest tests/test_admin_api.py::test_speech_settings_roundtrip -q`
Expected: FAIL (404 / missing endpoint).

- [ ] **Step 3: Implement RuntimeConfigStore speech settings**

Edit `voice_robot/runtime_config.py`:

```python
def resolve_speech_api_key(settings: Settings, store: RuntimeConfigStore) -> tuple[str, str]:
    data = store.load()
    speech = data.get("speech")
    if isinstance(speech, dict):
        api_key = str(speech.get("api_key", "") or "").strip()
        if api_key:
            return api_key, "runtime_override"
    return "", "unset"

def resolve_speech_base_url(store: RuntimeConfigStore, default_value: str = "") -> tuple[str, str]:
    data = store.load()
    speech = data.get("speech")
    if isinstance(speech, dict):
        base_url = str(speech.get("base_url", "") or "").strip().rstrip("/")
        if base_url:
            return base_url, "runtime_override"
    return default_value, "default"

def save_speech_settings(store: RuntimeConfigStore, *, api_key: str, base_url: str) -> None:
    data = store.load()
    speech = data.get("speech")
    if not isinstance(speech, dict):
        speech = {}
    api_key = api_key.strip()
    base_url = base_url.strip().rstrip("/")
    if api_key:
        speech["api_key"] = api_key
    else:
        speech.pop("api_key", None)
    if base_url:
        speech["base_url"] = base_url
    else:
        speech.pop("base_url", None)
    if speech:
        data["speech"] = speech
    else:
        data.pop("speech", None)
    store.save(data)
```

Notes:
- 运行态 speech 配置写入 `runtime/config/console_settings.json`，与 LLM settings 同文件。
- `mask_secret()` 复用现有函数。

- [ ] **Step 4: Add /api/settings/speech endpoints**

Edit `voice_robot/admin_api.py`:

1) Add Pydantic model:

```python
class SpeechSettingsUpdate(BaseModel):
    api_key: str = Field(default="", max_length=4096)
    base_url: str = Field(default="", max_length=2048)
```

2) Add endpoints (pattern copied from `/api/settings/llm`):

```python
@router.get("/settings/speech")
async def get_speech_settings(reveal: bool = Query(default=False)) -> dict[str, str | bool]:
    store = _runtime_config_store()
    api_key, source = resolve_speech_api_key(get_settings(), store)
    base_url, base_url_source = resolve_speech_base_url(store, "")
    return {
        "configured": bool(api_key),
        "api_key_source": source,
        "api_key_masked": mask_secret(api_key),
        "api_key": api_key if reveal else "",
        "base_url": base_url,
        "base_url_source": base_url_source,
    }

@router.put("/settings/speech")
async def update_speech_settings(payload: SpeechSettingsUpdate) -> dict[str, str | bool]:
    store = _runtime_config_store()
    save_speech_settings(store, api_key=payload.api_key, base_url=payload.base_url)
    api_key, source = resolve_speech_api_key(get_settings(), store)
    base_url, base_url_source = resolve_speech_base_url(store, "")
    return {
        "configured": bool(api_key),
        "api_key_source": source,
        "api_key_masked": mask_secret(api_key),
        "api_key": "",
        "base_url": base_url,
        "base_url_source": base_url_source,
        "message": "Speech settings updated",
    }
```

- [ ] **Step 5: Run test and verify pass**

Run: `.venv/bin/python -m pytest tests/test_admin_api.py::test_speech_settings_roundtrip -q`
Expected: PASS.

- [ ] **Step 6: Add status fields (optional but recommended)**

Update `/api/status` response to include:
- `speech_api_key_configured`
- `speech_api_key_masked`
- `speech_api_key_source`
- `speech_base_url` + source

Add/adjust tests accordingly.

---

### Task 2: Extend /ws Protocol for Barge-In and TTS End Reason

**Files:**
- Modify: `voice_robot/protocol.py`
- Modify: `voice_robot/server.py`
- Test: `tests/test_protocol.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Add failing protocol tests**

Edit `tests/test_protocol.py` (append):

```python
def test_client_barge_in_message_is_parsed() -> None:
    msg = ClientControlMessage.model_validate_json(
        '{"type":"barge_in","reason":"vad_start"}'
    )
    assert msg.type == "barge_in"
    assert msg.reason == "vad_start"
```

- [ ] **Step 2: Run test to confirm failure**

Run: `.venv/bin/python -m pytest tests/test_protocol.py::test_client_barge_in_message_is_parsed -q`
Expected: FAIL (validation error / unknown type).

- [ ] **Step 3: Implement protocol changes**

Edit `voice_robot/protocol.py`:

1) Extend server message schema:

```python
class ClientBargeInMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["barge_in"]
    reason: str = "vad_start"
```

2) Add into `ClientMessage` union.

3) Extend `ServerControlMessage` to allow `reason`:

```python
reason: str | None = None
```

- [ ] **Step 4: Update server tests to accept reason field**

Update `tests/test_server.py` expectations where needed (tts_end may now include reason).

- [ ] **Step 5: Run protocol and server tests**

Run:
- `.venv/bin/python -m pytest tests/test_protocol.py -q`
- `.venv/bin/python -m pytest tests/test_server.py -q`
Expected: PASS.

---

### Task 3: Add RealtimeSpeechService (HTTP+SSE) with Mocked Tests

**Files:**
- Create: `voice_robot/services/speech_realtime.py`
- Test: `tests/test_speech_realtime.py`
- Modify: `pyproject.toml` (if httpx is missing)

- [ ] **Step 1: Add failing unit test for SSE event parsing**

Create `tests/test_speech_realtime.py`:

```python
import pytest

from voice_robot.services.speech_realtime import parse_sse_lines


def test_parse_sse_lines_extracts_events() -> None:
    lines = [
        "event: asr.partial",
        "data: {\"text\":\"你\"}",
        "",
        "event: asr.final",
        "data: {\"text\":\"你好\"}",
        "",
    ]
    events = list(parse_sse_lines(lines))
    assert events[0]["type"] == "asr_partial"
    assert events[0]["text"] == "你"
    assert events[1]["type"] == "asr_final"
    assert events[1]["text"] == "你好"
```

- [ ] **Step 2: Run the test to confirm failure**

Run: `.venv/bin/python -m pytest tests/test_speech_realtime.py -q`
Expected: FAIL (module/function missing).

- [ ] **Step 3: Implement minimal parser and session skeleton**

Create `voice_robot/services/speech_realtime.py`:

```python
import json
from dataclasses import dataclass
from typing import AsyncIterator, Iterable


def parse_sse_lines(lines: Iterable[str]):
    event = ""
    data = ""
    for line in lines:
        line = line.rstrip("\n")
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = line.split(":", 1)[1].strip()
        elif line == "":
            if not event:
                continue
            payload = {}
            if data:
                payload = json.loads(data)
            yield _map_event(event, payload)
            event = ""
            data = ""


def _map_event(event: str, payload: dict) -> dict:
    if event == "asr.partial":
        return {"type": "asr_partial", "text": str(payload.get("text", ""))}
    if event == "asr.final":
        return {"type": "asr_final", "text": str(payload.get("text", ""))}
    if event == "tts.end":
        return {"type": "tts_end", "reason": str(payload.get("reason", "natural_end"))}
    return {"type": "unknown", "event": event, "payload": payload}
```

- [ ] **Step 4: Run tests and verify pass**

Run: `.venv/bin/python -m pytest tests/test_speech_realtime.py -q`
Expected: PASS.

- [ ] **Step 5: Add HTTP client integration (mock transport)**

Extend tests with `httpx.MockTransport` and implement an async method that streams and yields parsed events.

Notes:
- Prefer `httpx.AsyncClient.stream()` + `aiter_lines()`.
- Tests must not require real network.

---

### Task 4: Implement Opus Decoder Service-Side (Opus->PCM16k)

**Files:**
- Create: `voice_robot/services/opus_codec.py`
- Test: `tests/test_opus_codec.py`
- Modify: `pyproject.toml` (add dependency)

- [ ] **Step 1: Add failing decoder test (roundtrip using known test vector)**

Create `tests/test_opus_codec.py` with a small embedded Opus packet test vector (base64) and expected PCM length.

Example shape:

```python
import base64
from voice_robot.services.opus_codec import OpusDecoder16k


def test_opus_decoder_decodes_packet() -> None:
    decoder = OpusDecoder16k()
    packet = base64.b64decode("...")  # 20ms/16k mono packet
    pcm = decoder.decode(packet)
    assert isinstance(pcm, (bytes, bytearray))
    assert len(pcm) > 0
    assert len(pcm) % 2 == 0  # s16le
```

- [ ] **Step 2: Choose implementation approach and lock dependency**

Preferred:
- `opuslib` (requires libopus present on system).

Alternative (if libopus not available):
- Use `ffmpeg` subprocess for decode (slower, but reliable on dev machine).

Pick one and implement it fully; update `pyproject.toml` accordingly.

- [ ] **Step 3: Implement OpusDecoder16k**

Expose:
- `decode(packet: bytes) -> bytes` returning PCM s16le 16k mono.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_opus_codec.py -q`
Expected: PASS.

---

### Task 5: Wire /ws to RealtimeSpeechService + Barge-In Cancel

**Files:**
- Modify: `voice_robot/server.py`
- Modify: `voice_robot/services/asr.py` (optional: keep local fallback)
- Modify: `voice_robot/services/tts.py` (optional: keep local fallback)
- Test: `tests/test_ws_barge_in.py`

- [ ] **Step 1: Add failing ws barge-in test**

Create `tests/test_ws_barge_in.py`:

```python
from fastapi.testclient import TestClient
from voice_robot.config import get_settings
from voice_robot.server import app


def test_ws_barge_in_stops_tts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json"))
    get_settings.cache_clear()

    # monkeypatch realtime session to stream many pcm chunks slowly
    import voice_robot.server as server_mod

    class FakeSession:
        def __init__(self):
            self.cancelled = False
        async def run_once(self, user_text: str):
            # yield one asr_result then keep producing tts chunks
            yield {"type": "asr_final", "text": user_text}
            for _ in range(50):
                if self.cancelled:
                    break
                yield {"type": "tts_pcm", "bytes": b\"\\x00\\x00\" * 160}
            yield {"type": "tts_end", "reason": "barge_in" if self.cancelled else "natural_end"}
        def cancel(self, reason: str):
            self.cancelled = True

    server_mod._realtime_session_factory = lambda: FakeSession()

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "web-dev", "token": "<your-token>"})
        assert ws.receive_json()["type"] == "auth_ok"

        ws.send_json({"type": "audio_start", "user_id": "xiaoming"})
        ws.send_bytes(b"fake-opus")
        ws.send_json({"type": "audio_end"})

        # Once TTS starts, barge in should stop it quickly.
        while ws.receive_json()["type"] != "tts_start":
            pass
        ws.send_json({"type": "barge_in", "reason": "vad_start"})
        end = ws.receive_json()
        assert end["type"] == "tts_end"
        assert end.get("reason") == "barge_in"
```

- [ ] **Step 2: Run test to confirm failure**

Run: `.venv/bin/python -m pytest tests/test_ws_barge_in.py -q`
Expected: FAIL (no barge_in behavior).

- [ ] **Step 3: Implement barge_in handling in /ws**

In `voice_robot/server.py`:
- Accept `barge_in` control:
  - If currently speaking: cancel the active realtime session playback and send `tts_end(reason=barge_in)` immediately.
- Introduce minimal per-connection state:
  - `current_speaking = False`
  - `current_session` (realtime session)

For W1 correctness, allow tests to inject `_realtime_session_factory` to avoid real upstream calls.

- [ ] **Step 4: Run ws test and existing ws tests**

Run:
- `.venv/bin/python -m pytest tests/test_ws_barge_in.py -q`
- `.venv/bin/python -m pytest tests/test_server.py -q`
Expected: PASS.

---

### Task 6: Web Console Speech Settings UI (API key edit/hide/show)

**Files:**
- Modify: `web_console/index.html`
- Modify: `web_console/app.js`
- Modify: `web_console/styles.css`
- Test: `tests/test_web_console_assets.py`

- [ ] **Step 1: Add failing assets test for speech settings**

Append to `tests/test_web_console_assets.py`:

```python
def test_console_speech_settings_assets_present() -> None:
    client = TestClient(app)
    page = client.get("/console")
    script = client.get("/console/assets/app.js")
    assert "settings/speech" in script.text
    assert "speech" in page.text
```

- [ ] **Step 2: Implement UI**

Add a “语音配置” section under `模型配置` Tab:
- `GET /api/settings/speech` load
- 支持 `编辑/取消/保存`
- 支持 `显示/隐藏` reveal 模式（`?reveal=true`）

- [ ] **Step 3: Run test**

Run: `.venv/bin/python -m pytest tests/test_web_console_assets.py::test_console_speech_settings_assets_present -q`
Expected: PASS.

---

### Task 7: Web Console Voice Test Page (Mic + VAD + Opus Send + PCM Playback)

**Files:**
- Modify: `web_console/index.html`
- Modify: `web_console/app.js`
- Create: `web_console/vendor/opus/*`
- Test: `tests/test_web_console_assets.py`

- [ ] **Step 1: Add UI skeleton for voice test**

Add a “语音联调” panel in `实时调试` Tab (or a new tab `语音联调`), including:
- Connect WS + auth
- Start Mic / Stop Mic
- VAD enable toggle
- Live ASR text output area
- Live TTS playback state

- [ ] **Step 2: Integrate Opus encoder (WASM)**

Add static assets (checked into repo) and implement:
- WebAudio capture -> resample to 16k mono
- 20ms framing -> Opus encode -> ws binary send

- [ ] **Step 3: PCM playback**

Implement PCM16k s16le playback queue; on `tts_end(reason=barge_in)` clear & stop within 200ms.

- [ ] **Step 4: Asset tests**

Add assertions that vendor assets are served and code references exist.

---

### Task 8: Docs + Full Regression

**Files:**
- Modify: `docs/progress/WORK-IN-PROGRESS.md`
- Modify: `docs/prd/esp32-s3-technical-solution.md` (update “开发落地状态” if needed)

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (with 1 warning allowed).

- [ ] **Step 2: Update docs**

Update `docs/progress/WORK-IN-PROGRESS.md` with:
- 已完成：语音 API key 配置、barge_in、realtime 语音接入、Web 语音联调入口
- 测试结果行

---

## Plan Self-Review Checklist

- Spec coverage: `docs/design/specs/voice-interaction-module-design.md` 的协议扩展、barge_in、16k/Opus/PCM、API key 配置、200ms 止播，都有对应 task。
- Placeholder scan: no “TODO/TBD” in plan steps; decode 依赖选择在 Task 4 中明确要求“pick one and implement fully”。
- Type consistency: `barge_in`、`tts_end.reason`、settings endpoints 路径一致。

## Execution Handoff

Plan complete and saved to `docs/design/plans/2026-06-03-voice-realtime-interaction.md`.

Two execution options:
1. Subagent-Driven (recommended) - dispatch a fresh subagent per task, review between tasks
2. Inline Execution - execute tasks in this session using executing-plans

Which approach?
