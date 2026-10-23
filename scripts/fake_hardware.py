"""Fake ESP32-S3 hardware client for end-to-end debugging.

Pretends to be a hardware device: connects to /ws, authenticates with a
configurable device_id, sends heartbeats, and prints every server-pushed
event to stdout. Lets you verify the console -> backend -> device link
before the real firmware is ready.

Usage:
    .venv/bin/python scripts/fake_hardware.py \\
        --base ws://127.0.0.1:8090 \\
        --device-id esp32-fake-001 \\
        --token "$VOICE_ROBOT_AUTH_TOKEN"

Then in another terminal open /console, pick the device in 「硬件设备」
dropdown, click 「下发播报」 — this script will print the inbound turn.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

import websockets


def log(*parts: object) -> None:
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}]", *parts, flush=True)


async def heartbeat_loop(ws: "websockets.WebSocketClientProtocol", interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await ws.send(json.dumps({"type": "heartbeat"}))
            log("→ heartbeat")
        except websockets.ConnectionClosed:
            return


async def run(base: str, device_id: str, token: str, heartbeat: float) -> int:
    url = base.rstrip("/") + "/ws"
    log(f"connecting {url} as device_id={device_id}")
    async with websockets.connect(url, max_size=4 * 1024 * 1024) as ws:
        await ws.send(
            json.dumps({"type": "auth", "device_id": device_id, "token": token})
        )
        log(f"→ auth device_id={device_id}")

        hb_task = asyncio.create_task(heartbeat_loop(ws, heartbeat))
        try:
            audio_bytes = 0
            async for raw in ws:
                if isinstance(raw, bytes):
                    audio_bytes += len(raw)
                    # Print only the first chunk and a periodic counter to
                    # avoid flooding the terminal during long TTS playbacks.
                    if audio_bytes <= len(raw) or audio_bytes % (8 * 1024) < len(raw):
                        log(f"← binary audio chunk: total={audio_bytes} bytes")
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    log(f"← <invalid json> {raw!r}")
                    continue
                kind = payload.get("type", "")
                if kind == "tts_start":
                    audio_bytes = 0
                    log(f"← tts_start (source={payload.get('source')})")
                elif kind == "tts_end":
                    log(
                        "← tts_end "
                        f"reason={payload.get('reason')} "
                        f"latency_ms={payload.get('latency_ms')} "
                        f"total_audio_bytes={audio_bytes}"
                    )
                elif kind == "llm_reply_delta":
                    sys.stdout.write(payload.get("text", ""))
                    sys.stdout.flush()
                elif kind == "llm_reply_end":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    log(
                        f"← llm_reply_end source={payload.get('source')}"
                        f" detail={payload.get('detail')}"
                    )
                else:
                    log(f"← {kind} {payload}")
        except websockets.ConnectionClosed as exc:
            log(f"connection closed: code={exc.code} reason={exc.reason!r}")
            return 1
        finally:
            hb_task.cancel()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default="ws://127.0.0.1:8090",
        help="Backend base URL (e.g. ws://127.0.0.1:8090 or wss://example.com)",
    )
    parser.add_argument(
        "--device-id",
        default="esp32-fake-001",
        help='Device id used during /ws auth (anything other than "web-console" is treated as hardware)',
    )
    parser.add_argument(
        "--token",
        default=os.getenv("VOICE_ROBOT_AUTH_TOKEN", ""),
        help="Auth token; must match settings.auth_token on the server side",
    )
    parser.add_argument(
        "--heartbeat",
        type=float,
        default=15.0,
        help="Heartbeat interval in seconds (0 disables)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.token:
        raise SystemExit("auth token required: set VOICE_ROBOT_AUTH_TOKEN or pass --token")
    try:
        return asyncio.run(
            run(args.base, args.device_id, args.token, args.heartbeat)
        )
    except KeyboardInterrupt:
        log("interrupted")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
