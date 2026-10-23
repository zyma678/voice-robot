import json
import importlib.util
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "ble_gateway_client.py"
SPEC = importlib.util.spec_from_file_location("ble_gateway_client", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
ble_gateway_client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ble_gateway_client)


def test_build_set_volume_command_clamps_and_serializes() -> None:
    payload = json.loads(ble_gateway_client.build_set_volume_command(180).decode("utf-8"))

    assert payload == {"type": "set_volume", "volume": 100}


def test_target_device_name_uses_vr_prefix() -> None:
    assert ble_gateway_client.target_device_name("esp32-001") == "VR-esp32-001"


def test_target_device_names_include_full_and_short_forms() -> None:
    assert ble_gateway_client.target_device_names("esp32-001") == (
        "VR-esp32-001",
        "VR-001",
    )


def test_ble_match_accepts_short_advertised_name_with_service_uuid() -> None:
    device = SimpleNamespace(name=None)
    advertisement = SimpleNamespace(
        local_name="VR-001",
        service_uuids=[ble_gateway_client.SERVICE_UUID],
    )

    assert ble_gateway_client.matches_target_device("esp32-001", device, advertisement)


def test_ble_match_accepts_service_uuid_even_if_name_missing() -> None:
    device = SimpleNamespace(name=None)
    advertisement = SimpleNamespace(
        local_name=None,
        service_uuids=[ble_gateway_client.SERVICE_UUID],
    )

    assert ble_gateway_client.matches_target_device("esp32-001", device, advertisement)


def test_ble_match_rejects_unrelated_device_without_service_uuid() -> None:
    device = SimpleNamespace(name="Headphones")
    advertisement = SimpleNamespace(
        local_name="Headphones",
        service_uuids=[],
    )

    assert not ble_gateway_client.matches_target_device("esp32-001", device, advertisement)


def test_ble_client_reports_missing_bleak_cleanly() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            "tools/ble_gateway_client.py",
            "--device",
            "esp32-001",
            "--volume",
            "1",
            "--timeout",
            "0.01",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "bleak is required: pip install bleak" in completed.stderr
