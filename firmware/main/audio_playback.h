#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef ESP_PLATFORM
#include "esp_err.h"
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef int (*voice_audio_sink_write_fn)(const uint8_t *data, size_t len, void *ctx);

typedef struct {
    voice_audio_sink_write_fn sink;
    void *sink_ctx;
    size_t bytes_written;
    bool active;
} voice_audio_playback_t;

typedef struct {
    uint8_t *storage;
    size_t capacity;
    size_t head;
    size_t tail;
    size_t size;
    size_t dropped_bytes;
} voice_audio_playback_queue_t;

void voice_audio_playback_init(voice_audio_playback_t *playback,
                               voice_audio_sink_write_fn sink,
                               void *sink_ctx);
void voice_audio_playback_start(voice_audio_playback_t *playback);
int voice_audio_playback_write(voice_audio_playback_t *playback,
                               const uint8_t *data,
                               size_t len);
void voice_audio_playback_end(voice_audio_playback_t *playback);
void voice_audio_playback_interrupt(voice_audio_playback_t *playback);
bool voice_audio_playback_is_active(const voice_audio_playback_t *playback);
size_t voice_audio_playback_bytes_written(const voice_audio_playback_t *playback);

void voice_audio_playback_queue_init(voice_audio_playback_queue_t *queue,
                                     uint8_t *storage,
                                     size_t capacity);
size_t voice_audio_playback_queue_push(voice_audio_playback_queue_t *queue,
                                       const uint8_t *data,
                                       size_t len);
size_t voice_audio_playback_queue_pop(voice_audio_playback_queue_t *queue,
                                      uint8_t *out,
                                      size_t max_len);
void voice_audio_playback_queue_clear(voice_audio_playback_queue_t *queue);
void voice_audio_playback_queue_reset(voice_audio_playback_queue_t *queue);
size_t voice_audio_playback_queue_size(const voice_audio_playback_queue_t *queue);
size_t voice_audio_playback_queue_dropped_bytes(const voice_audio_playback_queue_t *queue);

#ifdef ESP_PLATFORM
#include "driver/i2s_std.h"

esp_err_t voice_audio_playback_start_hardware(void);
void voice_audio_playback_on_tts_start(void);
void voice_audio_playback_on_tts_chunk(const uint8_t *data, size_t len);
void voice_audio_playback_on_tts_end(const char *reason);
void voice_audio_playback_on_barge_in(void);
/** Mic-side channel of the duplex I2S bus (NULL until hardware start). */
i2s_chan_handle_t voice_audio_playback_rx_channel(void);
#endif

#ifdef __cplusplus
}
#endif
