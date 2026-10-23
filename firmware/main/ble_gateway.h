#pragma once

#include <stdbool.h>
#include <stddef.h>

#ifdef ESP_PLATFORM
#include "esp_err.h"
#else
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define VOICE_BLE_GATEWAY_SERVICE_UUID "6f4d5a9e-7d76-6f69-6365-626f74000001"
#define VOICE_BLE_GATEWAY_CONTROL_UUID "6f4d5a9e-7d76-6f69-6365-626f74000002"
#define VOICE_BLE_GATEWAY_STATUS_UUID  "6f4d5a9e-7d76-6f69-6365-626f74000003"

bool voice_ble_gateway_parse_set_volume(const char *json, int *out_volume);
void voice_ble_gateway_build_short_device_name(const char *device_id,
                                               char *out,
                                               size_t out_size);
esp_err_t voice_ble_gateway_start(void);
esp_err_t voice_ble_gateway_start_background(void);

#ifdef __cplusplus
}
#endif
