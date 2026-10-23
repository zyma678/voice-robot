import anyio
import time
from fastapi.testclient import TestClient

from voice_robot import server
from voice_robot.config import get_settings
from voice_robot.server import app


def test_devices_endpoint_empty_by_default() -> None:
    client = TestClient(app)

    response = client.get("/api/devices")

    assert response.status_code == 200
    data = response.json()
    assert data["hardware_online"] == 0
    assert data["console_online"] == 0
    assert data["devices"] == []


def test_hardware_registers_and_unregisters_on_ws() -> None:
    client = TestClient(app)
    token = get_settings().auth_token

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "esp32-001", "token": token})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        data = client.get("/api/devices").json()
        assert data["hardware_online"] == 1
        assert any(item["device_id"] == "esp32-001" for item in data["devices"])

    # After the connection closes the device should drop out.
    assert client.get("/api/devices").json()["hardware_online"] == 0


def test_ble_gateway_connection_exposes_transport_metadata() -> None:
    client = TestClient(app)
    token = get_settings().auth_token

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "auth",
                "device_id": "esp32-ble",
                "token": token,
                "transport": "ble_mobile_gateway",
                "gateway_id": "phone-dev",
            }
        )
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        data = client.get("/api/devices").json()
        matching = [
            item for item in data["devices"] if item["device_id"] == "esp32-ble"
        ]
        assert matching
        assert matching[0]["kind"] == "hardware"
        assert matching[0]["transport"] == "ble_mobile_gateway"
        assert matching[0]["gateway_id"] == "phone-dev"


def test_command_routing_prefers_wifi_direct_over_ble_gateway() -> None:
    send_ble, recv_ble = anyio.create_memory_object_stream(4)
    send_wifi, recv_wifi = anyio.create_memory_object_stream(4)

    server._register_connection(
        1001,
        "esp32-shared",
        transport="ble_mobile_gateway",
        gateway_id="phone-dev",
        command_send=send_ble,
    )
    server._register_connection(
        1002,
        "esp32-shared",
        transport="wifi_direct",
        command_send=send_wifi,
    )
    try:
        selected = server._command_send_for_device("esp32-shared")
        assert selected is send_wifi
        send_wifi.send_nowait({"type": "set_volume", "volume": 17})
        assert recv_wifi.receive_nowait() == {"type": "set_volume", "volume": 17}
        try:
            recv_ble.receive_nowait()
            raise AssertionError("ble gateway should not receive the preferred command")
        except anyio.WouldBlock:
            pass
    finally:
        server._unregister_connection(1001)
        server._unregister_connection(1002)


def test_command_routing_falls_back_to_ble_gateway_when_wifi_missing() -> None:
    send_ble, recv_ble = anyio.create_memory_object_stream(4)

    server._register_connection(
        1003,
        "esp32-shared-fallback",
        transport="ble_mobile_gateway",
        gateway_id="phone-dev",
        command_send=send_ble,
    )
    try:
        selected = server._command_send_for_device("esp32-shared-fallback")
        assert selected is send_ble
        send_ble.send_nowait({"type": "set_volume", "volume": 29})
        assert recv_ble.receive_nowait() == {"type": "set_volume", "volume": 29}
    finally:
        server._unregister_connection(1003)


def test_color_test_command_routes_to_online_device() -> None:
    send, recv = anyio.create_memory_object_stream(4)
    server._register_connection(
        10031,
        "esp32-color",
        transport="wifi_direct",
        command_send=send,
    )
    client = TestClient(app)
    try:
        response = client.post("/api/devices/esp32-color/color-test", json={"color": "red"})

        assert response.status_code == 200
        assert response.json() == {
            "dispatched": True,
            "device_id": "esp32-color",
            "color": "red",
        }
        assert recv.receive_nowait() == {"type": "show_color_test", "color": "red"}
    finally:
        server._unregister_connection(10031)


def test_color_test_rejects_unknown_color() -> None:
    client = TestClient(app)

    response = client.post("/api/devices/ghost/color-test", json={"color": "cyan"})

    assert response.status_code == 400


def test_command_routing_prefers_most_recently_touched_wifi_connection() -> None:
    send_old, recv_old = anyio.create_memory_object_stream(4)
    send_new, recv_new = anyio.create_memory_object_stream(4)

    server._register_connection(
        1004,
        "esp32-shared-touch",
        transport="wifi_direct",
        command_send=send_old,
    )
    server._register_connection(
        1005,
        "esp32-shared-touch",
        transport="wifi_direct",
        command_send=send_new,
    )
    try:
        # Move the older connection to the front by touching it after both are online.
        server._touch_connection(1004)
        selected = server._command_send_for_device("esp32-shared-touch")
        assert selected is send_old
        send_old.send_nowait({"type": "set_volume", "volume": 33})
        assert recv_old.receive_nowait() == {"type": "set_volume", "volume": 33}
        try:
            recv_new.receive_nowait()
            raise AssertionError("newer untouched wifi connection should not win")
        except anyio.WouldBlock:
            pass
    finally:
        server._unregister_connection(1004)
        server._unregister_connection(1005)


