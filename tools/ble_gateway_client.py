#!/usr/bin/env python3
"""Small BLE control client for the ESP32-S3 voice robot fallback path."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Iterable


SERVICE_UUID = "6f4d5a9e-7d76-6f69-6365-626f74000001"
CONTROL_UUID = "6f4d5a9e-7d76-6f69-6365-626f74000002"
STATUS_UUID = "6f4d5a9e-7d76-6f69-6365-626f74000003"


def build_set_volume_command(volume: int) -> bytes:
    value = max(0, min(100, int(volume)))
    return json.dumps(
        {"type": "set_volume", "volume": value},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def target_device_name(device_id: str) -> str:
    clean = (device_id or "").strip()
    if not clean:
        raise ValueError("device id must not be empty")
    return f"VR-{clean}"


def target_device_short_name(device_id: str) -> str:
    clean = (device_id or "").strip()
    if not clean:
        raise ValueError("device id must not be empty")
    suffix = clean[-3:] if len(clean) >= 3 else clean
    return f"VR-{suffix}"


def target_device_names(device_id: str) -> tuple[str, str]:
    return target_device_name(device_id), target_device_short_name(device_id)


def _normalize_uuid(value: str | None) -> str:
    return (value or "").strip().lower()


def _service_uuids(advertisement: Any) -> Iterable[str]:
    for value in getattr(advertisement, "service_uuids", None) or ():
        normalized = _normalize_uuid(value)
        if normalized:
            yield normalized


def matches_target_device(device_id: str, device: Any, advertisement: Any) -> bool:
    target_names = set(target_device_names(device_id))
    observed_names = {
        name.strip()
        for name in (
            getattr(device, "name", None),
            getattr(advertisement, "local_name", None),
        )
        if isinstance(name, str) and name.strip()
    }
    has_service_uuid = SERVICE_UUID in set(_service_uuids(advertisement))

    if has_service_uuid:
        if not observed_names:
            return True
        return any(name in target_names or name.startswith("VR-") for name in observed_names)

    return any(name in target_names for name in observed_names)


async def _run_async(args: argparse.Namespace) -> int:
    try:
        from bleak import BleakClient, BleakScanner
    except ModuleNotFoundError:
        print("bleak is required: pip install bleak", file=sys.stderr)
        return 2

    full_name, short_name = target_device_names(args.device)
    print(f"Scanning for {full_name} (short adv name {short_name}) ...")
    device = await BleakScanner.find_device_by_filter(
        lambda dev, adv: matches_target_device(args.device, dev, adv),
        timeout=args.timeout,
    )
    if device is None:
        print(
            f"device not found: {full_name} / {short_name} "
            f"(service {SERVICE_UUID})",
            file=sys.stderr,
        )
        return 1

    command = build_set_volume_command(args.volume)

    def on_status(_: Any, data: bytearray) -> None:
        try:
            print(f"status: {data.decode('utf-8')}")
        except UnicodeDecodeError:
            print(f"status bytes: {bytes(data).hex()}")

    async with BleakClient(device) as client:
        try:
            await client.start_notify(STATUS_UUID, on_status)
        except Exception:
            # Notification is best-effort; write path is the critical control path.
            pass
        await client.write_gatt_char(CONTROL_UUID, command, response=True)
        print(command.decode("utf-8"))
        await asyncio.sleep(args.listen)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send a BLE fallback control command to a voice robot device.",
    )
    parser.add_argument("--device", required=True, help="Hardware device id, e.g. esp32-001")
    parser.add_argument("--volume", type=int, required=True, help="Volume 0-100")
    parser.add_argument("--timeout", type=float, default=8.0, help="BLE scan timeout seconds")
    parser.add_argument("--listen", type=float, default=1.0, help="Seconds to wait for status")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
