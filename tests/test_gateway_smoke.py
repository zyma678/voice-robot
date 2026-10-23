import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "gateway_smoke.py"
SPEC = importlib.util.spec_from_file_location("gateway_smoke", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
gateway_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway_smoke)


def test_normalize_base_url_supports_http_input() -> None:
    http_base, ws_url = gateway_smoke.normalize_base_url("http://127.0.0.1:8080/")

    assert http_base == "http://127.0.0.1:8080"
    assert ws_url == "ws://127.0.0.1:8080/ws"


def test_normalize_base_url_supports_ws_input() -> None:
    http_base, ws_url = gateway_smoke.normalize_base_url("ws://127.0.0.1:8090")

    assert http_base == "http://127.0.0.1:8090"
    assert ws_url == "ws://127.0.0.1:8090/ws"


def test_build_auth_payload_includes_gateway_metadata() -> None:
    payload = gateway_smoke.build_auth_payload(
        device_id="esp32-001",
        token="local-token",
        gateway_id="phone-dev",
    )

    assert payload == {
        "type": "auth",
        "device_id": "esp32-001",
        "token": "local-token",
        "transport": "ble_mobile_gateway",
        "gateway_id": "phone-dev",
    }


def test_build_volume_url_escapes_device_id() -> None:
    url = gateway_smoke.build_volume_url("http://127.0.0.1:8080", "esp32 001")

    assert (
        url
        == "http://127.0.0.1:8080/api/devices/esp32%20001/volume"
    )
