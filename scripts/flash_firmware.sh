#!/usr/bin/env bash
# One-shot flasher for the ESP32-S3 voice-robot M0 firmware.
#
# Usage:
#   scripts/flash_firmware.sh [PORT]
#
# Environment overrides (loaded from repository .env, then shell overrides;
# written into sdkconfig before build):
#   VR_WIFI_SSID, VR_WIFI_PASSWORD, VR_WIFI_HOTSPOT_SSID,
#   VR_WIFI_HOTSPOT_PASSWORD, VR_WS_URL, VR_CLOUD_WS_URL, VR_DEVICE_ID, VR_AUTH_TOKEN,
#   VR_PROV_POP, VR_BOARD_UI_ENABLE
#
# Requires: ESP-IDF installed at ~/esp/esp-idf, cmake + ninja on PATH.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FW_DIR="$REPO_ROOT/firmware"
IDF_EXPORT="${IDF_EXPORT:-$HOME/esp/esp-idf/export.sh}"
BUILD_DIR="${BUILD_DIR:-build_flash}"

if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi
VR_AUTH_TOKEN="${VR_AUTH_TOKEN:-${VOICE_ROBOT_AUTH_TOKEN:-}}"

if [ ! -f "$IDF_EXPORT" ]; then
  echo "ERROR: ESP-IDF export.sh not found at $IDF_EXPORT" >&2
  echo "Install ESP-IDF or set IDF_EXPORT to its export.sh path." >&2
  exit 1
fi

# Auto-detect serial port if not provided.
PORT="${1:-}"
if [ -z "$PORT" ]; then
  PORT="$(ls /dev/cu.usbmodem* /dev/cu.wchusbserial* /dev/cu.SLAB_USBtoUART 2>/dev/null | head -1 || true)"
fi
if [ -z "$PORT" ]; then
  echo "ERROR: no serial port found. Plug the board in (Type-C) and retry," >&2
  echo "or pass the port explicitly: scripts/flash_firmware.sh /dev/cu.usbmodemXXXX" >&2
  exit 1
fi
echo ">> Using serial port: $PORT"

# shellcheck disable=SC1090
source "$IDF_EXPORT"

cd "$FW_DIR"

merge_config_line() {
  local key="$1"
  local value="$2"
  local config_path="$3"
  local tmp_path
  tmp_path="$(mktemp)"
  if [ -f "$config_path" ]; then
    grep -v -E "^${key}=|^# ${key} is not set$" "$config_path" > "$tmp_path" || true
  fi
  if [ -n "$value" ]; then
    printf '%s\n' "$value" >> "$tmp_path"
  fi
  mv "$tmp_path" "$config_path"
}

# Apply config overrides via sdkconfig.defaults.local (kept out of git ideally).
if [ -n "${VR_WIFI_SSID:-}${VR_WIFI_PASSWORD:-}${VR_WIFI_HOTSPOT_SSID:-}${VR_WIFI_HOTSPOT_PASSWORD:-}${VR_WS_URL:-}${VR_CLOUD_WS_URL:-}${VR_DEVICE_ID:-}${VR_AUTH_TOKEN:-}${VR_PROV_POP:-}${VR_BOARD_UI_ENABLE:-}" ]; then
  echo ">> Merging config overrides into sdkconfig.defaults.local"
  [ -n "${VR_WIFI_SSID:-}" ] && merge_config_line "CONFIG_VR_WIFI_SSID" "CONFIG_VR_WIFI_SSID=\"$VR_WIFI_SSID\"" "sdkconfig.defaults.local"
  [ -n "${VR_WIFI_PASSWORD:-}" ] && merge_config_line "CONFIG_VR_WIFI_PASSWORD" "CONFIG_VR_WIFI_PASSWORD=\"$VR_WIFI_PASSWORD\"" "sdkconfig.defaults.local"
  [ -n "${VR_WIFI_HOTSPOT_SSID:-}" ] && merge_config_line "CONFIG_VR_WIFI_HOTSPOT_SSID" "CONFIG_VR_WIFI_HOTSPOT_SSID=\"$VR_WIFI_HOTSPOT_SSID\"" "sdkconfig.defaults.local"
  [ -n "${VR_WIFI_HOTSPOT_PASSWORD:-}" ] && merge_config_line "CONFIG_VR_WIFI_HOTSPOT_PASSWORD" "CONFIG_VR_WIFI_HOTSPOT_PASSWORD=\"$VR_WIFI_HOTSPOT_PASSWORD\"" "sdkconfig.defaults.local"
  [ -n "${VR_WS_URL:-}" ] && merge_config_line "CONFIG_VR_WS_URL" "CONFIG_VR_WS_URL=\"$VR_WS_URL\"" "sdkconfig.defaults.local"
  [ -n "${VR_CLOUD_WS_URL:-}" ] && merge_config_line "CONFIG_VR_CLOUD_WS_URL" "CONFIG_VR_CLOUD_WS_URL=\"$VR_CLOUD_WS_URL\"" "sdkconfig.defaults.local"
  [ -n "${VR_DEVICE_ID:-}" ] && merge_config_line "CONFIG_VR_DEVICE_ID" "CONFIG_VR_DEVICE_ID=\"$VR_DEVICE_ID\"" "sdkconfig.defaults.local"
  [ -n "${VR_AUTH_TOKEN:-}" ] && merge_config_line "CONFIG_VR_AUTH_TOKEN" "CONFIG_VR_AUTH_TOKEN=\"$VR_AUTH_TOKEN\"" "sdkconfig.defaults.local"
  [ -n "${VR_PROV_POP:-}" ] && merge_config_line "CONFIG_VR_PROV_POP" "CONFIG_VR_PROV_POP=\"$VR_PROV_POP\"" "sdkconfig.defaults.local"
  if [ -n "${VR_BOARD_UI_ENABLE:-}" ]; then
    case "$VR_BOARD_UI_ENABLE" in
      1|y|Y|yes|YES|true|TRUE)
        merge_config_line "CONFIG_VR_BOARD_UI_ENABLE" "CONFIG_VR_BOARD_UI_ENABLE=y" "sdkconfig.defaults.local"
        ;;
      *)
        merge_config_line "CONFIG_VR_BOARD_UI_ENABLE" "# CONFIG_VR_BOARD_UI_ENABLE is not set" "sdkconfig.defaults.local"
        ;;
    esac
  fi
  # Force sdkconfig regeneration so overrides take effect.
  rm -f sdkconfig
fi

if [ -f sdkconfig ]; then
  regen_sdkconfig=0
  [ sdkconfig.defaults -nt sdkconfig ] && regen_sdkconfig=1
  [ -f sdkconfig.defaults.local ] && [ sdkconfig.defaults.local -nt sdkconfig ] && regen_sdkconfig=1
  if [ "$regen_sdkconfig" -eq 1 ]; then
    echo ">> sdkconfig is older than defaults; regenerating"
    rm -f sdkconfig
  fi
fi

echo ">> set-target esp32s3"
idf.py -B "$BUILD_DIR" set-target esp32s3

echo ">> build"
idf.py -B "$BUILD_DIR" build

echo ">> flash to $PORT"
idf.py -B "$BUILD_DIR" -p "$PORT" flash

echo ">> done. Run 'idf.py -p $PORT monitor' to watch logs (Ctrl+] to exit)."
