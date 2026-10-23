#include "conversation.h"

#include <string.h>

static bool greeting_enabled(const voice_conversation_t *conv) {
    return conv->config.greeting_text && conv->config.greeting_text[0] != '\0';
}

static void capture_set(voice_conversation_t *conv, bool enabled) {
    if (conv->ops.capture_set_enabled) {
        conv->ops.capture_set_enabled(enabled, conv->ops.ctx);
    }
}

static void ui_post(voice_conversation_t *conv, voice_ui_event_type_t type, const char *detail) {
    if (conv->ops.ui_event) {
        conv->ops.ui_event(type, detail, conv->ops.ctx);
    }
}

static void preroll_clear(voice_conversation_t *conv) {
    conv->preroll_count = 0;
    conv->preroll_head = 0;
}

static void preroll_push(voice_conversation_t *conv, const int16_t *samples, size_t count) {
    if (count > VOICE_CONV_FRAME_SAMPLES) {
        count = VOICE_CONV_FRAME_SAMPLES;
    }
    size_t slot;
    if (conv->preroll_count < VOICE_CONV_PREROLL_FRAMES) {
        slot = (conv->preroll_head + conv->preroll_count) % VOICE_CONV_PREROLL_FRAMES;
        conv->preroll_count++;
    } else {
        slot = conv->preroll_head;
        conv->preroll_head = (conv->preroll_head + 1) % VOICE_CONV_PREROLL_FRAMES;
    }
    memcpy(conv->preroll[slot], samples, count * sizeof(int16_t));
    conv->preroll_lens[slot] = count;
}

static void preroll_flush(voice_conversation_t *conv) {
    for (size_t i = 0; i < conv->preroll_count; i++) {
        size_t slot = (conv->preroll_head + i) % VOICE_CONV_PREROLL_FRAMES;
        if (conv->ops.send_audio_frame) {
            conv->ops.send_audio_frame(conv->preroll[slot], conv->preroll_lens[slot],
                                       conv->ops.ctx);
        }
    }
    preroll_clear(conv);
}

static void close_utterance(voice_conversation_t *conv) {
    if (conv->utterance_open) {
        if (conv->ops.send_audio_end) {
            conv->ops.send_audio_end(conv->ops.ctx);
        }
        conv->utterance_open = false;
    }
}

static void enter_listening(voice_conversation_t *conv) {
    conv->state = VOICE_CONV_STATE_LISTENING;
    conv->utterance_open = false;
    conv->playing_frames = 0;
    voice_vad_reset(&conv->vad);
    preroll_clear(conv);
    capture_set(conv, true);
    ui_post(conv, VOICE_UI_EVENT_RECORDING_START, NULL);
}

/* Full-duplex barge-in: user spoke (or wake word fired) during playback. Stop
 * the speaker immediately, tell the server to cancel the current turn, and
 * re-arm listening so the next frames capture the user's utterance. */
static void start_barge_in(voice_conversation_t *conv, const char *reason) {
    if (conv->ops.stop_playback) {
        conv->ops.stop_playback(conv->ops.ctx);
    }
    if (conv->ops.send_barge_in) {
        conv->ops.send_barge_in(reason, conv->ops.ctx);
    }
    enter_listening(conv);
}

static void enter_off(voice_conversation_t *conv) {
    bool was_busy = conv->state == VOICE_CONV_STATE_THINKING ||
                    conv->state == VOICE_CONV_STATE_PLAYING;
    close_utterance(conv);
    capture_set(conv, false);
    if (conv->state == VOICE_CONV_STATE_PLAYING && conv->ops.stop_playback) {
        conv->ops.stop_playback(conv->ops.ctx);
    }
    if (was_busy && conv->ops.send_barge_in) {
        conv->ops.send_barge_in("conversation_off", conv->ops.ctx);
    }
    conv->state = VOICE_CONV_STATE_OFF;
    preroll_clear(conv);
    ui_post(conv, VOICE_UI_EVENT_CONVERSATION_OFF, NULL);
}

voice_conversation_config_t voice_conversation_default_config(void) {
    voice_conversation_config_t config = {
        .vad = voice_vad_default_config(),
        .greeting_text = NULL,
        .duplex = VOICE_CONV_DUPLEX_HALF,
        .barge_guard_frames = 13, /* ~260ms @ 20ms frames */
    };
    return config;
}

