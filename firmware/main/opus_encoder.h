#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Opus uplink encoder: 16kHz/mono, 20ms frames (320 samples) -> raw Opus
 * packets, application VOIP. Only compiled/used when VR_UPLINK_CODEC_OPUS.
 */
esp_err_t voice_opus_encoder_init(void);

/**
 * Encode one 20ms PCM frame (320 int16 samples) into a raw Opus packet.
 * Returns the packet length in bytes, or a negative value on error.
 */
int voice_opus_encode_frame(const int16_t *pcm, size_t sample_count,
                            uint8_t *out, size_t out_capacity);

#ifdef __cplusplus
}
#endif
