#include "audio_playback.h"

#include <stdio.h>
#include <string.h>

#ifdef ESP_PLATFORM
#include "driver/gpio.h"
#include "driver/i2s_std.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "conversation.h"
#include "es7210_codec.h"
#include "es8311_codec.h"
#include "sdkconfig.h"
#include "ui_screen.h"
#endif

void voice_audio_playback_init(voice_audio_playback_t *playback,
                               voice_audio_sink_write_fn sink,
                               void *sink_ctx) {
    if (!playback) {
        return;
    }
    memset(playback, 0, sizeof(*playback));
    playback->sink = sink;
    playback->sink_ctx = sink_ctx;
}

void voice_audio_playback_start(voice_audio_playback_t *playback) {
    if (!playback) {
        return;
    }
    playback->active = true;
    playback->bytes_written = 0;
}

int voice_audio_playback_write(voice_audio_playback_t *playback,
                               const uint8_t *data,
                               size_t len) {
    if (!playback || !playback->active || !data || len == 0) {
        return 0;
    }
    if (!playback->sink) {
        return 0;
    }

    int written = playback->sink(data, len, playback->sink_ctx);
    if (written > 0) {
        playback->bytes_written += (size_t)written;
        return written;
    }
    return 0;
}

void voice_audio_playback_end(voice_audio_playback_t *playback) {
    if (!playback) {
        return;
    }
    playback->active = false;
}

void voice_audio_playback_interrupt(voice_audio_playback_t *playback) {
    if (!playback) {
        return;
    }
    playback->active = false;
}

bool voice_audio_playback_is_active(const voice_audio_playback_t *playback) {
    return playback && playback->active;
}

size_t voice_audio_playback_bytes_written(const voice_audio_playback_t *playback) {
    return playback ? playback->bytes_written : 0;
}

void voice_audio_playback_queue_init(voice_audio_playback_queue_t *queue,
                                     uint8_t *storage,
                                     size_t capacity) {
    if (!queue) {
        return;
    }
    memset(queue, 0, sizeof(*queue));
    queue->storage = storage;
    queue->capacity = storage ? capacity : 0;
}

size_t voice_audio_playback_queue_push(voice_audio_playback_queue_t *queue,
                                       const uint8_t *data,
                                       size_t len) {
    if (!queue || !queue->storage || queue->capacity == 0 || !data || len == 0) {
        return 0;
    }

    size_t writable = queue->capacity - queue->size;
    size_t to_write = len < writable ? len : writable;
    for (size_t i = 0; i < to_write; i++) {
        queue->storage[queue->tail] = data[i];
        queue->tail = (queue->tail + 1) % queue->capacity;
    }
    queue->size += to_write;
    queue->dropped_bytes += len - to_write;
    return to_write;
}

size_t voice_audio_playback_queue_pop(voice_audio_playback_queue_t *queue,
                                      uint8_t *out,
                                      size_t max_len) {
    if (!queue || !queue->storage || !out || max_len == 0 || queue->size == 0) {
        return 0;
    }

    size_t to_read = max_len < queue->size ? max_len : queue->size;
    for (size_t i = 0; i < to_read; i++) {
        out[i] = queue->storage[queue->head];
        queue->head = (queue->head + 1) % queue->capacity;
    }
    queue->size -= to_read;
    return to_read;
}

void voice_audio_playback_queue_clear(voice_audio_playback_queue_t *queue) {
    if (!queue) {
        return;
    }
    queue->head = 0;
    queue->tail = 0;
    queue->size = 0;
}

void voice_audio_playback_queue_reset(voice_audio_playback_queue_t *queue) {
    if (!queue) {
        return;
    }
    voice_audio_playback_queue_clear(queue);
    queue->dropped_bytes = 0;
}

size_t voice_audio_playback_queue_size(const voice_audio_playback_queue_t *queue) {
    return queue ? queue->size : 0;
}

size_t voice_audio_playback_queue_dropped_bytes(const voice_audio_playback_queue_t *queue) {
    return queue ? queue->dropped_bytes : 0;
}

