#pragma once

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * I2S microphone capture: 16kHz/16bit/mono, 20ms frames pushed into the
 * conversation state machine. Shares the duplex I2S bus created by
 * voice_audio_playback_start_hardware() (call that first).
 *
 * Capture starts disabled; the conversation state machine toggles it so the
 * mic never runs while TTS is playing (half-duplex, PRD 11.13).
 */
esp_err_t voice_audio_capture_start_hardware(void);
void voice_audio_capture_set_enabled(bool enabled);
bool voice_audio_capture_enabled(void);

#ifdef __cplusplus
}
#endif
