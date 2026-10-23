#include "ws_client.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_crt_bundle.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "nvs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"

#include "audio_playback.h"
#include "conversation.h"
#include "display_lcd.h"
#include "es8311_codec.h"
#include "opus_encoder.h"
#include "sdkconfig.h"
#include "ui_screen.h"

#ifdef CONFIG_VR_UPLINK_CODEC_OPUS
#define VR_UPLINK_CODEC_STR "opus_raw"
#else
#define VR_UPLINK_CODEC_STR "pcm_s16le"
#endif

static const char *TAG = "ws";

static esp_websocket_client_handle_t s_client;
// Keepalive runs on an esp_timer (shared timer task) instead of a dedicated
// FreeRTOS task: internal RAM is extremely tight on this board (PSRAM disabled),
// and a dedicated 4 KB task stack could not be allocated at boot — reserving it
// statically starved the websocket client's own task and triggered a boot loop.
static esp_timer_handle_t s_heartbeat_timer;
static volatile bool s_connected = false;
static volatile bool s_authenticated = false;
/* Reconnect supervision (checked from the heartbeat timer task, never the WS
 * event task). s_link_ok_us = last time the link was up; s_conv_off_pending =
 * a link drop needs the conversation turned off (deferred off the WS task to
 * avoid the conv-lock / ws-lock cross that used to wedge auto-reconnect). */
static volatile int64_t s_link_ok_us = 0;
static volatile bool s_conv_off_pending = false;
// Deferred from DISCONNECTED: restore the main UI style if a scene (英语学习) was
// active when the link dropped — server forgets the scene on disconnect, so a
// fresh session starts in general chat. Restored in the timer task (LVGL-safe).
static volatile bool s_scene_exit_pending = false;
/* If the link stays down this long the reconnect is wedged (or the network is
 * gone); reboot to recover — the thin client holds no state worth keeping. */
#define WS_DOWN_REBOOT_US (60LL * 1000 * 1000)

#define WS_ENV_NVS_NS "vrcfg"
#define WS_ENV_NVS_KEY "ws_env"
static uint8_t s_ws_env = 0; /* 0 = local (CONFIG_VR_WS_URL), 1 = cloud */

const char *voice_ws_env_url(int env) {
    return env == 1 ? CONFIG_VR_CLOUD_WS_URL : CONFIG_VR_WS_URL;
}

int voice_ws_get_env(void) {
    return (int)s_ws_env;
}