#ifdef ESP_PLATFORM
#define AUDIO_SAMPLE_RATE_HZ 16000
#define AUDIO_BITS_PER_SAMPLE I2S_DATA_BIT_WIDTH_16BIT
#define AUDIO_SLOT_MODE I2S_SLOT_MODE_MONO
/* 32KB ≈ 1s of 16kHz/16bit playback. The server now paces TTS frames at real
 * time, so the queue stays nearly empty (frames arrive as fast as they drain) —
 * the old 96KB was sized for "dump the whole reply at once" and is unnecessary.
 * This static .bss array competes with the heap on this RAM-tight board, where
 * the boot log shows LVGL buffer allocation already failing (~145KB free total);
 * the extra 64KB of heap keeps the Wi-Fi/WS TLS RX buffers from starving and
 * dropping the connection mid-TTS (which truncated playback after sentence 1). */
#define AUDIO_QUEUE_BYTES (32 * 1024)
#define AUDIO_TASK_CHUNK_BYTES 512

/* Digital output attenuation, Q8 fixed point (256 = unity). The Volcano TTS PCM
 * arrives near full scale (0 dBFS); at a usable volume that overdrives the codec
 * DAC / external PA on loud peaks, heard as a faint "电流声" only while speaking
 * (idle is clean thanks to auto_clear). Scaling each sample down a couple dB
 * before the codec gives the analog path headroom so peaks stop clipping, while
 * perceived loudness barely changes because peaks rarely sustain. 205/256 ≈ 0.80
 * ≈ -1.9 dB. (audio-opt.md §3: 数字音量衰减，给功放留余量，避免削顶失真.) */
#define AUDIO_OUTPUT_SCALE_Q8 205

/* One-time prebuffer (prime) before playback starts draining: ~128ms at
 * 16kHz/16bit (4KB, was 6KB/~192ms). The codec consumes at its exact hardware
 * clock while the server paces frames at a nominal 16kHz; the small rate
 * difference makes the queue slowly drift toward empty over a multi-sentence
 * reply, dipping to zero at the quiet sentence boundaries where auto_clear's
 * silence transition clicks ("切句子时的电流声"). A small cushion keeps the queue
 * off empty for the whole reply without re-priming mid-stream (which is what
 * caused the earlier stutter). Trimmed to 4KB so the bot starts speaking sooner
 * after tts_start; bump back toward 6KB if sentence-boundary underruns return. */
#define AUDIO_PREBUFFER_BYTES (4 * 1024)

#define I2S_PIN_MCLK 16
#define I2S_PIN_BCLK 9
#define I2S_PIN_WS 45
#define I2S_PIN_DOUT 8
#define I2S_PIN_DIN 10
#define I2S_PIN_PA_EN 46

static const char *TAG = "audio";

static i2s_chan_handle_t s_i2s_tx;
static i2s_chan_handle_t s_i2s_rx;
static bool s_i2s_ready;
static voice_audio_playback_t s_playback;
static uint8_t s_queue_storage[AUDIO_QUEUE_BYTES];
static voice_audio_playback_queue_t s_audio_queue;
static SemaphoreHandle_t s_audio_mutex;
static TaskHandle_t s_audio_task;
static volatile bool s_tts_input_open;
static bool s_primed;

static void queue_lock(void) {
    if (s_audio_mutex) {
        xSemaphoreTake(s_audio_mutex, portMAX_DELAY);
    }
}

static void queue_unlock(void) {
    if (s_audio_mutex) {
        xSemaphoreGive(s_audio_mutex);
    }
}

static int i2s_sink_write(const uint8_t *data, size_t len, void *ctx) {
    (void)ctx;
    if (!s_i2s_ready || !s_i2s_tx) {
        return 0;
    }

    size_t written = 0;
    esp_err_t ret = i2s_channel_write(s_i2s_tx, data, len, &written, portMAX_DELAY);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "i2s write failed: %s", esp_err_to_name(ret));
        return 0;
    }
    return (int)written;
}

