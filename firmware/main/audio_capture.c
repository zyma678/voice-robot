#include "audio_capture.h"

#include "driver/i2s_std.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "audio_playback.h"
#include "conversation.h"

#define CAPTURE_FRAME_SAMPLES VOICE_CONV_FRAME_SAMPLES /* 320 = 20ms @ 16k */
/* The I2S RX runs as a 2-slot (stereo) frame even though slot_mode is MONO
 * (TX/RX share one std_cfg in full-duplex). ES7210 routes MIC1 to the LEFT
 * slot; the right slot is silent. So the DMA delivers interleaved [L,0,L,0,...]
 * — we read 2x and keep the left samples to recover real mono. */
#define CAPTURE_FRAME_BYTES (CAPTURE_FRAME_SAMPLES * 2 * sizeof(int16_t))
#define CAPTURE_READ_TIMEOUT_MS 200
#define CAPTURE_IDLE_POLL_MS 20
/* Digital make-up gain on top of the ES7210 analog PGA (max +37.5dB). The
 * analog gain alone is enough for a healthy level (speech avg ~800 / peak
 * ~8-13k, noise floor avg ~15), so keep this at 1: digital gain would only
 * amplify noise and risk clipping. Clamped to int16. */
#define CAPTURE_SW_GAIN 1

static const char *TAG = "capture";

static TaskHandle_t s_capture_task;
static volatile bool s_enabled;
/* Set when capture is (re)enabled: tells the task to drop whatever the I2S RX
 * DMA buffered while capture was off (our own TTS bleeding into the mic during
 * playback) so listening starts fresh and we don't transcribe + answer our own
 * speech. */
static volatile bool s_flush_pending;

static void capture_task(void *arg) {
    (void)arg;
    static int16_t stereo[CAPTURE_FRAME_SAMPLES * 2]; /* interleaved [L,R] from RX */
    static int16_t frame[CAPTURE_FRAME_SAMPLES];      /* de-interleaved left = MIC1 */

    while (true) {
        if (!s_enabled) {
            vTaskDelay(pdMS_TO_TICKS(CAPTURE_IDLE_POLL_MS));
            continue;
        }

        i2s_chan_handle_t rx = voice_audio_playback_rx_channel();
        if (!rx) {
            vTaskDelay(pdMS_TO_TICKS(CAPTURE_IDLE_POLL_MS));
            continue;
        }

        if (s_flush_pending) {
            /* Drain the stale RX DMA frames buffered while capture was off so we
             * don't process our own speaker bleed-through as user speech. Bounded
             * non-blocking reads until the ring is empty. */
            size_t drained = 0;
            for (int i = 0; i < 64; i++) {
                size_t got = 0;
                if (i2s_channel_read(rx, stereo, CAPTURE_FRAME_BYTES, &got, 0) != ESP_OK ||
                    got == 0) {
                    break;
                }
                drained += got;
            }
            s_flush_pending = false;
            ESP_LOGI(TAG, "capture flushed %u stale bytes", (unsigned)drained);
        }

        size_t read = 0;
        esp_err_t ret = i2s_channel_read(rx, stereo, CAPTURE_FRAME_BYTES, &read,
                                         pdMS_TO_TICKS(CAPTURE_READ_TIMEOUT_MS));
        if (ret != ESP_OK || read == 0) {
            if (ret != ESP_OK && ret != ESP_ERR_TIMEOUT) {
                ESP_LOGW(TAG, "i2s read failed: %s", esp_err_to_name(ret));
            }
            continue;
        }
        if (s_enabled) {
            /* De-interleave the left slot (MIC1), drop the silent right slot,
             * and apply digital make-up gain. n = real mono samples. */
            size_t n = (read / sizeof(int16_t)) / 2;
            for (size_t i = 0; i < n; i++) {
                int32_t s = (int32_t)stereo[2 * i] * CAPTURE_SW_GAIN;
                if (s > 32767) {
                    s = 32767;
                } else if (s < -32768) {
                    s = -32768;
                }
                frame[i] = (int16_t)s;
            }
            voice_conversation_hw_on_frame(frame, n);
        }
    }
}

esp_err_t voice_audio_capture_start_hardware(void) {
    if (s_capture_task) {
        return ESP_OK;
    }
    if (!voice_audio_playback_rx_channel()) {
        ESP_LOGE(TAG, "i2s rx channel missing; start playback hardware first");
        return ESP_ERR_INVALID_STATE;
    }
    if (xTaskCreate(capture_task, "audio_cap", 4096, NULL, 5, &s_capture_task) != pdPASS) {
        ESP_LOGE(TAG, "capture task create failed");
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "i2s capture ready: 16kHz/16bit/mono, %dms frames",
             (int)(CAPTURE_FRAME_SAMPLES * 1000 / 16000));
    return ESP_OK;
}

void voice_audio_capture_set_enabled(bool enabled) {
    if (s_enabled == enabled) {
        return;
    }
    if (enabled) {
        /* Flag before flipping s_enabled so the task flushes the stale RX buffer
         * (speaker bleed captured while we were off) before its first real read. */
        s_flush_pending = true;
    }
    s_enabled = enabled;
    ESP_LOGI(TAG, "capture %s", enabled ? "enabled" : "disabled");
}

bool voice_audio_capture_enabled(void) {
    return s_enabled;
}
