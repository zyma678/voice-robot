#pragma once

#ifdef ESP_PLATFORM
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t voice_es8311_init_16k(void);
esp_err_t voice_es8311_set_volume(int volume);

/* Last applied playback volume (0-100). */
int voice_es8311_get_volume(void);

#ifdef __cplusplus
}
#endif
#endif
