#pragma once

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Bring up Wi-Fi in station mode and block until the device gets an IPv4
 * address. Credential precedence (PRD 11.14): forced re-provisioning flag ->
 * BLE provisioning; stored/provisioned credentials; menuconfig
 * CONFIG_VR_WIFI_SSID fallback; none -> blocking BLE provisioning
 * (service "PROV_<device_id>"). Returns ESP_FAIL on hard failure.
 */
esp_err_t voice_robot_wifi_start(void);

/**
 * Persist a force-provisioning flag and reboot into BLE provisioning mode.
 * Wired to a BOOT-key long press.
 */
void voice_robot_wifi_request_provisioning(void);

/**
 * Snapshot the current STA connection info for the on-device UI. Any output
 * pointer may be NULL. `ssid` should be at least 33 bytes, `ip` at least 16.
 * On success fills ssid/ip/rssi and returns true; if not connected returns
 * false and leaves outputs as empty string / 0.
 */
bool voice_wifi_get_info(char *ssid, int ssid_len, char *ip, int ip_len, int *rssi);

/* Built-in Wi-Fi presets for quick switching during development/testing. */
#define VOICE_WIFI_PRESET_PRIMARY 0  /* CONFIG_VR_WIFI_SSID / CONFIG_VR_WIFI_PASSWORD */
#define VOICE_WIFI_PRESET_HOTSPOT 1  /* optional CONFIG_VR_WIFI_HOTSPOT_* */

/**
 * Returns the currently selected Wi-Fi preset index (0 or 1). This is the last
 * user-selected preset, persisted in NVS. If the user never switched presets
 * this defaults to 0.
 */
int voice_wifi_get_preset(void);

/**
 * Select a built-in Wi-Fi preset (0 or 1), persist it, then reboot so the STA
 * reconnects using the new credentials.
 */
void voice_wifi_select_preset(int preset);

const char *voice_wifi_preset_ssid(int preset);

#ifdef __cplusplus
}
#endif
