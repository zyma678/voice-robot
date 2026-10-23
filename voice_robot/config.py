from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "voice-robot"
    default_user_id: str = "default-user"
    auth_token: str = ""
    memory_root: Path = Field(default=Path("runtime/memory"))
    llm_model: str = "doubao-seed-2-0-mini-260428"
    llm_api_key: str = ""
    llm_api_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    speech_mode: str = ""
    asr_api_key: str = ""
    asr_resource_id: str = ""
    asr_ws_url: str = ""
    asr_sample_rate_hz: str = ""
    tts_api_key: str = ""
    tts_resource_id: str = ""
    tts_ws_url: str = ""
    tts_sample_rate_hz: str = ""
    tts_encoding: str = ""
    tts_voice_type: str = ""
    runtime_config_path: Path = Field(default=Path("runtime/config/console_settings.json"))
    public_base_url: str = ""
    auto_public_tunnel: bool = True
    public_tunnel_command: str = "cloudflared"
    public_tunnel_target_url: str = "http://127.0.0.1:8090"

    model_config = SettingsConfigDict(
        env_prefix="VOICE_ROBOT_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