void voice_conversation_init(voice_conversation_t *conv,
                             const voice_conversation_ops_t *ops,
                             const voice_conversation_config_t *config) {
    if (!conv) {
        return;
    }
    memset(conv, 0, sizeof(*conv));
    if (ops) {
        conv->ops = *ops;
    }
    conv->config = config ? *config : voice_conversation_default_config();
    voice_vad_init(&conv->vad, &conv->config.vad);
    conv->state = VOICE_CONV_STATE_OFF;
}

voice_conversation_state_t voice_conversation_state(const voice_conversation_t *conv) {
    return conv ? conv->state : VOICE_CONV_STATE_OFF;
}

bool voice_conversation_active(const voice_conversation_t *conv) {
    return conv && conv->state != VOICE_CONV_STATE_OFF;
}

void voice_conversation_post_event(voice_conversation_t *conv,
                                   voice_conversation_event_t event) {
    if (!conv) {
        return;
    }

    switch (event) {
        case VOICE_CONV_EVENT_TOGGLE:
            if (conv->state == VOICE_CONV_STATE_OFF) {
                if (greeting_enabled(conv)) {
                    /* Greeting reply will arrive as a normal TTS turn; wait for
                     * it instead of capturing the device's own speaker output. */
                    conv->state = VOICE_CONV_STATE_THINKING;
                    if (conv->ops.send_text_input) {
                        conv->ops.send_text_input(conv->config.greeting_text, conv->ops.ctx);
                    }
                    ui_post(conv, VOICE_UI_EVENT_RECORDING_END, NULL);
                } else {
                    enter_listening(conv);
                }
            } else {
                enter_off(conv);
            }
            break;

        case VOICE_CONV_EVENT_TTS_STARTED:
            if (conv->state == VOICE_CONV_STATE_OFF) {
                break; /* console-initiated say outside conversation mode */
            }
            close_utterance(conv);
            conv->state = VOICE_CONV_STATE_PLAYING;
            conv->playing_frames = 0;
            if (conv->config.duplex == VOICE_CONV_DUPLEX_FULL) {
                /* Keep listening during playback (upstream AEC cleans the mic);
                 * a barge during the guard window is ignored. */
                voice_vad_reset(&conv->vad);
                preroll_clear(conv);
                capture_set(conv, true);
            } else {
                /* Half-duplex: never capture while the speaker is on. */
                capture_set(conv, false);
            }
            break;

        case VOICE_CONV_EVENT_TTS_FINISHED:
            if (conv->state == VOICE_CONV_STATE_PLAYING ||
                conv->state == VOICE_CONV_STATE_THINKING) {
                enter_listening(conv);
            } else {
                /* Console-initiated say while conversation mode is off: the UI no
                 * longer idles on tts_end (it would race this FSM), so own the
                 * return-to-idle here once playback has truly drained. */
                ui_post(conv, VOICE_UI_EVENT_CONVERSATION_OFF, NULL);
            }
            break;

        case VOICE_CONV_EVENT_SERVER_ERROR:
            if (conv->state == VOICE_CONV_STATE_LISTENING) {
                /* A streaming ASR error can arrive before local VAD reaches
                 * speech-end. Close the server segment before resetting the
                 * local utterance flag, otherwise its trailing audio_end is lost. */
                close_utterance(conv);
                enter_listening(conv);
            } else if (conv->state == VOICE_CONV_STATE_THINKING ||
                       conv->state == VOICE_CONV_STATE_PLAYING) {
                enter_listening(conv);
            }
            break;

        case VOICE_CONV_EVENT_REPLY_PROGRESS:
            /* No state transition. voice_conversation_hw_post() calls
             * sync_think_timer() after this pure state-machine step, so genuine
             * server progress grants the next phase a fresh bounded window. */
            break;

        case VOICE_CONV_EVENT_THINKING_TIMEOUT:
            if (conv->state == VOICE_CONV_STATE_THINKING) {
                enter_listening(conv);
            }
            break;

        case VOICE_CONV_EVENT_BARGE_DETECTED:
            /* Wake-word / external detector fired (phase 2). Only meaningful
             * while the speaker is playing. No frame-count guard: a wake-word
             * detector is robust to the bot's own voice and its frames don't
             * necessarily drive the energy-VAD counter. */
            if (conv->state == VOICE_CONV_STATE_PLAYING) {
                start_barge_in(conv, "wake_word");
            }
            break;
    }
}