static void playback_task(void *arg) {
    (void)arg;
    uint8_t chunk[AUDIO_TASK_CHUNK_BYTES];

    while (true) {
        size_t len = 0;
        bool active = false;
        bool input_open = false;

        queue_lock();
        active = voice_audio_playback_is_active(&s_playback);
        input_open = s_tts_input_open;
        if (active && !s_primed) {
            // Hold off draining until a small cushion is buffered. If the input
            // closes first (very short reply), prime immediately so it plays out.
            if (voice_audio_playback_queue_size(&s_audio_queue) >= AUDIO_PREBUFFER_BYTES ||
                !input_open) {
                s_primed = true;
            }
        }
        if (active && s_primed) {
            len = voice_audio_playback_queue_pop(&s_audio_queue, chunk, sizeof(chunk));
        }
        bool drained_now = false;
        if (active && s_primed && !input_open && len == 0) {
            voice_audio_playback_end(&s_playback);
            active = false;
            drained_now = true;
            ESP_LOGI(TAG, "tts playback drained bytes=%u dropped=%u",
                     (unsigned)voice_audio_playback_bytes_written(&s_playback),
                     (unsigned)voice_audio_playback_queue_dropped_bytes(&s_audio_queue));
        }
        queue_unlock();

        if (drained_now) {
            // Playback truly finished. Let the I2S DMA tail (~100ms) flush before
            // signalling the conversation FSM to reopen the mic — otherwise the
            // half-duplex capture picks up our own speaker tail and self-triggers
            // a barge-in, cutting the reply short. This is why TTS_FINISHED is
            // posted here, not when the server's tts_end arrives (queue not empty
            // yet at that point).
            vTaskDelay(pdMS_TO_TICKS(200));
            voice_conversation_hw_post(VOICE_CONV_EVENT_TTS_FINISHED);
            continue;
        }

        if (!active || len == 0) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

#if AUDIO_OUTPUT_SCALE_Q8 != 256
        // Attenuate 16-bit samples in place to keep the codec/PA out of clipping.
        int16_t *samples = (int16_t *)chunk;
        for (size_t i = 0; i < len / sizeof(int16_t); i++) {
            samples[i] = (int16_t)((samples[i] * AUDIO_OUTPUT_SCALE_Q8) >> 8);
        }
#endif

        int written = voice_audio_playback_write(&s_playback, chunk, len);
        if (written <= 0) {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}

static esp_err_t init_pa_gpio(void) {
    gpio_config_t pa_cfg = {
        .pin_bit_mask = 1ULL << I2S_PIN_PA_EN,
        .mode = GPIO_MODE_OUTPUT,
    };
    esp_err_t ret = gpio_config(&pa_cfg);
    if (ret == ESP_OK) {
        gpio_set_level(I2S_PIN_PA_EN, 1);
    }
    return ret;
}

esp_err_t voice_audio_playback_start_hardware(void) {
    voice_audio_playback_init(&s_playback, i2s_sink_write, NULL);
    voice_audio_playback_queue_init(&s_audio_queue, s_queue_storage, sizeof(s_queue_storage));

    s_audio_mutex = xSemaphoreCreateMutex();
    if (!s_audio_mutex) {
        ESP_LOGE(TAG, "audio mutex create failed");
        return ESP_ERR_NO_MEM;
    }

    esp_err_t ret = init_pa_gpio();
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "PA gpio init failed: %s", esp_err_to_name(ret));
    }

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    // Keep dma_desc_num at 6 (~96ms): this board is internal-RAM tight (the
    // heartbeat task once boot-looped on RAM starvation), and extra DMA
    // descriptors can starve the Wi-Fi/WS TLS send buffers → the auth frame
    // silently fails to send and the device never comes online.
    chan_cfg.dma_desc_num = 6;
    chan_cfg.dma_frame_num = 256;
    // auto_clear: on underrun the DMA outputs silence instead of looping the
    // stale buffer. The TX channel stays enabled for the device's whole life,
    // so without this the unfed DMA replays old samples whenever the queue runs
    // dry (between chunks and while idle) → the continuous "滋滋" hiss. With it,
    // gaps become clean silence. (audio-opt.md §1/§6: underrun, not power.)
    // This flag adds no RAM, so it is safe on this board.
    chan_cfg.auto_clear = true;
    // Full duplex on one controller: TX feeds ES8311 DAC, RX reads the
    // codec ADC (mic) on DIN. Both share MCLK/BCLK/WS.
    ret = i2s_new_channel(&chan_cfg, &s_i2s_tx, &s_i2s_rx);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s channel create failed: %s", esp_err_to_name(ret));
        return ret;
    }

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(AUDIO_BITS_PER_SAMPLE, AUDIO_SLOT_MODE),
        .gpio_cfg = {
            .mclk = I2S_PIN_MCLK,
            .bclk = I2S_PIN_BCLK,
            .ws = I2S_PIN_WS,
            .dout = I2S_PIN_DOUT,
            .din = I2S_PIN_DIN,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };

    ret = i2s_channel_init_std_mode(s_i2s_tx, &std_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s std init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    ret = i2s_channel_init_std_mode(s_i2s_rx, &std_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s rx std init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    ret = i2s_channel_enable(s_i2s_tx);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s enable failed: %s", esp_err_to_name(ret));
        return ret;
    }

    ret = i2s_channel_enable(s_i2s_rx);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s rx enable failed: %s", esp_err_to_name(ret));
        return ret;
    }

    ret = voice_es8311_init_16k();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "es8311 init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    // The mic is on a separate ES7210 ADC (not the ES8311) that shares this same
    // I2S bus (DIN) and I2C bus. Without it the RX line carries no mic data, so
    // capture/VAD never detect speech. Configure it after the ES8311 so MCLK is
    // already running (the ES7210 is an I2S slave). A failure here only loses
    // capture (mic), so warn and continue rather than aborting playback.
    ret = voice_es7210_init_16k();
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "es7210 mic init failed: %s (capture/VAD unavailable)",
                 esp_err_to_name(ret));
    }

    s_i2s_ready = true;
    if (xTaskCreate(playback_task, "audio_play", 4096, NULL, 5, &s_audio_task) != pdPASS) {
        ESP_LOGE(TAG, "audio playback task create failed");
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "i2s playback ready: 16kHz/16bit/mono, DOUT=%d, PA=%d",
             I2S_PIN_DOUT, I2S_PIN_PA_EN);
    return ESP_OK;
}