static void ws_env_load(void) {
    s_ws_env = 0;
    nvs_handle_t h;
    if (nvs_open(WS_ENV_NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
        uint8_t val = 0;
        if (nvs_get_u8(h, WS_ENV_NVS_KEY, &val) == ESP_OK) {
            s_ws_env = val ? 1 : 0;
        } else {
            nvs_set_u8(h, WS_ENV_NVS_KEY, s_ws_env);
            nvs_commit(h);
        }
        nvs_close(h);
    }
}

void voice_ws_set_env(int env) {
    uint8_t want = env ? 1 : 0;
    bool changed = (want != s_ws_env);
    s_ws_env = want;
    if (changed) {
        nvs_handle_t h;
        if (nvs_open(WS_ENV_NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
            nvs_set_u8(h, WS_ENV_NVS_KEY, s_ws_env);
            nvs_commit(h);
            nvs_close(h);
        }
    }
    if (s_client) {
        if (!changed && s_connected && s_authenticated) {
            return;
        }
        s_connected = false;
        s_authenticated = false;
        ESP_LOGI(TAG, "%s backend -> %s",
                 changed ? "switching" : "reconnecting",
                 voice_ws_env_url(s_ws_env));
        esp_websocket_client_stop(s_client);
        esp_websocket_client_set_uri(s_client, voice_ws_env_url(s_ws_env));
        esp_websocket_client_start(s_client);
    }
}

static void ws_send_text(const char *text) {
    if (!s_client || !esp_websocket_client_is_connected(s_client)) {
        return;
    }
    ESP_LOGI(TAG, "→ %s", text);
    /* Never wait forever here: this function is called from the audio/VAD and
     * conversation paths as well as the WS event task.  A saturated socket TX
     * buffer must not stall those tasks (which previously made the UI appear
     * frozen until watchdog reset). */
    int sent = esp_websocket_client_send_text(s_client, text, strlen(text),
                                              pdMS_TO_TICKS(200));
    if (sent < 0) {
        ESP_LOGW(TAG, "text send timed out; dropping frame");
    }
}

static void send_auth(void) {
    char buf[256];
    snprintf(buf, sizeof(buf),
             "{\"type\":\"auth\",\"device_id\":\"%s\",\"token\":\"%s\"}",
             CONFIG_VR_DEVICE_ID, CONFIG_VR_AUTH_TOKEN);
    ws_send_text(buf);
}

// Runs in the shared esp_timer task; keep it short and use a bounded send
// timeout so a stalled TX buffer cannot block other timers.
static void heartbeat_timer_cb(void *arg) {
    (void)arg;

    /* Deferred from WEBSOCKET_EVENT_DISCONNECTED: turn conversation off here, in
     * the timer task, so we never take the conv lock from the WS event task (that
     * cross-lock with audio send used to wedge the client task and kill
     * auto-reconnect). */
    if (s_conv_off_pending) {
        s_conv_off_pending = false;
        if (voice_conversation_hw_active()) {
            voice_conversation_hw_toggle_deferred();
        }
    }

    if (s_scene_exit_pending) {
        s_scene_exit_pending = false;
        voice_display_lcd_scene_exit_face();
    }

    /* Reconnect watchdog: refresh the liveness timestamp while up; if the link
     * has been down too long the reconnect is stuck — reboot to recover. */
    int64_t now = esp_timer_get_time();
    if (s_connected) {
        s_link_ok_us = now;
    } else if (s_link_ok_us != 0 && now - s_link_ok_us > WS_DOWN_REBOOT_US) {
        ESP_LOGE(TAG, "websocket down >%llds; rebooting to recover",
                 (long long)(WS_DOWN_REBOOT_US / 1000000));
        esp_restart();
    }

    if (!s_connected || !s_authenticated) {
        return;
    }
    if (!s_client || !esp_websocket_client_is_connected(s_client)) {
        return;
    }
    static const char msg[] = "{\"type\":\"heartbeat\"}";
    esp_websocket_client_send_text(s_client, msg, sizeof(msg) - 1,
                                   pdMS_TO_TICKS(200));
}

bool voice_ws_ready(void) {
    return s_connected && s_authenticated;
}

void voice_ws_send_audio_start(void) {
    char buf[192];
    snprintf(buf, sizeof(buf),
             "{\"type\":\"audio_start\",\"user_id\":\"%s\","
             "\"codec\":\"%s\",\"sample_rate_hz\":16000}",
             CONFIG_VR_USER_ID, VR_UPLINK_CODEC_STR);
    ws_send_text(buf);
}

static void ws_send_bin(const uint8_t *data, size_t len) {
    int sent = esp_websocket_client_send_bin(s_client, (const char *)data, (int)len,
                                             pdMS_TO_TICKS(120));
    if (sent < 0) {
        ESP_LOGW(TAG, "audio frame send failed");
    }
}

void voice_ws_send_audio_frame(const uint8_t *data, size_t len) {
    if (!data || len == 0 || !voice_ws_ready()) {
        return;
    }
#ifdef CONFIG_VR_UPLINK_CODEC_OPUS
    // Encode the 20ms PCM frame to a raw Opus packet and length-prefix it
    // ([u16be len][packet]) so the backend can recover packet boundaries.
    uint8_t packet[2 + 1024];
    int n = voice_opus_encode_frame((const int16_t *)data, len / sizeof(int16_t),
                                    packet + 2, sizeof(packet) - 2);
    if (n <= 0) {
        return;
    }
    packet[0] = (uint8_t)((n >> 8) & 0xFF);
    packet[1] = (uint8_t)(n & 0xFF);
    ws_send_bin(packet, (size_t)n + 2);
#else
    ws_send_bin(data, len);
#endif
}

void voice_ws_send_audio_end(void) {
    ws_send_text("{\"type\":\"audio_end\"}");
}

void voice_ws_send_barge_in(const char *reason) {
    char buf[128];
    snprintf(buf, sizeof(buf), "{\"type\":\"barge_in\",\"reason\":\"%s\"}",
             reason && reason[0] ? reason : "manual");
    ws_send_text(buf);
}

void voice_ws_send_debug(const char *text) {
    if (!text || text[0] == '\0' || !voice_ws_ready()) {
        return;
    }
    char buf[256];
    snprintf(buf, sizeof(buf), "{\"type\":\"device_debug\",\"text\":\"%s\"}", text);
    // Bypass ws_send_text's INFO log: debug lines are frequent and would flood
    // the (already unreliable) serial console; the backend echoes them anyway.
    if (s_client && esp_websocket_client_is_connected(s_client)) {
        esp_websocket_client_send_text(s_client, buf, strlen(buf), pdMS_TO_TICKS(200));
    }
}

void voice_ws_send_text_input(const char *text) {
    if (!text || text[0] == '\0') {
        return;
    }
    char buf[512];
    snprintf(buf, sizeof(buf),
             "{\"type\":\"text_input\",\"user_id\":\"%s\",\"text\":\"%s\"}",
             CONFIG_VR_USER_ID, text);
    ws_send_text(buf);
}

/**
 * Extract the string value of a top-level JSON field without a full parser.
 * Looks for "\"<key>\"" then the next quoted string after the colon.
 * Returns the number of bytes written to out (excluding NUL), or 0.
 */
static size_t json_extract_string(const char *json, int json_len,
                                  const char *key, char *out, size_t out_size) {
    char needle[48];
    int n = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (n <= 0 || n >= (int)sizeof(needle)) {
        return 0;
    }
    const char *p = NULL;
    for (int i = 0; i + n <= json_len; i++) {
        if (strncmp(json + i, needle, (size_t)n) == 0) {
            p = json + i + n;
            break;
        }
    }
    if (!p) {
        return 0;
    }
    const char *end = json + json_len;
    while (p < end && (*p == ':' || *p == ' ' || *p == '\t')) {
        p++;
    }
    if (p >= end || *p != '"') {
        return 0;
    }
    p++;  // skip opening quote
    size_t w = 0;
    while (p < end && *p != '"' && w + 1 < out_size) {
        out[w++] = *p++;
    }
    out[w] = '\0';
    return w;
}

static bool json_extract_bool(const char *json, int json_len, const char *key, bool *out) {
    char needle[48];
    int n = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (n <= 0 || n >= (int)sizeof(needle)) {
        return false;
    }
    const char *p = NULL;
    for (int i = 0; i + n <= json_len; i++) {
        if (strncmp(json + i, needle, (size_t)n) == 0) {
            p = json + i + n;
            break;
        }
    }
    if (!p) {
        return false;
    }
    const char *end = json + json_len;
    while (p < end && (*p == ':' || *p == ' ' || *p == '\t')) {
        p++;
    }
    if (end - p >= 4 && strncmp(p, "true", 4) == 0) {
        *out = true;
        return true;
    }
    if (end - p >= 5 && strncmp(p, "false", 5) == 0) {
        *out = false;
        return true;
    }
    return false;
}

static bool json_extract_int(const char *json, int json_len, const char *key, int *out) {
    char needle[48];
    int n = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (n <= 0 || n >= (int)sizeof(needle)) {
        return false;
    }
    const char *p = NULL;
    for (int i = 0; i + n <= json_len; i++) {
        if (strncmp(json + i, needle, (size_t)n) == 0) {
            p = json + i + n;
            break;
        }
    }
    if (!p) {
        return false;
    }
    const char *end = json + json_len;
    while (p < end && (*p == ':' || *p == ' ' || *p == '\t')) {
        p++;
    }
    char *parse_end = NULL;
    long value = strtol(p, &parse_end, 10);
    if (parse_end == p) {
        return false;
    }
    *out = (int)value;
    return true;
}

static const char *skip_ws(const char *p, const char *end) {
    while (p < end && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) {
        p++;
    }
    return p;
}

static bool parse_int_pair(const char **cursor, const char *end, int *x, int *y) {
    const char *p = skip_ws(*cursor, end);
    if (p >= end || *p != '[') {
        return false;
    }
    p++;
    p = skip_ws(p, end);
    char *parse_end = NULL;
    long vx = strtol(p, &parse_end, 10);
    if (parse_end == p) {
        return false;
    }
    p = skip_ws(parse_end, end);
    if (p >= end || *p != ',') {
        return false;
    }
    p++;
    p = skip_ws(p, end);
    long vy = strtol(p, &parse_end, 10);
    if (parse_end == p) {
        return false;
    }
    p = skip_ws(parse_end, end);
    if (p >= end || *p != ']') {
        return false;
    }
    *x = (int)vx;
    *y = (int)vy;
    *cursor = p + 1;
    return true;
}

static bool json_extract_trace_strokes(const char *json, int len,
                                       voice_trace_stroke_t *strokes,
                                       size_t *stroke_count) {
    if (!json || len <= 0 || !strokes || !stroke_count) {
        return false;
    }
    const char *end = json + len;
    const char *p = strstr(json, "\"strokes\"");
    if (!p || p >= end) {
        return false;
    }
    p = strchr(p, '[');
    if (!p || p >= end) {
        return false;
    }
    p++;

    size_t si = 0;
    while (p < end && si < VOICE_TRACE_MAX_STROKES) {
        p = skip_ws(p, end);
        if (p >= end || *p == ']') {
            break;
        }
        if (*p == ',') {
            p++;
            continue;
        }
        if (*p != '[') {
            p++;
            continue;
        }
        p++;
        voice_trace_stroke_t stroke = {0};
        while (p < end && stroke.point_count < VOICE_TRACE_MAX_POINTS_PER_STROKE) {
            p = skip_ws(p, end);
            if (p >= end || *p == ']') {
                break;
            }
            if (*p == ',') {
                p++;
                continue;
            }
            int x = 0;
            int y = 0;
            if (!parse_int_pair(&p, end, &x, &y)) {
                break;
            }
            if (x < 0) {
                x = 0;
            } else if (x > 1000) {
                x = 1000;
            }
            if (y < 0) {
                y = 0;
            } else if (y > 1000) {
                y = 1000;
            }
            stroke.points[stroke.point_count++] = (voice_trace_point_t){
                .x = (int16_t)x,
                .y = (int16_t)y,
            };
        }
        while (p < end && *p != ']') {
            p++;
        }
        if (p < end && *p == ']') {
            p++;
        }
        if (stroke.point_count >= 2) {
            strokes[si++] = stroke;
        }
    }
    *stroke_count = si;
    return si > 0;
}

// Parse the optional "outlines" field of show_character_trace: a nested array
// [ stroke: [ contour: [ [x,y], ... ], ... ], ... ]. We keep ONE polygon per
// stroke (the largest contour) — 楷体 strokes are simple closed shapes, so the
// outer boundary is enough for a filled render; rare holes are ignored. Returns
// the number of strokes parsed (0 on absent/invalid → caller falls back).
static size_t json_extract_trace_outlines(const char *json, int len,
                                          voice_trace_outline_t *outlines,
                                          size_t max_strokes) {
    if (!json || len <= 0 || !outlines) {
        return 0;
    }
    const char *end = json + len;
    const char *p = strstr(json, "\"outlines\"");
    if (!p || p >= end) {
        return 0;
    }
    p = strchr(p, '[');
    if (!p || p >= end) {
        return 0;
    }
    p++;  // into the strokes array

    size_t si = 0;
    while (p < end && si < max_strokes) {
        p = skip_ws(p, end);
        if (p >= end || *p == ']') {
            break;  // end of strokes array
        }
        if (*p != '[') {
            p++;
            continue;
        }
        p++;  // into one stroke (array of contours)

        voice_trace_outline_t best = {0};
        while (p < end) {
            p = skip_ws(p, end);
            if (p >= end || *p == ']') {
                break;  // end of this stroke
            }
            if (*p != '[') {
                p++;
                continue;
            }
            p++;  // into one contour (array of points)
            voice_trace_outline_t cur = {0};
            while (p < end && cur.point_count < VOICE_TRACE_OUTLINE_MAX_POINTS) {
                p = skip_ws(p, end);
                if (p >= end || *p == ']') {
                    break;  // end of contour
                }
                if (*p == ',') {
                    p++;
                    continue;
                }
                int x = 0;
                int y = 0;
                if (!parse_int_pair(&p, end, &x, &y)) {
                    break;
                }
                if (x < 0) {
                    x = 0;
                } else if (x > 1000) {
                    x = 1000;
                }
                if (y < 0) {
                    y = 0;
                } else if (y > 1000) {
                    y = 1000;
                }
                cur.points[cur.point_count++] = (voice_trace_point_t){
                    .x = (int16_t)x,
                    .y = (int16_t)y,
                };
            }
            while (p < end && *p != ']') {  // consume rest of contour
                p++;
            }
            if (p < end && *p == ']') {
                p++;
            }
            if (cur.point_count > best.point_count) {
                best = cur;  // keep the largest contour (outer boundary)
            }
        }
        while (p < end && *p != ']') {  // consume rest of stroke
            p++;
        }
        if (p < end && *p == ']') {
            p++;
        }
        outlines[si++] = best;  // may be empty → that stroke just won't fill
    }
    return si;
}

// Find the "glyphs" array and record each top-level object element's [start,len)
// span (string-aware brace tracking). Each span is then fed back into the
// per-glyph strokes/outlines/character parsers. Returns the span count (capped).
static size_t json_split_glyphs(const char *json, int len,
                                const char **starts, int *lens, size_t max) {
    if (!json || len <= 0 || !starts || !lens) {
        return 0;
    }
    const char *end = json + len;
    const char *p = strstr(json, "\"glyphs\"");
    if (!p || p >= end) {
        return 0;
    }
    p = strchr(p, '[');
    if (!p || p >= end) {
        return 0;
    }
    p++;  // into the glyphs array
    size_t count = 0;
    while (p < end && count < max) {
        p = skip_ws(p, end);
        if (p >= end || *p == ']') {
            break;
        }
        if (*p != '{') {
            p++;
            continue;
        }
        const char *obj_start = p;
        int depth = 0;
        bool in_str = false;
        bool esc = false;
        while (p < end) {
            const char c = *p;
            if (in_str) {
                if (esc) {
                    esc = false;
                } else if (c == '\\') {
                    esc = true;
                } else if (c == '"') {
                    in_str = false;
                }
            } else if (c == '"') {
                in_str = true;
            } else if (c == '{') {
                depth++;
            } else if (c == '}') {
                depth--;
                if (depth == 0) {
                    p++;
                    break;
                }
            }
            p++;
        }
        starts[count] = obj_start;
        lens[count] = (int)(p - obj_start);
        count++;
    }
    return count;
}

static void handle_server_text(const char *data, int len) {
    char type[48] = {0};
    json_extract_string(data, len, "type", type, sizeof(type));
    // LOGD: streaming replies push many llm_reply_delta frames; logging each one
    // at INFO floods the USB-Serial-JTAG console and can block when no host is
    // attached. Key transitions (auth_ok, error, tts) keep their own logs below.
    ESP_LOGD(TAG, "← type=%s", type[0] ? type : "<none>");

    if (strcmp(type, "auth_ok") == 0) {
        s_authenticated = true;
        ESP_LOGI(TAG, "authenticated as device_id=%s", CONFIG_VR_DEVICE_ID);
        voice_ui_post_event(VOICE_UI_EVENT_AUTH_OK, CONFIG_VR_DEVICE_ID);
    } else if (strcmp(type, "error") == 0) {
        char msg[128] = {0};
        char reason[48] = {0};
        json_extract_string(data, len, "message", msg, sizeof(msg));
        json_extract_string(data, len, "reason", reason, sizeof(reason));
        ESP_LOGE(TAG, "server error: %s", msg);
        if (strcmp(msg, "auth failed") == 0) {
            s_authenticated = false;
        }
        voice_conversation_hw_post(VOICE_CONV_EVENT_SERVER_ERROR);
        if (strcmp(reason, "no_speech") == 0 ||
            strcmp(reason, "asr_failed") == 0 ||
            strcmp(reason, "audio_limit") == 0 ||
            strcmp(reason, "speech_turn_failed") == 0) {
            /* FSM is re-armed first, then keep a useful retry hint visible while
             * capture is already ready for the next utterance. */
            voice_ui_post_event(VOICE_UI_EVENT_RETRY,
                                msg[0] ? msg : "请再说一次");
        } else {
            voice_ui_post_event(VOICE_UI_EVENT_ERROR,
                                msg[0] ? msg : "server error");
        }
    } else if (strcmp(type, "heartbeat") == 0) {
        // server pong
    } else if (strcmp(type, "asr_partial") == 0) {
        char text[128] = {0};
        json_extract_string(data, len, "text", text, sizeof(text));
        if (text[0]) {
            voice_ui_post_event(VOICE_UI_EVENT_ASR_PARTIAL, text);
        }
    } else if (strcmp(type, "asr_result") == 0) {
        char text[128] = {0};
        json_extract_string(data, len, "text", text, sizeof(text));
        voice_ui_post_event(VOICE_UI_EVENT_ASR_RESULT, text);
        voice_conversation_hw_post(VOICE_CONV_EVENT_REPLY_PROGRESS);
    } else if (strcmp(type, "llm_prompt") == 0) {
        voice_conversation_hw_post(VOICE_CONV_EVENT_REPLY_PROGRESS);
    } else if (strcmp(type, "llm_reply_start") == 0) {
        voice_ui_post_event(VOICE_UI_EVENT_LLM_START, NULL);
        voice_conversation_hw_post(VOICE_CONV_EVENT_REPLY_PROGRESS);
    } else if (strcmp(type, "llm_reply_delta") == 0) {
        char text[128] = {0};
        json_extract_string(data, len, "text", text, sizeof(text));
        voice_ui_post_event(VOICE_UI_EVENT_LLM_DELTA, text);
    } else if (strcmp(type, "llm_reply_end") == 0 || strcmp(type, "llm_reply") == 0) {
        char text[128] = {0};
        json_extract_string(data, len, "text", text, sizeof(text));
        voice_ui_post_event(VOICE_UI_EVENT_LLM_END, text);
        voice_conversation_hw_post(VOICE_CONV_EVENT_REPLY_PROGRESS);
    } else if (strcmp(type, "tts_start") == 0) {
        voice_audio_playback_on_tts_start();
        voice_ui_post_event(VOICE_UI_EVENT_TTS_START, NULL);
        voice_conversation_hw_post(VOICE_CONV_EVENT_TTS_STARTED);
    } else if (strcmp(type, "show_color_test") == 0) {
        char color[16] = {0};
        json_extract_string(data, len, "color", color, sizeof(color));
        voice_display_lcd_show_color_test(color[0] ? color : "bars");
    } else if (strcmp(type, "show_character_trace") == 0) {
        char character[8] = {0};
        size_t stroke_count = 0;
        int duration_ms = 1800;
        json_extract_string(data, len, "character", character, sizeof(character));
        json_extract_int(data, len, "duration_ms", &duration_ms);
        voice_trace_stroke_t *strokes =
            heap_caps_calloc(VOICE_TRACE_MAX_STROKES, sizeof(voice_trace_stroke_t),
                             MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!strokes) {
            strokes = heap_caps_calloc(VOICE_TRACE_MAX_STROKES,
                                       sizeof(voice_trace_stroke_t), MALLOC_CAP_8BIT);
        }
        if (!strokes) {
            ESP_LOGW(TAG, "show_character_trace stroke alloc failed");
            return;
        }
        if (json_extract_trace_strokes(data, len, strokes, &stroke_count)) {
            // Outlines are large (up to 33×96 points): parse into a heap buffer,
            // not the WS task stack. Only used when the count matches the medians
            // so each stroke's fill aligns with its reveal order.
            voice_trace_outline_t *outlines =
                heap_caps_calloc(stroke_count, sizeof(voice_trace_outline_t),
                                 MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            if (!outlines) {
                outlines = heap_caps_calloc(stroke_count, sizeof(voice_trace_outline_t),
                                            MALLOC_CAP_8BIT);
            }
            const voice_trace_outline_t *outlines_arg = NULL;
            if (outlines) {
                size_t outline_count =
                    json_extract_trace_outlines(data, len, outlines, stroke_count);
                if (outline_count == stroke_count) {
                    outlines_arg = outlines;
                }
            }
            voice_display_lcd_show_trace(character, strokes, stroke_count,
                                         outlines_arg, (uint32_t)duration_ms);
            heap_caps_free(outlines);
        } else {
            ESP_LOGW(TAG, "show_character_trace missing valid strokes");
        }
        heap_caps_free(strokes);
    } else if (strcmp(type, "show_idiom") == 0) {
        // 成语大字：glyphs 列表里每个字一份 strokes+outlines；逐字解析并渲染到 2×2 格。
        const char *spans[VOICE_IDIOM_MAX_GLYPHS];
        int span_lens[VOICE_IDIOM_MAX_GLYPHS];
        size_t glyph_count =
            json_split_glyphs(data, len, spans, span_lens, VOICE_IDIOM_MAX_GLYPHS);
        if (glyph_count == 0) {
            ESP_LOGW(TAG, "show_idiom missing glyphs");
            return;
        }
        // One reusable strokes+outlines buffer for all glyphs (parsed serially),
        // sized to the worst-case single character — avoids holding the whole idiom.
        voice_trace_stroke_t *strokes =
            heap_caps_calloc(VOICE_TRACE_MAX_STROKES, sizeof(voice_trace_stroke_t),
                             MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!strokes) {
            strokes = heap_caps_calloc(VOICE_TRACE_MAX_STROKES,
                                       sizeof(voice_trace_stroke_t), MALLOC_CAP_8BIT);
        }
        voice_trace_outline_t *outlines =
            heap_caps_calloc(VOICE_TRACE_MAX_STROKES, sizeof(voice_trace_outline_t),
                             MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!outlines) {
            outlines = heap_caps_calloc(VOICE_TRACE_MAX_STROKES,
                                        sizeof(voice_trace_outline_t), MALLOC_CAP_8BIT);
        }
        if (!strokes || !outlines) {
            ESP_LOGW(TAG, "show_idiom buffer alloc failed");
            heap_caps_free(strokes);
            heap_caps_free(outlines);
            return;
        }
        voice_display_lcd_show_idiom_begin(glyph_count);
        for (size_t i = 0; i < glyph_count; i++) {
            char character[8] = {0};
            size_t stroke_count = 0;
            json_extract_string(spans[i], span_lens[i], "character", character,
                                sizeof(character));
            const voice_trace_outline_t *outlines_arg = NULL;
            if (json_extract_trace_strokes(spans[i], span_lens[i], strokes,
                                           &stroke_count)) {
                size_t outline_count = json_extract_trace_outlines(
                    spans[i], span_lens[i], outlines, stroke_count);
                if (outline_count == stroke_count) {
                    outlines_arg = outlines;
                }
            }
            voice_display_lcd_show_idiom_glyph(i, strokes, stroke_count, outlines_arg,
                                               character);
        }
        heap_caps_free(strokes);
        heap_caps_free(outlines);
    } else if (strcmp(type, "scene_changed") == 0) {
        // 进入持久人设场景（如英语学习）切「经典脸」，退出切回。动作型场景
        // （汉字/成语）不发此消息，故只随英语进出切脸。
        char action[16] = {0};
        json_extract_string(data, len, "scene_action", action, sizeof(action));
        if (strcmp(action, "enter") == 0) {
            voice_display_lcd_scene_enter_face();
        } else if (strcmp(action, "exit") == 0) {
            voice_display_lcd_scene_exit_face();
        }
    } else if (strcmp(type, "tts_end") == 0) {
        char reason[64] = {0};
        json_extract_string(data, len, "reason", reason, sizeof(reason));
        if (strcmp(reason, "barge_in") == 0) {
            voice_audio_playback_on_barge_in();
        } else {
            voice_audio_playback_on_tts_end(reason);
        }
        voice_ui_post_event(VOICE_UI_EVENT_TTS_END, reason);
        // NOTE: do NOT post TTS_FINISHED here. The server's tts_end only means
        // "all audio sent"; the device still has a full playback queue + DMA
        // tail to drain. The playback task posts TTS_FINISHED once it has truly
        // finished, so half-duplex capture reopens only after the speaker is
        // silent (avoids self-triggered barge-in). A thinking-timeout in the
        // conversation FSM is the safety net if playback never started.
    } else if (strcmp(type, "set_conversation") == 0) {
        bool enabled = false;
        if (json_extract_bool(data, len, "enabled", &enabled)) {
            if (enabled != voice_conversation_hw_active()) {
                voice_conversation_hw_toggle_deferred();
            }
            ESP_LOGI(TAG, "conversation mode set remotely: %s", enabled ? "on" : "off");
        } else {
            ESP_LOGW(TAG, "set_conversation missing enabled");
        }
    } else if (strcmp(type, "set_volume") == 0) {
        int volume = 85;
        if (json_extract_int(data, len, "volume", &volume)) {
            esp_err_t ret = voice_es8311_set_volume(volume);
            if (ret != ESP_OK) {
                ESP_LOGW(TAG, "set volume failed: %s", esp_err_to_name(ret));
            } else {
                ESP_LOGI(TAG, "volume command applied: %d", volume);
            }
        } else {
            ESP_LOGW(TAG, "set_volume missing volume");
        }
    } else {
        // M0 only logs other turn events; M1 will hook playback/status here.
        char text[96] = {0};
        if (json_extract_string(data, len, "text", text, sizeof(text)) > 0) {
            ESP_LOGI(TAG, "  text=%s", text);
        }
    }
}

/* Server control messages are UTF-8 text frames. The esp_websocket_client
 * delivers a single frame across multiple WEBSOCKET_EVENT_DATA callbacks when
 * the payload spans more than one TCP read (~1.4 KB MSS), keeping the frame's
 * op_code on every chunk and reporting the whole-frame size via payload_len plus
 * this chunk's payload_offset/data_len. Short control frames (auth_ok, asr_result,
 * set_volume, …) fit one segment and arrive whole; the larger
 * show_character_trace JSON (1–2.5 KB) gets chunked and used to be parsed from a
 * truncated buffer — so the trace overlay never opened on real hardware.
 * Reassemble the full frame here before dispatching. */
#define WS_TEXT_ASM_MAX 16384
static char *s_text_asm;
static int s_text_asm_cap;

static void handle_text_frame_chunk(const esp_websocket_event_data_t *data) {
    int total = data->payload_len;
    /* Fast path: the whole frame arrived in this one chunk. */
    if (data->payload_offset == 0 && data->data_len >= total) {
        if (data->data_len > 0) {
            handle_server_text((const char *)data->data_ptr, data->data_len);
        }
        return;
    }
    if (total <= 0 || total > WS_TEXT_ASM_MAX) {
        return;  // implausible size; drop rather than over-allocate
    }
    if (data->payload_offset == 0 || !s_text_asm || s_text_asm_cap < total) {
        free(s_text_asm);
        s_text_asm = malloc((size_t)total + 1);
        s_text_asm_cap = s_text_asm ? total : 0;
    }
    if (!s_text_asm || data->payload_offset < 0 ||
        data->payload_offset + data->data_len > s_text_asm_cap) {
        // out of sync (e.g. dropped first chunk): reset and wait for a fresh frame
        free(s_text_asm);
        s_text_asm = NULL;
        s_text_asm_cap = 0;
        return;
    }
    memcpy(s_text_asm + data->payload_offset, data->data_ptr, data->data_len);
    if (data->payload_offset + data->data_len >= total) {
        s_text_asm[total] = '\0';
        handle_server_text(s_text_asm, total);
        free(s_text_asm);
        s_text_asm = NULL;
        s_text_asm_cap = 0;
    }
}

static void on_ws_event(void *arg, esp_event_base_t base, int32_t event_id, void *event_data) {
    (void)arg;
    (void)base;
    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;
    switch ((esp_websocket_event_id_t)event_id) {
        case WEBSOCKET_EVENT_CONNECTED:
            ESP_LOGI(TAG, "connected to %s", voice_ws_env_url(s_ws_env));
            s_connected = true;
            s_authenticated = false;
            s_link_ok_us = esp_timer_get_time();
            voice_ui_post_event(VOICE_UI_EVENT_WS_CONNECTED, CONFIG_VR_DEVICE_ID);
            send_auth();
            break;
        case WEBSOCKET_EVENT_DISCONNECTED:
            ESP_LOGW(TAG, "disconnected");
            s_connected = false;
            s_authenticated = false;
            voice_ui_post_event(VOICE_UI_EVENT_WS_DISCONNECTED, NULL);
            // Capture must not stream into a dead link, but DO NOT take the conv
            // lock here: this runs on the WS event task and the audio-send path
            // holds the conv lock while waiting on the WS lock — that cross used
            // to wedge the client task and stop auto-reconnect. Defer the toggle
            // to the heartbeat timer task instead.
            s_conv_off_pending = true;
            s_scene_exit_pending = true;
            break;
        case WEBSOCKET_EVENT_DATA:
            if (data->op_code == 0x1) {  // text frame (may span multiple chunks)
                handle_text_frame_chunk(data);
            } else if (data->op_code == 0x2 && data->data_len > 0) {  // binary frame
                static int s_total = 0;
                s_total += data->data_len;
                voice_audio_playback_on_tts_chunk((const uint8_t *)data->data_ptr,
                                                  (size_t)data->data_len);
                // LOGD (compiled out at the default INFO level): logging every
                // ~1280-byte TTS frame floods the USB-Serial-JTAG console, and
                // when no host is draining it the write blocks the WS event task
                // long enough to drop the connection mid-playback.
                ESP_LOGD(TAG, "← binary chunk %d bytes (total=%d)",
                         data->data_len, s_total);
            }
            break;
        case WEBSOCKET_EVENT_ERROR:
            ESP_LOGE(TAG, "ws error");
            voice_ui_post_event(VOICE_UI_EVENT_ERROR, "websocket error");
            break;
        default:
            break;
    }
}

static void on_wifi_got_ip(void *arg, esp_event_base_t base,
                           int32_t event_id, void *event_data) {
    (void)arg; (void)base; (void)event_id; (void)event_data;
    if (!s_client || s_connected) {
        return;
    }
    ESP_LOGI(TAG, "wifi back; reconnecting websocket to %s",
             voice_ws_env_url(s_ws_env));
    esp_websocket_client_stop(s_client);
    esp_websocket_client_set_uri(s_client, voice_ws_env_url(s_ws_env));
    esp_websocket_client_start(s_client);
}

esp_err_t voice_ws_start(void) {
    ws_env_load();
    const char *ws_url = voice_ws_env_url(s_ws_env);
    esp_websocket_client_config_t cfg = {
        .uri = ws_url,
        .reconnect_timeout_ms = 2000,
        .network_timeout_ms = 5000,
        .buffer_size = 4096,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    voice_ui_post_event(VOICE_UI_EVENT_WS_CONNECTING, ws_url);
    s_client = esp_websocket_client_init(&cfg);
    if (!s_client) {
        ESP_LOGE(TAG, "init failed");
        return ESP_FAIL;
    }
    esp_websocket_register_events(s_client, WEBSOCKET_EVENT_ANY, on_ws_event, NULL);

    esp_event_handler_instance_t ip_inst;
    esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &on_wifi_got_ip, NULL, &ip_inst);

    ESP_ERROR_CHECK(esp_websocket_client_start(s_client));
    ESP_LOGI(TAG, "starting websocket client to %s as device_id=%s",
             ws_url, CONFIG_VR_DEVICE_ID);

    const esp_timer_create_args_t hb_args = {
        .callback = heartbeat_timer_cb,
        .name = "ws_hb",
    };
    esp_err_t err = esp_timer_create(&hb_args, &s_heartbeat_timer);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "heartbeat timer create failed: %s", esp_err_to_name(err));
        return err;
    }
    err = esp_timer_start_periodic(
        s_heartbeat_timer, (uint64_t)CONFIG_VR_HEARTBEAT_INTERVAL_MS * 1000);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "heartbeat timer start failed: %s", esp_err_to_name(err));
        return err;
    }
    return ESP_OK;
}
