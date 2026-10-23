import os
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SOURCE = REPO_ROOT / "scripts" / "flash_firmware.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_flash_script_preserves_existing_local_config_and_uses_build_flash(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    scripts_dir = repo / "scripts"
    firmware_dir = repo / "firmware"
    fake_bin = tmp_path / "fake-bin"
    scripts_dir.mkdir(parents=True)
    firmware_dir.mkdir(parents=True)
    fake_bin.mkdir(parents=True)

    script_copy = scripts_dir / "flash_firmware.sh"
    script_copy.write_text(SCRIPT_SOURCE.read_text(encoding="utf-8"), encoding="utf-8")
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IXUSR)

    local_config = firmware_dir / "sdkconfig.defaults.local"
    local_config.write_text(
        "\n".join(
            [
                'CONFIG_VR_WIFI_SSID="keep-ssid"',
                'CONFIG_VR_WIFI_PASSWORD="keep-password"',
                'CONFIG_VR_WS_URL="ws://10.0.0.8:8090/ws"',
                'CONFIG_VR_DEVICE_ID="keep-device"',
                'CONFIG_VR_AUTH_TOKEN="keep-token"',
                "# CONFIG_VR_BOARD_UI_ENABLE is not set",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (firmware_dir / "sdkconfig").write_text("stale-sdkconfig", encoding="utf-8")

    log_path = tmp_path / "idf.log"
    export_path = tmp_path / "export.sh"
    _write_executable(
        export_path,
        "#!/usr/bin/env bash\n"
        'export PATH="$FAKE_BIN:$PATH"\n',
    )
    _write_executable(
        fake_bin / "idf.py",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$FAKE_IDF_LOG"\n',
    )

    env = os.environ.copy()
    env.pop("VOICE_ROBOT_AUTH_TOKEN", None)
    env["IDF_EXPORT"] = str(export_path)
    env["FAKE_BIN"] = str(fake_bin)
    env["FAKE_IDF_LOG"] = str(log_path)
    env["VR_BOARD_UI_ENABLE"] = "1"

    completed = subprocess.run(
        [str(script_copy), "/dev/null"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not (firmware_dir / "sdkconfig").exists()

    content = local_config.read_text(encoding="utf-8")
    assert 'CONFIG_VR_WIFI_SSID="keep-ssid"' in content
    assert 'CONFIG_VR_WIFI_PASSWORD="keep-password"' in content
    assert 'CONFIG_VR_WS_URL="ws://10.0.0.8:8090/ws"' in content
    assert 'CONFIG_VR_DEVICE_ID="keep-device"' in content
    assert 'CONFIG_VR_AUTH_TOKEN="keep-token"' in content
    assert "CONFIG_VR_BOARD_UI_ENABLE=y" in content
    assert "# CONFIG_VR_BOARD_UI_ENABLE is not set" not in content

    commands = log_path.read_text(encoding="utf-8").splitlines()
    assert commands == [
        "-B build_flash set-target esp32s3",
        "-B build_flash build",
        "-B build_flash -p /dev/null flash",
    ]


def test_flash_script_regenerates_sdkconfig_when_defaults_are_newer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    scripts_dir = repo / "scripts"
    firmware_dir = repo / "firmware"
    fake_bin = tmp_path / "fake-bin"
    scripts_dir.mkdir(parents=True)
    firmware_dir.mkdir(parents=True)
    fake_bin.mkdir(parents=True)

    script_copy = scripts_dir / "flash_firmware.sh"
    script_copy.write_text(SCRIPT_SOURCE.read_text(encoding="utf-8"), encoding="utf-8")
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IXUSR)

    sdkconfig = firmware_dir / "sdkconfig"
    sdkconfig.write_text("stale-sdkconfig", encoding="utf-8")
    defaults = firmware_dir / "sdkconfig.defaults"
    defaults.write_text("CONFIG_SPIRAM=y\n", encoding="utf-8")
    local_defaults = firmware_dir / "sdkconfig.defaults.local"
    local_defaults.write_text("CONFIG_VR_BOARD_UI_ENABLE=y\n", encoding="utf-8")

    old_time = 1_700_000_000
    new_time = old_time + 60
    os.utime(sdkconfig, (old_time, old_time))
    os.utime(defaults, (new_time, new_time))
    os.utime(local_defaults, (old_time, old_time))

    log_path = tmp_path / "idf.log"
    export_path = tmp_path / "export.sh"
    _write_executable(
        export_path,
        "#!/usr/bin/env bash\n"
        'export PATH="$FAKE_BIN:$PATH"\n',
    )
    _write_executable(
        fake_bin / "idf.py",
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$FAKE_IDF_LOG"\n',
    )

    env = os.environ.copy()
    env.pop("VOICE_ROBOT_AUTH_TOKEN", None)
    env["IDF_EXPORT"] = str(export_path)
    env["FAKE_BIN"] = str(fake_bin)
    env["FAKE_IDF_LOG"] = str(log_path)

    completed = subprocess.run(
        [str(script_copy), "/dev/null"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not sdkconfig.exists()
    assert "sdkconfig is older than defaults; regenerating" in completed.stdout
