import pytest
from pydantic import ValidationError

from voice_robot.protocol import (
    ClientControlMessage,
    ServerControlMessage,
    encode_server_message,
)


def test_client_auth_message_validates_required_fields() -> None:
    msg = ClientControlMessage.model_validate(
        {"type": "auth", "device_id": "web-dev", "token": "local-token"}
    )

    assert msg.type == "auth"
    assert msg.device_id == "web-dev"
    assert msg.token == "local-token"
    assert msg.transport == "wifi_direct"
    assert msg.gateway_id is None


def test_client_auth_message_accepts_ble_gateway_metadata() -> None:
    msg = ClientControlMessage.model_validate(
        {
            "type": "auth",
            "device_id": "esp32-001",
            "token": "local-token",
            "transport": "ble_mobile_gateway",
            "gateway_id": "phone-dev",
        }
    )

    assert msg.type == "auth"
    assert msg.device_id == "esp32-001"
    assert msg.transport == "ble_mobile_gateway"
    assert msg.gateway_id == "phone-dev"


def test_client_auth_message_rejects_unknown_transport() -> None:
    with pytest.raises(ValidationError):
        ClientControlMessage.model_validate(
            {
                "type": "auth",
                "device_id": "esp32-001",
                "token": "local-token",
                "transport": "classic_bluetooth",
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "auth", "token": "local-token"},
        {"type": "auth", "device_id": "web-dev"},
    ],
)
def test_client_auth_message_requires_device_id_and_token(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ClientControlMessage.model_validate(payload)


def test_client_audio_start_defaults_user_id() -> None:
    msg = ClientControlMessage.model_validate({"type": "audio_start"})

    assert msg.type == "audio_start"
    assert msg.user_id == "default-user"
    assert msg.codec == "pcm_s16le"
    assert msg.sample_rate_hz == 16000


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "audio_end", "token": "local-token"},
        {"type": "heartbeat", "device_id": "web-dev"},
    ],
)
def test_client_messages_reject_unrelated_fields(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ClientControlMessage.model_validate(payload)


def test_rejects_unknown_client_message_type() -> None:
    with pytest.raises(ValidationError):
        ClientControlMessage.model_validate({"type": "unknown"})


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "text_input", "text": "x" * 2001},
        {"type": "audio_start", "sample_rate_hz": 96000},
        {"type": "auth", "device_id": "", "token": "local-token"},
        {"type": "device_debug", "text": "x" * 1025},
    ],
)
def test_client_messages_enforce_cost_and_resource_limits(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ClientControlMessage.model_validate(payload)


def test_server_message_serializes_type_and_text() -> None:
    payload = encode_server_message(
        ServerControlMessage(
            type="text",
            text="你好，我是小智",
            source="ark",
            detail="方舟返回成功",
        )
    )

    assert payload == {
        "type": "text",
        "text": "你好，我是小智",
        "source": "ark",
        "detail": "方舟返回成功",
    }


def test_server_streaming_reply_message_types_are_allowed() -> None:
    prompt = encode_server_message(
        ServerControlMessage(
            type="llm_prompt",
            payload={"rendered_user_prompt": "u=你好"},
        )
    )
    start = encode_server_message(ServerControlMessage(type="llm_reply_start"))
    delta = encode_server_message(
        ServerControlMessage(type="llm_reply_delta", text="你好")
    )
    end = encode_server_message(
        ServerControlMessage(type="llm_reply_end", text="你好")
    )

    assert prompt == {"type": "llm_prompt", "payload": {"rendered_user_prompt": "u=你好"}}
    assert start == {"type": "llm_reply_start"}
    assert delta == {"type": "llm_reply_delta", "text": "你好"}
    assert end == {"type": "llm_reply_end", "text": "你好"}


def test_server_color_test_message_is_supported() -> None:
    payload = encode_server_message(
        ServerControlMessage(type="show_color_test", color="bars")
    )

    assert payload == {"type": "show_color_test", "color": "bars"}


def test_client_barge_in_message_is_supported() -> None:
    msg = ClientControlMessage.model_validate({"type": "barge_in", "reason": "vad_start"})

    assert msg.type == "barge_in"
    assert msg.reason == "vad_start"


def test_server_tts_end_supports_reason_field() -> None:
    payload = encode_server_message(
        ServerControlMessage(type="tts_end", reason="barge_in", latency_ms=12)
    )

    assert payload == {"type": "tts_end", "reason": "barge_in", "latency_ms": 12}


def test_client_text_input_message_is_supported() -> None:
    msg = ClientControlMessage.model_validate(
        {"type": "text_input", "user_id": "xiaoming", "text": "你好"}
    )

    assert msg.type == "text_input"
    assert msg.user_id == "xiaoming"
    assert msg.text == "你好"


def test_server_character_trace_message_is_supported() -> None:
    payload = encode_server_message(
        ServerControlMessage(
            type="show_character_trace",
            character="十",
            duration_ms=1800,
            strokes=[
                [[180, 280], [500, 280], [820, 280]],
                [[500, 200], [500, 500], [500, 820]],
            ],
        )
    )

    assert payload == {
        "type": "show_character_trace",
        "character": "十",
        "duration_ms": 1800,
        "strokes": [
            [[180, 280], [500, 280], [820, 280]],
            [[500, 200], [500, 500], [500, 820]],
        ],
    }
