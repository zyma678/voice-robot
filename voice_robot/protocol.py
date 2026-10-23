from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


ServerMessageType = Literal[
    "auth_ok",
    "asr_partial",
    "asr_result",
    "llm_prompt",
    "llm_reply",
    "llm_reply_start",
    "llm_reply_delta",
    "llm_reply_end",
    "tts_start",
    "tts_end",
    "barge_in",
    "set_volume",
    "set_conversation",
    "show_color_test",
    "show_character_trace",
    "show_idiom",
    "scene_changed",
    "text_input_ack",
    "text",
    "heartbeat",
    "error",
]


class ClientAuthMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["auth"]
    device_id: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=1, max_length=4096)
    transport: Literal["wifi_direct", "ble_mobile_gateway"] = "wifi_direct"
    gateway_id: str | None = Field(default=None, max_length=128)


class ClientAudioStartMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["audio_start"]
    user_id: str = Field(default="default-user", min_length=1, max_length=128)
    # pcm_s16le: streaming PCM (web + ESP32 default).
    # opus: container Opus (web MediaRecorder WebM/Ogg blob).
    # opus_raw: length-prefixed raw Opus packets (ESP32 uplink, see opus_codec).
    codec: Literal["pcm_s16le", "opus", "opus_raw"] = "pcm_s16le"
    sample_rate_hz: int = Field(default=16000, ge=8000, le=48000)


class ClientAudioEndMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["audio_end"]


class ClientHeartbeatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["heartbeat"]

class ClientBargeInMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["barge_in"]
    reason: str = Field(default="manual", max_length=64)


class ClientTextInputMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text_input"]
    user_id: str = Field(default="default-user", min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=2000)


class ClientDeviceDebugMessage(BaseModel):
    # Free-form diagnostic line pushed from firmware (serial is unreliable on
    # the USB-Serial-JTAG board). Republished to SSE monitors as device_debug.
    model_config = ConfigDict(extra="forbid")

    type: Literal["device_debug"]
    text: str = Field(min_length=1, max_length=1024)


ClientMessage = (
    ClientAuthMessage
    | ClientAudioStartMessage
    | ClientAudioEndMessage
    | ClientHeartbeatMessage
    | ClientBargeInMessage
    | ClientTextInputMessage
    | ClientDeviceDebugMessage
)


class ClientControlMessage(RootModel[ClientMessage]):
    # Route by msg.type in server code; concrete models are available via msg.root.
    def __getattr__(self, name: str) -> Any:
        return getattr(self.root, name)


class ServerControlMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ServerMessageType
    text: str | None = None
    message: str | None = None
    payload: dict | None = None
    source: str | None = None
    detail: str | None = None
    reason: str | None = None
    turn_id: str | None = None
    tts_url: str | None = None
    tts_audio_format: str | None = None
    tts_sample_rate_hz: int | None = None
    latency_ms: int | None = None
    volume: int | None = None
    enabled: bool | None = None
    color: str | None = None
    character: str | None = None
    duration_ms: int | None = None
    strokes: list | None = None
    outlines: list | None = None
    idiom: str | None = None
    pinyin: str | None = None
    glyphs: list | None = None
    scene_id: str | None = None
    scene_action: str | None = None
    scene_name: str | None = None


def encode_server_message(message: ServerControlMessage) -> dict[str, Any]:
    return message.model_dump(exclude_none=True)