void voice_audio_playback_on_tts_start(void) {
    queue_lock();
    voice_audio_playback_queue_reset(&s_audio_queue);
    s_tts_input_open = true;
    s_primed = false;
    voice_audio_playback_start(&s_playback);
    queue_unlock();
    ESP_LOGI(TAG, "tts playback started");
}

void voice_audio_playback_on_tts_chunk(const uint8_t *data, size_t len) {
    queue_lock();
    bool active = voice_audio_playback_is_active(&s_playback);
    size_t queued = active ? voice_audio_playback_queue_push(&s_audio_queue, data, len) : 0;
    size_t queue_size = voice_audio_playback_queue_size(&s_audio_queue);
    size_t dropped = voice_audio_playback_queue_dropped_bytes(&s_audio_queue);
    queue_unlock();

    if (queued < len) {
        ESP_LOGW(TAG, "audio queue full: queued=%u dropped_now=%u dropped_total=%u",
                 (unsigned)queued, (unsigned)(len - queued), (unsigned)dropped);
    } else {
        ESP_LOGD(TAG, "queued tts chunk %u bytes (queue=%u)",
                 (unsigned)queued, (unsigned)queue_size);
    }
}

void voice_audio_playback_on_tts_end(const char *reason) {
    queue_lock();
    s_tts_input_open = false;
    queue_unlock();
    ESP_LOGI(TAG, "tts playback input ended reason=%s bytes=%u queued=%u",
             reason && reason[0] ? reason : "unknown",
             (unsigned)voice_audio_playback_bytes_written(&s_playback),
             (unsigned)voice_audio_playback_queue_size(&s_audio_queue));
}

void voice_audio_playback_on_barge_in(void) {
    queue_lock();
    voice_audio_playback_interrupt(&s_playback);
    voice_audio_playback_queue_clear(&s_audio_queue);
    s_tts_input_open = false;
    s_primed = false;
    queue_unlock();
    ESP_LOGI(TAG, "tts playback interrupted");
}

i2s_chan_handle_t voice_audio_playback_rx_channel(void) {
    return s_i2s_ready ? s_i2s_rx : NULL;
}
#endif
