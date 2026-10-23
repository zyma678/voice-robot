#pragma once

#ifdef ESP_PLATFORM
#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Initialize the ES7210 microphone ADC for 16kHz/16bit I2S-normal capture.
 *
 * On the Waveshare ESP32-S3-Touch-LCD-1.83 the microphone is NOT on the ES8311;
 * it is wired to a separate ES7210 4-channel ADC (I2C 0x40) that shares the same
 * I2S bus (DIN=GPIO10) and I2C bus as the ES8311. Without configuring the ES7210
 * the shared I2S RX line carries no mic data, so capture/VAD never see speech.
 *
 * The ESP32 is the I2S master (emits MCLK/BCLK/WS), so the ES7210 runs in slave
 * mode and derives its clocks from the incoming 4.096MHz MCLK (256x16kHz). */
esp_err_t voice_es7210_init_16k(void);

/* True once voice_es7210_init_16k() has completed successfully. */
bool voice_es7210_ready(void);

#ifdef __cplusplus
}
#endif
#endif
