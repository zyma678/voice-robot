import asyncio
import json
import os

import websockets


async def recv_with_timeout(ws, label: str, timeout: float = 5.0):
    try:
        return await asyncio.wait_for(ws.recv(), timeout=timeout)
    except TimeoutError as exc:
        raise AssertionError(f"timed out waiting for {label}") from exc


async def recv_control(ws, expected_type: str) -> dict:
    raw = await recv_with_timeout(ws, expected_type)
    if not isinstance(raw, str):
        raise AssertionError(f"expected JSON control frame {expected_type}, got bytes")

    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"expected JSON control frame {expected_type}, got malformed JSON: {raw}") from exc

    message_type = message.get("type")
    if message_type != expected_type:
        raise AssertionError(f"expected {expected_type}, got {message_type}: {message}")

    print(raw)
    return message


async def main() -> None:
    token = os.getenv("VOICE_ROBOT_AUTH_TOKEN", "")
    if not token:
        raise SystemExit("set VOICE_ROBOT_AUTH_TOKEN before running this smoke test")
    async with websockets.connect("ws://127.0.0.1:8080/ws") as ws:
        await ws.send(json.dumps({"type": "auth", "device_id": "smoke", "token": token}))
        await recv_control(ws, "auth_ok")
        await ws.send(json.dumps({"type": "audio_start", "user_id": "default-user"}))
        await ws.send("出一道简单口算题".encode("utf-8"))
        await ws.send(json.dumps({"type": "audio_end"}))
        await recv_control(ws, "asr_result")
        await recv_control(ws, "llm_reply")
        await recv_control(ws, "tts_start")
        audio = await recv_with_timeout(ws, "audio bytes")
        if not isinstance(audio, bytes):
            raise AssertionError(f"expected audio bytes, got {type(audio).__name__}: {audio}")
        if not audio:
            raise AssertionError("expected non-empty audio bytes")
        print(f"audio_bytes={len(audio)}")
        await recv_control(ws, "tts_end")


if __name__ == "__main__":
    asyncio.run(main())
