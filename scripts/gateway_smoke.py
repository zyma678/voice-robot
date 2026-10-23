"""Smoke-test the BLE mobile gateway cloud path without real BLE hardware.

Usage:
    .venv/bin/python scripts/gateway_smoke.py \
        --base http://127.0.0.1:8080 \
        --device-id esp32-001 \
        --gateway-id web-gateway-local \
        --token "$VOICE_ROBOT_AUTH_TOKEN" \
        --volume 17

What it verifies:
1. A `/ws` client can authenticate as `transport=ble_mobile_gateway`.
2. `POST /api/devices/{device_id}/volume` is routed back to that gateway
   connection as `{"type":"set_volume","volume":N}`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Tuple
from urllib.parse import quote

import httpx
import websockets


def normalize_base_url(base: str) -> Tuple[str, str]:
    normalized = base.rstrip("/")
    if normalized.startswith("ws://"):
        ws_base = normalized
        http_base = "http://" + normalized[len("ws://") :]
    elif normalized.startswith("wss://"):
        ws_base = normalized
        http_base = "https://" + normalized[len("wss://") :]
    elif normalized.startswith("http://"):
        http_base = normalized
        ws_base = "ws://" + normalized[len("http://") :]
    elif normalized.startswith("https://"):
        http_base = normalized
        ws_base = "wss://" + normalized[len("https://") :]
    else:
        http_base = f"http://{normalized}"
        ws_base = f"ws://{normalized}"
    return http_base, f"{ws_base}/ws"


def build_auth_payload(*, device_id: str, token: str, gateway_id: str) -> dict:
    return {
        "type": "auth",
        "device_id": device_id,
        "token": token,
        "transport": "ble_mobile_gateway",
        "gateway_id": gateway_id,
    }


def build_volume_url(http_base: str, device_id: str) -> str:
    return f"{http_base}/api/devices/{quote(device_id, safe='')}/volume"


async def recv_json(ws, *, timeout: float, label: str) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    if not isinstance(raw, str):
        raise AssertionError(f"expected JSON frame for {label}, got bytes")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"expected JSON frame for {label}, got: {raw}") from exc


async def main_async(args: argparse.Namespace) -> int:
    http_base, ws_url = normalize_base_url(args.base)
    auth_payload = build_auth_payload(
        device_id=args.device_id,
        token=args.token,
        gateway_id=args.gateway_id,
    )

    async with websockets.connect(ws_url, max_size=4 * 1024 * 1024) as ws:
        await ws.send(json.dumps(auth_payload))
        auth_ok = await recv_json(ws, timeout=args.timeout, label="auth_ok")
        if auth_ok.get("type") != "auth_ok":
            raise AssertionError(f"expected auth_ok, got {auth_ok}")
        print(json.dumps(auth_ok, ensure_ascii=False))

        async with httpx.AsyncClient(timeout=args.timeout) as client:
            response = await client.post(
                build_volume_url(http_base, args.device_id),
                json={"volume": args.volume},
            )
            if response.status_code != 200:
                raise AssertionError(
                    f"volume dispatch failed: {response.status_code} {response.text}"
                )
            print(response.text)

        volume_command = await recv_json(ws, timeout=args.timeout, label="set_volume")
        if volume_command != {"type": "set_volume", "volume": args.volume}:
            raise AssertionError(f"unexpected set_volume payload: {volume_command}")
        print(json.dumps(volume_command, ensure_ascii=False))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--device-id", default="esp32-001")
    parser.add_argument("--gateway-id", default="web-gateway-local")
    parser.add_argument("--token", default=os.getenv("VOICE_ROBOT_AUTH_TOKEN", ""))
    parser.add_argument("--volume", type=int, default=17)
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.token:
        raise SystemExit("auth token required: set VOICE_ROBOT_AUTH_TOKEN or pass --token")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