void voice_conversation_on_audio_frame(voice_conversation_t *conv,
                                       const int16_t *samples,
                                       size_t count) {
    if (!conv || !samples || count == 0) {
        return;
    }

    /* Full-duplex: during playback, run VAD on the (AEC-cleaned) mic frames and
     * treat speech onset as a barge-in. Frames during the guard window are
     * dropped to skip AEC convergence transients. */
    if (conv->state == VOICE_CONV_STATE_PLAYING) {
        if (conv->config.duplex != VOICE_CONV_DUPLEX_FULL) {
            return;
        }
        conv->playing_frames++;
        if (conv->playing_frames <= conv->config.barge_guard_frames) {
            return;
        }
        if (voice_vad_process(&conv->vad, samples, count) == VOICE_VAD_SPEECH_START) {
            start_barge_in(conv, "user_speech");
        }
        return;
    }

    if (conv->state != VOICE_CONV_STATE_LISTENING) {
        return;
    }

    if (!conv->utterance_open) {
        preroll_push(conv, samples, count);
    }

    voice_vad_result_t result = voice_vad_process(&conv->vad, samples, count);
    switch (result) {
        case VOICE_VAD_SPEECH_START:
            if (conv->ops.send_audio_start) {
                conv->ops.send_audio_start(conv->ops.ctx);
            }
            conv->utterance_open = true;
            /* Pre-roll already contains this frame: flush covers the leading edge. */
            preroll_flush(conv);
            break;

        case VOICE_VAD_SPEECH:
            if (conv->utterance_open && conv->ops.send_audio_frame) {
                conv->ops.send_audio_frame(samples, count, conv->ops.ctx);
            }
            break;

        case VOICE_VAD_SPEECH_END:
            if (conv->utterance_open) {
                if (conv->ops.send_audio_frame) {
                    conv->ops.send_audio_frame(samples, count, conv->ops.ctx);
                }
                close_utterance(conv);
                capture_set(conv, false);
                conv->state = VOICE_CONV_STATE_THINKING;
                ui_post(conv, VOICE_UI_EVENT_RECORDING_END, NULL);
            }
            break;

        case VOICE_VAD_SILENCE:
            break;
    }
}

#ifdef ESP_PLATFORM
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#include "audio_capture.h"
#include "audio_playback.h"
#include "opus_encoder.h"
#include "ui_screen.h"
#include "ws_client.h"

static const char *TAG = "conv";

static voice_conversation_t s_conv;
static SemaphoreHandle_t s_conv_mutex;
static esp_timer_handle_t s_think_timer;
static TaskHandle_t s_toggle_task;
static volatile bool s_conv_active_cached;

static void conv_lock(void) {
    xSemaphoreTake(s_conv_mutex, portMAX_DELAY);
}

static void conv_unlock(void) {
    xSemaphoreGive(s_conv_mutex);
}

/* Re-arm or stop the thinking watchdog to match the current state. */
static void sync_think_timer(void) {
    if (!s_think_timer) {
        return;
    }
    esp_timer_stop(s_think_timer);
    if (s_conv.state == VOICE_CONV_STATE_THINKING) {
        esp_timer_start_once(s_think_timer,
                             (uint64_t)CONFIG_VR_THINKING_TIMEOUT_MS * 1000);
    }
}

static void sync_active_cached_locked(void) {
    s_conv_active_cached = voice_conversation_active(&s_conv);
}

static void think_timer_cb(void *arg) {
    (void)arg;
    ESP_LOGW(TAG, "thinking timeout; re-arming listening");
    voice_conversation_hw_post(VOICE_CONV_EVENT_THINKING_TIMEOUT);
}

static void toggle_task(void *arg) {
    (void)arg;
    while (true) {
        /* Clear accumulated taps before running the toggle so rapid repeated
         * touches collapse to one state transition instead of thrashing the
         * websocket/audio path. */
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        voice_conversation_hw_toggle();
    }
}

static void ops_capture_set_enabled(bool enabled, void *ctx) {
    (void)ctx;
    voice_audio_capture_set_enabled(enabled);
}

static void ops_send_audio_start(void *ctx) {
    (void)ctx;
    voice_ws_send_audio_start();
}

static void ops_send_audio_frame(const int16_t *samples, size_t count, void *ctx) {
    (void)ctx;
    voice_ws_send_audio_frame((const uint8_t *)samples, count * sizeof(int16_t));
}

static void ops_send_audio_end(void *ctx) {
    (void)ctx;
    voice_ws_send_audio_end();
}

static void ops_send_barge_in(const char *reason, void *ctx) {
    (void)ctx;
    voice_ws_send_barge_in(reason);
}

static void ops_send_text_input(const char *text, void *ctx) {
    (void)ctx;
    voice_ws_send_text_input(text);
}

static void ops_stop_playback(void *ctx) {
    (void)ctx;
    voice_audio_playback_on_barge_in();
}