def test_websocket_heartbeat_updates_last_seen() -> None:
    client = TestClient(app)
    token = get_settings().auth_token

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "esp32-touch", "token": token})
        assert ws.receive_json() == {"type": "auth_ok", "text": "ok"}

        conn_key = next(
            key
            for key, entry in server._online_connections.items()
            if entry["device_id"] == "esp32-touch"
        )
        before = float(server._online_connections[conn_key]["last_seen"])
        time.sleep(0.01)

        ws.send_json({"type": "heartbeat"})
        assert ws.receive_json() == {"type": "heartbeat"}

        after = float(server._online_connections[conn_key]["last_seen"])
        assert after > before


def test_say_dispatches_turn_to_connected_device(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_ROBOT_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "VOICE_ROBOT_RUNTIME_CONFIG_PATH", str(tmp_path / "console_settings.json")
    )
    get_settings.cache_clear()
    client = TestClient(app)
    token = get_settings().auth_token

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "esp32-say", "token": token})
        ws.receive_json()

        response = client.post("/api/devices/esp32-say/say", json={"text": "你好硬件"})
        assert response.status_code == 200
        assert response.json()["dispatched"] is True

        seen: list[str] = []
        for _ in range(4):
            message = ws.receive()
            text = message.get("text")
            if text:
                import json

                seen.append(json.loads(text).get("type"))
            elif message.get("bytes") is not None:
                seen.append("binary")
            if "llm_reply_start" in seen:
                break
        assert "text_input_ack" in seen
        assert "llm_reply_start" in seen


def test_say_to_offline_device_returns_409() -> None:
    client = TestClient(app)

    response = client.post("/api/devices/ghost/say", json={"text": "x"})

    assert response.status_code == 409


def test_say_requires_text() -> None:
    client = TestClient(app)

    response = client.post("/api/devices/anything/say", json={"text": "  "})

    assert response.status_code == 400


def test_volume_dispatches_command_to_connected_device() -> None:
    client = TestClient(app)
    token = get_settings().auth_token

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "esp32-volume", "token": token})
        ws.receive_json()

        response = client.post("/api/devices/esp32-volume/volume", json={"volume": 42})

        assert response.status_code == 200
        assert response.json() == {
            "dispatched": True,
            "device_id": "esp32-volume",
            "volume": 42,
        }
        assert ws.receive_json() == {"type": "set_volume", "volume": 42}


def test_volume_clamps_to_valid_range() -> None:
    client = TestClient(app)
    token = get_settings().auth_token

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "esp32-volume", "token": token})
        ws.receive_json()

        response = client.post("/api/devices/esp32-volume/volume", json={"volume": 180})

        assert response.status_code == 200
        assert response.json()["volume"] == 100
        assert ws.receive_json() == {"type": "set_volume", "volume": 100}


def test_volume_to_offline_device_returns_409() -> None:
    client = TestClient(app)

    response = client.post("/api/devices/ghost/volume", json={"volume": 50})

    assert response.status_code == 409


def test_conversation_dispatches_command_to_connected_device() -> None:
    client = TestClient(app)
    token = get_settings().auth_token

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "esp32-conv", "token": token})
        ws.receive_json()

        response = client.post(
            "/api/devices/esp32-conv/conversation", json={"enabled": True}
        )

        assert response.status_code == 200
        assert response.json() == {
            "dispatched": True,
            "device_id": "esp32-conv",
            "enabled": True,
        }
        assert ws.receive_json() == {"type": "set_conversation", "enabled": True}


def test_conversation_requires_boolean_enabled() -> None:
    client = TestClient(app)
    token = get_settings().auth_token

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "device_id": "esp32-conv", "token": token})
        ws.receive_json()

        response = client.post(
            "/api/devices/esp32-conv/conversation", json={"enabled": "yes"}
        )

        assert response.status_code == 400


def test_conversation_to_offline_device_returns_409() -> None:
    client = TestClient(app)

    response = client.post("/api/devices/ghost/conversation", json={"enabled": False})

    assert response.status_code == 409


def test_device_event_pub_sub() -> None:
    async def scenario() -> None:
        send, recv = anyio.create_memory_object_stream(8)
        server._subscribe_device("esp32-evt", send)
        try:
            server._publish_event(
                "esp32-evt", {"event_type": "llm_reply_start", "device_id": "esp32-evt"}
            )
            server._publish_event("other-device", {"event_type": "ignored"})
            received = recv.receive_nowait()
            assert received["event_type"] == "llm_reply_start"
        finally:
            server._unsubscribe_device("esp32-evt", send)

        # After unsubscribe nothing more is delivered.
        server._publish_event("esp32-evt", {"event_type": "tts_end"})
        try:
            recv.receive_nowait()
            raise AssertionError("should not receive after unsubscribe")
        except anyio.WouldBlock:
            pass

    anyio.run(scenario)
