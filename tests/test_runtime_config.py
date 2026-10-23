import json
import stat

import pytest

from voice_robot.config import Settings
from voice_robot.runtime_config import (
    RuntimeConfigStore,
    resolve_speech_asr_api_key,
    resolve_speech_asr_sample_rate_hz,
    resolve_speech_tts_voice_type,
)


def test_runtime_config_save_is_private_and_leaves_no_temp_file(tmp_path) -> None:
    path = tmp_path / "config" / "console_settings.json"
    store = RuntimeConfigStore(path)

    store.save({"llm": {"model": "m1"}})

    assert json.loads(path.read_text(encoding="utf-8"))["llm"]["model"] == "m1"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_runtime_config_failed_replace_keeps_previous_file(monkeypatch, tmp_path) -> None:
    path = tmp_path / "console_settings.json"
    store = RuntimeConfigStore(path)
    store.save({"version": 1})

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("voice_robot.runtime_config.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        store.save({"version": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1}
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []


def test_speech_settings_use_env_then_runtime_override(tmp_path) -> None:
    store = RuntimeConfigStore(tmp_path / "console_settings.json")
    settings = Settings(
        _env_file=None,
        asr_api_key="env-asr-key",
        asr_sample_rate_hz="24000",
        tts_voice_type="env-voice",
    )

    assert resolve_speech_asr_api_key(store, settings) == ("env-asr-key", "env")
    assert resolve_speech_asr_sample_rate_hz(store, settings=settings) == (24000, "env")
    assert resolve_speech_tts_voice_type(store, settings=settings) == ("env-voice", "env")

    store.save_speech_settings(
        asr_api_key="runtime-asr-key",
        asr_sample_rate_hz=16000,
        tts_voice_type="runtime-voice",
    )

    assert resolve_speech_asr_api_key(store, settings) == (
        "runtime-asr-key",
        "runtime_override",
    )
    assert resolve_speech_asr_sample_rate_hz(store, settings=settings) == (
        16000,
        "runtime_override",
    )
    assert resolve_speech_tts_voice_type(store, settings=settings) == (
        "runtime-voice",
        "runtime_override",
    )