static void ops_ui_event(voice_ui_event_type_t type, const char *detail, void *ctx) {
    (void)ctx;
    voice_ui_post_event(type, detail);
}

esp_err_t voice_conversation_hw_init(void) {
    if (s_conv_mutex) {
        return ESP_OK;
    }
    s_conv_mutex = xSemaphoreCreateMutex();
    if (!s_conv_mutex) {
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreate(toggle_task, "conv_toggle", 3072, NULL, 4,
                    &s_toggle_task) != pdPASS) {
        s_toggle_task = NULL;
        ESP_LOGW(TAG, "deferred toggle task unavailable");
    }

    esp_timer_create_args_t timer_args = {
        .callback = think_timer_cb,
        .name = "conv_think",
    };
    esp_err_t ret = esp_timer_create(&timer_args, &s_think_timer);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "thinking timer create failed: %s", esp_err_to_name(ret));
        return ret;
    }

    if (voice_opus_encoder_init() != ESP_OK) {
        ESP_LOGE(TAG, "opus encoder init failed; uplink audio unavailable");
        return ESP_FAIL;
    }

    voice_conversation_ops_t ops = {
        .capture_set_enabled = ops_capture_set_enabled,
        .send_audio_start = ops_send_audio_start,
        .send_audio_frame = ops_send_audio_frame,
        .send_audio_end = ops_send_audio_end,
        .send_barge_in = ops_send_barge_in,
        .send_text_input = ops_send_text_input,
        .stop_playback = ops_stop_playback,
        .ui_event = ops_ui_event,
    };
    voice_conversation_config_t config = voice_conversation_default_config();
    config.vad.start_threshold = CONFIG_VR_VAD_START_THRESHOLD;
    config.vad.end_threshold = CONFIG_VR_VAD_END_THRESHOLD;
    config.vad.end_silence_frames = CONFIG_VR_VAD_END_SILENCE_MS / 20;
    config.greeting_text = CONFIG_VR_GREETING_TEXT;
#ifdef CONFIG_VR_FULL_DUPLEX
    /* Full-duplex needs an upstream AEC (esp-sr AFE) feeding clean frames; with
     * raw mic the speaker self-triggers. Default off until AEC lands + is
     * tuned on real hardware (ADR 0002). */
    config.duplex = VOICE_CONV_DUPLEX_FULL;
    config.barge_guard_frames = CONFIG_VR_BARGE_GUARD_MS / 20;
#endif
    voice_conversation_init(&s_conv, &ops, &config);
    s_conv_active_cached = false;
    ESP_LOGI(TAG, "conversation ready: vad start=%d end=%d silence=%dms greeting=%s",
             CONFIG_VR_VAD_START_THRESHOLD, CONFIG_VR_VAD_END_THRESHOLD,
             CONFIG_VR_VAD_END_SILENCE_MS,
             config.greeting_text && config.greeting_text[0] ? "on" : "off");
    return ESP_OK;
}

void voice_conversation_hw_toggle_deferred(void) {
    if (!s_conv_mutex) {
        return;
    }
    if (s_toggle_task) {
        xTaskNotifyGive(s_toggle_task);
        return;
    }
    /* If the task is unavailable, drop the tap rather than re-entering the
     * bounded-but-blocking websocket path from the LVGL or esp_timer task. */
    ESP_LOGW(TAG, "deferred toggle unavailable; dropping UI tap");
}

void voice_conversation_hw_toggle(void) {
    if (!s_conv_mutex) {
        return;
    }
    conv_lock();
    voice_conversation_post_event(&s_conv, VOICE_CONV_EVENT_TOGGLE);
    sync_active_cached_locked();
    bool active = s_conv_active_cached;
    sync_think_timer();
    conv_unlock();
    ESP_LOGI(TAG, "conversation mode %s", active ? "ON" : "OFF");
}

void voice_conversation_hw_post(voice_conversation_event_t event) {
    if (!s_conv_mutex) {
        return;
    }
    conv_lock();
    voice_conversation_post_event(&s_conv, event);
    sync_active_cached_locked();
    sync_think_timer();
    conv_unlock();
}

void voice_conversation_hw_on_frame(const int16_t *samples, size_t count) {
    if (!s_conv_mutex) {
        return;
    }
    conv_lock();
    voice_conversation_on_audio_frame(&s_conv, samples, count);
    sync_active_cached_locked();
    sync_think_timer();
    conv_unlock();
}

bool voice_conversation_hw_active(void) {
    return s_conv_active_cached;
}
#endif
