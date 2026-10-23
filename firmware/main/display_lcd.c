#include "display_lcd.h"

#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "esp_log.h"
#include "sdkconfig.h"

static const char *TAG = "display";

#ifdef CONFIG_VR_BOARD_UI_ENABLE
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_st7789.h"
#include "esp_lcd_touch_cst816s.h"
#include "esp_lvgl_port.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"

#include <stdio.h>

#include "conversation.h"
#include "es8311_codec.h"
#include "face_eyes_sprites.h"
#include "face_mouth_sprites.h"
#include "battery.h"
#include "butterfly_sprites.h"
#include "i2c_bus.h"
#include "wifi_client.h"
#include "ws_client.h"

#include "nvs.h"

#define LCD_HOST SPI2_HOST

#define LCD_PIN_SCLK 6
#define LCD_PIN_MOSI 7
#define LCD_PIN_CS 5
#define LCD_PIN_DC 4
#define LCD_PIN_RST 38
#define LCD_PIN_BACKLIGHT 40

#define TOUCH_PIN_INT 13
#define TOUCH_PIN_RST 39

#define LCD_H_RES 240
#define LCD_V_RES 284
#define LCD_SPI_CLOCK_HZ (24 * 1000 * 1000)
#define LCD_CMD_BITS 8
#define LCD_PARAM_BITS 8
#define LCD_DRAW_BUF_LINES 40

#define TRACE_GRID_COLOR 0xFF0000
#define TRACE_BG_COLOR 0x000000
#define TRACE_STROKE_COLOR 0xFFFFFF
#define TRACE_GRID_SIZE 200
#define TRACE_GRID_X ((LCD_H_RES - TRACE_GRID_SIZE) / 2)
#define TRACE_GRID_Y 42
#define TRACE_STROKE_MAIN_WIDTH 11
#define TRACE_STROKE_CAP_SIZE 14
#define TRACE_STROKE_JOINT_SIZE 18
#define TRACE_MIN_TICK_MS 35
#define TRACE_MAX_TICK_MS 240
// Max gap (ms) between two taps to count as a double-tap (exit). A lone tap
// within this overlay always replays; a quick second tap exits.
#define TRACE_DOUBLE_TAP_MS 400
// 真正正楷: A8 ink-mask canvas rendered at the 田字格 resolution (native, no
// scaling — LVGL's fast A8 path needs an untransformed image). Per-stroke
// reveal cadence is bounded so each filled stroke is visible.
#define TRACE_CANVAS_PX TRACE_GRID_SIZE
// Writing speed (slower = calmer, more legible). The per-tick floor dominates for
// many-stroke characters; raising it from 30→60 roughly halves their racing pace,
// landing each stroke at ~SUBSTEPS*60 ≈ 0.5s. See TRACE_KAITI_DURATION_SCALE too.
#define TRACE_KAITI_MIN_TICK_MS 60
#define TRACE_KAITI_MAX_TICK_MS 280
// Sub-steps per stroke for the filled-stroke reveal. Keep this at 1 on the
// ESP32-S3 LCD: the old swept reveal re-filled each polygon 8 times from the
// LVGL task and could starve touch/animation on complex characters.
#define TRACE_KAITI_SUBSTEPS 1
// Filled outline rendering is intentionally bounded. Above this point the
// visual win is not worth risking a stuck LVGL task; use the skeleton fallback.
#define TRACE_OUTLINE_MAX_TOTAL_POINTS 900
// Stretch the server-provided animation duration so simple (few-stroke) chars,
// whose pace is set by duration rather than the tick floor, also slow down.
#define TRACE_KAITI_DURATION_SCALE_NUM 3
#define TRACE_KAITI_DURATION_SCALE_DEN 2
#define MENU_PULL_ZONE_H 34
/* Vertical travel (px) from the press point inside the hotspot that counts as a
 * deliberate top-down pull. Detected manually from PRESSING events rather than
 * via LV_EVENT_GESTURE, whose velocity gate makes slow pulls fail to register. */
#define MENU_PULL_TRIGGER 18
#define FACE_HIT_Y 36
#define FACE_EYES_Y 48
#define FACE_MOUTH_Y 167
#define FACE_HIT_H (FACE_MOUTH_Y + FACE_MOUTH_H - FACE_HIT_Y + 18)
#define CYCLE_LEN(arr) (sizeof(arr) / sizeof((arr)[0]))

/* Main-screen UI style: classic robot face vs. neon butterfly. Persisted in the
 * shared "vrcfg" NVS namespace (same as volume / ws-env). Boot default is the
 * butterfly. */
typedef enum {
    UI_STYLE_FACE = 0,
    UI_STYLE_BUTTERFLY = 1,
} ui_style_t;
#define UI_STYLE_NVS_NS "vrcfg"
#define UI_STYLE_NVS_KEY "ui_style"
#define UI_STYLE_DEFAULT UI_STYLE_BUTTERFLY

/* Butterfly layout: wings centred upper area, the expressive body overlaps the
 * wing centre. */
#define BF_WING_Y 60
#define BF_BODY_Y (BF_WING_Y + (BUTTERFLY_WING_H - BUTTERFLY_BODY_H) / 2 + 2)
#define BF_FACE_Y (BF_BODY_Y + BUTTERFLY_FACE_BODY_DY)
#define BF_WING_X ((LCD_H_RES - BUTTERFLY_WING_W) / 2)
#define BF_BODY_X ((LCD_H_RES - BUTTERFLY_BODY_W) / 2)
#define BF_FACE_X ((LCD_H_RES - BUTTERFLY_FACE_W) / 2)

static bool s_ready;
static lv_display_t *s_disp;
static esp_lcd_touch_handle_t s_touch;
static volatile uint32_t s_anim_heartbeat;
static TaskHandle_t s_ui_watchdog_task;

/* Project-local CJK subtitle font: full GB2312 (6763 hanzi + punctuation),
 * generated from SourceHanSansSC-Normal.otf via lv_font_conv. The LVGL built-in
 * lv_font_source_han_sans_sc_16_cjk only carries a ~1187-char demo subset, so
 * arbitrary LLM/TTS replies showed tofu (乱码) for any uncovered character. */
LV_FONT_DECLARE(lv_font_cjk_sc_16);

/* Widget tree (created once, updated by voice_display_lcd_render). */
static lv_obj_t *s_lbl_topbar;   /* small Chinese voice status, top-center */
static lv_obj_t *s_status_pill;  /* dark status capsule behind topbar */
static lv_obj_t *s_status_dot;   /* semantic state color + lightweight pulse */
static lv_obj_t *s_pull_zone;    /* top swipe strip opening the dropdown panel */
static lv_obj_t *s_img_eyes;     /* cyan eyes sprite (lv_image) */
static lv_obj_t *s_img_mouth;    /* cyan mouth sprite (lv_image) */
static lv_obj_t *s_face_hit;     /* transparent tap layer over the face */
static lv_obj_t *s_lbl_detail;
static lv_obj_t *s_subtitle_card;
static lv_obj_t *s_lbl_subtitle;

/* Butterfly UI (alternative main screen): wings recolor through a cycling hue
 * and flap; the body base is static, the face overlay carries the expression. */
static lv_obj_t *s_img_bf_wing;
static lv_obj_t *s_img_bf_body;
static lv_obj_t *s_img_bf_face;

#define SPARKLE_MAX 8
static lv_obj_t *s_sparkle_objs[SPARKLE_MAX];
static struct { int16_t x, y; int8_t dx, dy; uint8_t life; uint16_t hue; } s_sparkle[SPARKLE_MAX];
static uint32_t s_sparkle_rng = 12345;
static ui_style_t s_ui_style = UI_STYLE_DEFAULT;
// 意图场景临时切「经典脸」：进入场景记住原风格，退出/断连恢复（不写 NVS）。
static bool s_scene_face_active = false;
static ui_style_t s_scene_prev_style = UI_STYLE_DEFAULT;
static uint16_t s_bf_hue; /* 0..359, advances over time for the color cycle */

/* Settings panel (volume + network), an overlay built lazily on first open. */
static lv_obj_t *s_menu_overlay; /* full-screen dim mask + card; NULL=closed */
static lv_obj_t *s_slider_vol;
static lv_obj_t *s_lbl_vol;
static lv_obj_t *s_lbl_net;

/* Animation state driven by a periodic lv_timer (LVGL task context). */
static lv_timer_t *s_anim_timer;
static voice_ui_screen_t s_cur_screen = VOICE_UI_SCREEN_BOOT;
static voice_ui_screen_t s_prev_screen = VOICE_UI_SCREEN_BOOT;
static bool s_online; /* authenticated: blink/talk only when truly online */
static uint32_t s_tick;

/* Butterfly fly-away / fly-back animation when returning to idle. */
#define BF_FLY_AWAY_TICKS   12
#define BF_FLY_BACK_TICKS   14
#define BF_FLY_TOTAL_TICKS  (BF_FLY_AWAY_TICKS + BF_FLY_BACK_TICKS)
static uint8_t s_bf_fly_phase;   /* 0 = not flying */

/* Double-tap detection for force-stop conversation. */
static uint32_t s_last_tap_ms;

/* Full-screen character tracing overlay. It deliberately sits above the
 * conversation UI so tts/asr state changes keep flowing underneath. */
static lv_obj_t *s_trace_overlay;
static lv_obj_t *s_trace_lines;
static lv_timer_t *s_trace_timer;
static voice_trace_stroke_t s_trace_strokes[VOICE_TRACE_MAX_STROKES];
static size_t s_trace_stroke_count;
static size_t s_trace_current_stroke;
static uint8_t s_trace_current_segment;
static uint32_t s_trace_tick_ms;
static uint32_t s_trace_duration_ms;
static bool s_trace_completed;
/* Single-tap replays the animation; a quick double-tap (two taps within
 * TRACE_DOUBLE_TAP_MS) exits the overlay back to the conversation face.
 * s_trace_first_done gates taps until the very first write has completed once;
 * s_trace_last_tap_ms timestamps the previous tap for double-tap detection. */
static bool s_trace_first_done;
static uint32_t s_trace_last_tap_ms;
static lv_point_precise_t s_trace_grid_points[4][2];
static lv_point_precise_t s_trace_line_points[VOICE_TRACE_MAX_STROKES]
                                             [VOICE_TRACE_MAX_POINTS_PER_STROKE - 1][2];
/* 真正正楷 fill: A8 ink-mask canvas + per-stroke filled polygons. When
 * s_trace_outlines is non-NULL the overlay fills 楷体 strokes into the canvas in
 * stroke order; otherwise it falls back to the median-line skeleton above. */
static lv_obj_t *s_trace_canvas;        // white ink (written strokes)
static uint8_t *s_trace_canvas_buf;
static uint32_t s_trace_canvas_stride;
static voice_trace_outline_t *s_trace_outlines;
static uint8_t s_trace_kaiti_step;      // sub-step within the current stroke (sweep)
static lv_obj_t *s_color_test_overlay;

/* Load the persisted UI style, or UI_STYLE_DEFAULT (butterfly) when unset. */
static ui_style_t ui_style_load(void) {
    nvs_handle_t h;
    if (nvs_open(UI_STYLE_NVS_NS, NVS_READONLY, &h) != ESP_OK) {
        return UI_STYLE_DEFAULT;
    }
    uint8_t stored = 0;
    esp_err_t ret = nvs_get_u8(h, UI_STYLE_NVS_KEY, &stored);
    nvs_close(h);
    if (ret != ESP_OK || stored > UI_STYLE_BUTTERFLY) {
        return UI_STYLE_DEFAULT;
    }
    return (ui_style_t)stored;
}

/* Persist the chosen UI style. Best-effort: NVS failure must not break the UI. */
static void ui_style_save(ui_style_t style) {
    nvs_handle_t h;
    if (nvs_open(UI_STYLE_NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_u8(h, UI_STYLE_NVS_KEY, (uint8_t)style);
        nvs_commit(h);
        nvs_close(h);
    }
}

static void trace_overlay_reset_state(void) {
    memset(s_trace_strokes, 0, sizeof(s_trace_strokes));
    s_trace_stroke_count = 0;
    s_trace_current_stroke = 0;
    s_trace_current_segment = 0;
    s_trace_kaiti_step = 0;
    s_trace_tick_ms = 0;
    s_trace_duration_ms = 1800;
    s_trace_completed = false;
    s_trace_first_done = false;
    s_trace_last_tap_ms = 0;
}

static void color_test_overlay_close(void) {
    if (s_color_test_overlay) {
        lv_obj_del(s_color_test_overlay);
        s_color_test_overlay = NULL;
    }
}

typedef struct {
    const char *status_cn;  /* big Chinese status text */
    lv_color_t color;       /* per-state background tint base */
} screen_style_t;

static screen_style_t screen_style(voice_ui_screen_t screen) {
    switch (screen) {
        case VOICE_UI_SCREEN_BOOT:
            return (screen_style_t){"启动中", lv_color_hex(0x3A4A60)};
        case VOICE_UI_SCREEN_WIFI:
            return (screen_style_t){"连接网络", lv_color_hex(0x1F6FA8)};
        case VOICE_UI_SCREEN_CLOUD:
            return (screen_style_t){"连接云端", lv_color_hex(0x3C55B4)};
        case VOICE_UI_SCREEN_IDLE:
            return (screen_style_t){"点击开始对话", lv_color_hex(0x1E9E6A)};
        case VOICE_UI_SCREEN_RECORDING:
            return (screen_style_t){"收听中", lv_color_hex(0xC23B3B)};
        case VOICE_UI_SCREEN_RECOGNIZING:
            return (screen_style_t){"识别中", lv_color_hex(0xB07028)};
        case VOICE_UI_SCREEN_THINKING:
            return (screen_style_t){"生成中", lv_color_hex(0x9A7B1E)};
        case VOICE_UI_SCREEN_PLAYBACK:
            return (screen_style_t){"播放中", lv_color_hex(0x5A4ED2)};
        case VOICE_UI_SCREEN_BLE_GATEWAY:
            return (screen_style_t){"蓝牙等待手机", lv_color_hex(0x1E6E92)};
        case VOICE_UI_SCREEN_PROVISIONING:
            return (screen_style_t){"手机配网中", lv_color_hex(0x2A7FA8)};
        case VOICE_UI_SCREEN_ERROR:
            return (screen_style_t){"出错了", lv_color_hex(0x8E2433)};
    }
    return (screen_style_t){"", lv_color_hex(0x000000)};
}

static void main_button_cb(lv_event_t *event) {
    (void)event;
    if (!voice_ws_ready()) {
        ESP_LOGW(TAG, "tap ignored: not connected/authenticated yet");
        return;
    }
    uint32_t now = lv_tick_get();
    bool is_double = s_last_tap_ms != 0 && (now - s_last_tap_ms) < 400;
    s_last_tap_ms = now;
    bool active = voice_conversation_hw_active();
    if (is_double && active) {
        voice_conversation_hw_toggle_deferred();
        ESP_LOGI(TAG, "double-tap: force stop conversation");
        return;
    }
    if (!active) {
        voice_conversation_hw_toggle_deferred();
    }
}

static bool screen_is_online(voice_ui_screen_t s) {
    return s == VOICE_UI_SCREEN_IDLE || s == VOICE_UI_SCREEN_RECORDING ||
           s == VOICE_UI_SCREEN_RECOGNIZING || s == VOICE_UI_SCREEN_THINKING ||
           s == VOICE_UI_SCREEN_PLAYBACK;
}

static bool screen_detail_is_dialog_context(voice_ui_screen_t s) {
    return s == VOICE_UI_SCREEN_RECORDING || s == VOICE_UI_SCREEN_RECOGNIZING ||
           s == VOICE_UI_SCREEN_THINKING || s == VOICE_UI_SCREEN_PLAYBACK ||
           s == VOICE_UI_SCREEN_BLE_GATEWAY || s == VOICE_UI_SCREEN_PROVISIONING ||
           s == VOICE_UI_SCREEN_ERROR;
}

/* Per-sprite frame caches so we only invalidate when a frame actually changes.
 * File-scope (not function-static) so ui_style_apply can reset them when the
 * widget tree is rebuilt — otherwise they'd point at deleted objects' frames. */
static const lv_image_dsc_t *s_last_eyes;
static const lv_image_dsc_t *s_last_mouth;
static int s_last_eye_x = 999;
static int s_last_eye_y = 999;
static int s_last_mouth_x = 999;
static int s_last_mouth_y = 999;
static const lv_image_dsc_t *s_last_bf_wing;
static const lv_image_dsc_t *s_last_bf_face;
static int s_last_bf_hue = -1;

static void anim_reset_caches(void) {
    s_last_eyes = NULL;
    s_last_mouth = NULL;
    s_last_eye_x = s_last_eye_y = 999;
    s_last_mouth_x = s_last_mouth_y = 999;
    s_last_bf_wing = NULL;
    s_last_bf_face = NULL;
    s_last_bf_hue = -1;
}

/* Periodic face animation (LVGL task context). Picks an EYE frame and a MOUTH
 * frame per voice state; the mouth animates open/close while speaking, the eyes
 * blink while idle and switch expression (surprised / question / happy ...) to
 * reflect the conversation state. Only invalidates a sprite when its frame
 * actually changes, to keep the SPI flush off the audio path. */
static void anim_face_tick(void) {
    if (!s_img_eyes || !s_img_mouth) {
        return;
    }

    static const lv_image_dsc_t *const idle_eyes_cycle[] = {
        &face_eyes_neutral, &face_eyes_neutral, &face_eyes_happy,
        &face_eyes_neutral, &face_eyes_blink, &face_eyes_neutral,
    };
    static const lv_image_dsc_t *const recording_eyes_cycle[] = {
        &face_eyes_surprised, &face_eyes_surprised, &face_eyes_blink,
        &face_eyes_happy, &face_eyes_surprised, &face_eyes_happy,
    };
    static const lv_image_dsc_t *const recognizing_eyes_cycle[] = {
        &face_eyes_question, &face_eyes_happy, &face_eyes_question,
        &face_eyes_blink, &face_eyes_question, &face_eyes_surprised,
    };
    static const lv_image_dsc_t *const thinking_eyes_cycle[] = {
        &face_eyes_question, &face_eyes_question, &face_eyes_blink,
        &face_eyes_angry, &face_eyes_question, &face_eyes_sad,
    };
    static const lv_image_dsc_t *const playback_eyes_cycle[] = {
        &face_eyes_happy, &face_eyes_happy, &face_eyes_surprised,
        &face_eyes_happy, &face_eyes_blink, &face_eyes_happy,
    };
    static const lv_image_dsc_t *const recording_mouth_cycle[] = {
        &face_mouth_smile_small, &face_mouth_o_small, &face_mouth_smile_small,
        &face_mouth_open1, &face_mouth_smile_small, &face_mouth_open1,
    };
    static const lv_image_dsc_t *const recognizing_mouth_cycle[] = {
        &face_mouth_smile_small, &face_mouth_o_small, &face_mouth_smile_small,
        &face_mouth_teardrop, &face_mouth_smile_small, &face_mouth_o_small,
    };
    static const lv_image_dsc_t *const thinking_mouth_cycle[] = {
        &face_mouth_teardrop, &face_mouth_smile_small, &face_mouth_frown,
        &face_mouth_teardrop, &face_mouth_smile_small, &face_mouth_o_small,
    };
    static const lv_image_dsc_t *const playback_mouth_cycle[] = {
        &face_mouth_smile_small, &face_mouth_open1, &face_mouth_open2,
        &face_mouth_smile_small, &face_mouth_o_sing, &face_mouth_open1,
        &face_mouth_smile_small, &face_mouth_open_tongue,
    };
    static const int8_t recording_eyes_x_cycle[] = {0, 0, -1, 0, 1, 0};
    static const int8_t recording_eyes_y_cycle[] = {0, -1, 0, 1, 0, -1};
    static const int8_t recording_mouth_y_cycle[] = {0, 0, 1, 0, -1, 0};
    static const int8_t recognizing_eyes_x_cycle[] = {-1, 0, 1, 0, -1, 1};
    static const int8_t recognizing_eyes_y_cycle[] = {0, -1, 0, 1, 0, 0};
    static const int8_t recognizing_mouth_y_cycle[] = {0, 1, 0, 1, 0, 0};
    static const int8_t thinking_eyes_x_cycle[] = {0, 1, 0, -1, 0, 1};
    static const int8_t thinking_eyes_y_cycle[] = {0, 0, -1, 0, 1, 0};
    static const int8_t thinking_mouth_y_cycle[] = {0, 1, 0, 0, 1, 0};
    static const int8_t playback_eyes_x_cycle[] = {0, -1, 0, 1, 0, 0, 1, 0};
    static const int8_t playback_eyes_y_cycle[] = {0, -1, 0, 1, 0, -1, 0, 1};
    static const int8_t playback_mouth_x_cycle[] = {0, 0, 1, 0, -1, 0, 1, 0};
    static const int8_t playback_mouth_y_cycle[] = {0, 1, 0, -1, 0, 1, 0, -1};

    const lv_image_dsc_t *eyes = &face_eyes_neutral;
    const lv_image_dsc_t *mouth = &face_mouth_smile_small;
    int eye_x = 0;
    int eye_y = FACE_EYES_Y;
    int mouth_x = 0;
    int mouth_y = FACE_MOUTH_Y;

    switch (s_cur_screen) {
        case VOICE_UI_SCREEN_RECORDING:
            /* 收听中: attentive ears-up face with small mouth movements. */
            eyes = recording_eyes_cycle[(s_tick / 4u) % CYCLE_LEN(recording_eyes_cycle)];
            mouth = recording_mouth_cycle[(s_tick / 3u) % CYCLE_LEN(recording_mouth_cycle)];
            eye_x = recording_eyes_x_cycle[(s_tick / 4u) % CYCLE_LEN(recording_eyes_x_cycle)];
            eye_y +=
                recording_eyes_y_cycle[(s_tick / 4u) % CYCLE_LEN(recording_eyes_y_cycle)];
            mouth_y +=
                recording_mouth_y_cycle[(s_tick / 3u) % CYCLE_LEN(recording_mouth_y_cycle)];
            break;
        case VOICE_UI_SCREEN_RECOGNIZING:
            /* 识别中: brighter curious scan while ASR is working. */
            eyes = recognizing_eyes_cycle[(s_tick / 4u) % CYCLE_LEN(recognizing_eyes_cycle)];
            mouth =
                recognizing_mouth_cycle[(s_tick / 3u) % CYCLE_LEN(recognizing_mouth_cycle)];
            eye_x +=
                recognizing_eyes_x_cycle[(s_tick / 4u) % CYCLE_LEN(recognizing_eyes_x_cycle)];
            eye_y +=
                recognizing_eyes_y_cycle[(s_tick / 4u) % CYCLE_LEN(recognizing_eyes_y_cycle)];
            mouth_y += recognizing_mouth_y_cycle[(s_tick / 3u) %
                                                 CYCLE_LEN(recognizing_mouth_y_cycle)];
            break;
        case VOICE_UI_SCREEN_THINKING:
            /* 识别中/生成中: eyes wander between curious and pondering. */
            eyes = thinking_eyes_cycle[(s_tick / 5u) % CYCLE_LEN(thinking_eyes_cycle)];
            mouth = thinking_mouth_cycle[(s_tick / 4u) % CYCLE_LEN(thinking_mouth_cycle)];
            eye_x += thinking_eyes_x_cycle[(s_tick / 5u) % CYCLE_LEN(thinking_eyes_x_cycle)];
            eye_y += thinking_eyes_y_cycle[(s_tick / 5u) % CYCLE_LEN(thinking_eyes_y_cycle)];
            mouth_y +=
                thinking_mouth_y_cycle[(s_tick / 4u) % CYCLE_LEN(thinking_mouth_y_cycle)];
            break;
        case VOICE_UI_SCREEN_PLAYBACK:
            /* 播放中 (说话): cheerful lip-sync with lighter, less jarring motion. */
            eyes = playback_eyes_cycle[(s_tick / 2u) % CYCLE_LEN(playback_eyes_cycle)];
            mouth = playback_mouth_cycle[(s_tick / 2u) % CYCLE_LEN(playback_mouth_cycle)];
            eye_x += playback_eyes_x_cycle[(s_tick / 2u) % CYCLE_LEN(playback_eyes_x_cycle)];
            eye_y += playback_eyes_y_cycle[(s_tick / 2u) % CYCLE_LEN(playback_eyes_y_cycle)];
            mouth_x +=
                playback_mouth_x_cycle[(s_tick / 2u) % CYCLE_LEN(playback_mouth_x_cycle)];
            mouth_y +=
                playback_mouth_y_cycle[(s_tick / 2u) % CYCLE_LEN(playback_mouth_y_cycle)];
            break;
        case VOICE_UI_SCREEN_IDLE: {
            /* idle/开启: calm and alive. Eyes stay open most of the time with
             * brief, natural single-frame blinks on an irregular schedule, plus
             * occasional short expression "glances" (happy / curious) and a slow
             * look-around drift, instead of the old fast metronomic blink. */
            (void)idle_eyes_cycle;
            /* Two coprime-ish periods → blinks land ~every 4–6s, each lasting a
             * single 90 ms frame (≈ a real blink); sometimes they fall close
             * together for a natural double blink. */
            bool blink_now = (s_tick % 47u == 0u) || (s_tick % 71u == 13u);
            uint32_t phase = s_tick % 220u;  // ~20 s super-cycle of subtle moods
            if (blink_now) {
                eyes = &face_eyes_blink;
            } else if (phase < 18u) {
                eyes = &face_eyes_happy;       /* brief content squint */
            } else if (phase >= 96u && phase < 106u) {
                eyes = &face_eyes_surprised;   /* brief curious look-up */
            } else if (phase >= 150u && phase < 162u) {
                eyes = &face_eyes_happy;
            } else {
                eyes = &face_eyes_neutral;
            }
            /* Slow gaze drift so the face never feels frozen. */
            static const int8_t idle_eye_x[] = {0, 0, 1, 1, 0, -1, -1, 0};
            static const int8_t idle_eye_y[] = {0, -1, 0, 0, 1, 0, 0, -1};
            eye_x += idle_eye_x[(s_tick / 20u) % CYCLE_LEN(idle_eye_x)];
            eye_y += idle_eye_y[(s_tick / 20u) % CYCLE_LEN(idle_eye_y)];
            /* Mostly a gentle smile; a brief "o" now and then like a soft breath. */
            mouth = (s_online && phase >= 200u && phase < 206u)
                        ? &face_mouth_o_small
                        : &face_mouth_smile_small;
            break;
        }
        case VOICE_UI_SCREEN_ERROR:
            eyes = &face_eyes_sad;
            mouth = &face_mouth_frown;
            break;
        default:
            /* boot / wifi / cloud etc.: stay calm with a quick, natural blink. */
            eyes = (s_tick % 50u == 0u) ? &face_eyes_blink : &face_eyes_neutral;
            mouth = &face_mouth_smile_small;
            break;
    }

    if (eyes != s_last_eyes) {
        s_last_eyes = eyes;
        lv_image_set_src(s_img_eyes, eyes);
    }
    if (mouth != s_last_mouth) {
        s_last_mouth = mouth;
        lv_image_set_src(s_img_mouth, mouth);
    }
    if (eye_x != s_last_eye_x || eye_y != s_last_eye_y) {
        s_last_eye_x = eye_x;
        s_last_eye_y = eye_y;
        lv_obj_align(s_img_eyes, LV_ALIGN_TOP_MID, eye_x, eye_y);
    }
    if (mouth_x != s_last_mouth_x || mouth_y != s_last_mouth_y) {
        s_last_mouth_x = mouth_x;
        s_last_mouth_y = mouth_y;
        lv_obj_align(s_img_mouth, LV_ALIGN_TOP_MID, mouth_x, mouth_y);
    }
}

static uint16_t sparkle_rand16(void) {
    s_sparkle_rng = s_sparkle_rng * 1103515245u + 12345u;
    return (uint16_t)(s_sparkle_rng >> 16);
}

static void sparkle_spawn(int i) {
    int cx = LCD_H_RES / 2;
    int cy = BF_WING_Y + BUTTERFLY_WING_H / 2;
    int16_t ox = (int16_t)(sparkle_rand16() % BUTTERFLY_WING_W) - BUTTERFLY_WING_W / 2;
    int16_t oy = (int16_t)(sparkle_rand16() % BUTTERFLY_WING_H) - BUTTERFLY_WING_H / 2;
    s_sparkle[i].x = (int16_t)(cx + ox);
    s_sparkle[i].y = (int16_t)(cy + oy);
    s_sparkle[i].dx = (int8_t)(ox > 0 ? 1 + sparkle_rand16() % 2 : -(1 + sparkle_rand16() % 2));
    s_sparkle[i].dy = (int8_t)(oy > 0 ? 1 + sparkle_rand16() % 2 : -(1 + sparkle_rand16() % 2));
    s_sparkle[i].life = 8 + sparkle_rand16() % 14;
    s_sparkle[i].hue = sparkle_rand16() % 360;
}

static void sparkle_tick(void) {
    for (int i = 0; i < SPARKLE_MAX; i++) {
        if (!s_sparkle_objs[i]) continue;
        if (s_sparkle[i].life == 0) {
            if (sparkle_rand16() % 5 == 0) {
                sparkle_spawn(i);
            } else {
                lv_obj_add_flag(s_sparkle_objs[i], LV_OBJ_FLAG_HIDDEN);
                continue;
            }
        }
        s_sparkle[i].x += s_sparkle[i].dx;
        s_sparkle[i].y += s_sparkle[i].dy;
        s_sparkle[i].life--;
        lv_obj_clear_flag(s_sparkle_objs[i], LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_pos(s_sparkle_objs[i], s_sparkle[i].x, s_sparkle[i].y);
        uint8_t opa = s_sparkle[i].life > 15 ? LV_OPA_COVER
                     : (uint8_t)(s_sparkle[i].life * 255 / 15);
        lv_obj_set_style_bg_opa(s_sparkle_objs[i], opa, 0);
        lv_obj_set_style_bg_color(s_sparkle_objs[i],
                                  lv_color_hsv_to_rgb(s_sparkle[i].hue, 85, 100), 0);
    }
}

static void bf_set_fly_transform(uint8_t phase) {
    if (!s_img_bf_wing || !s_img_bf_body) return;

    if (phase == 0 || phase > BF_FLY_TOTAL_TICKS) {
        lv_obj_clear_flag(s_img_bf_wing, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_img_bf_body, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_opa(s_img_bf_wing, LV_OPA_COVER, 0);
        lv_obj_set_style_opa(s_img_bf_body, LV_OPA_COVER, 0);
        lv_obj_set_pos(s_img_bf_wing, BF_WING_X, BF_WING_Y);
        lv_obj_set_pos(s_img_bf_body, BF_BODY_X, BF_BODY_Y);
        lv_obj_set_style_transform_scale(s_img_bf_body, 256, 0);
        if (s_img_bf_face) {
            lv_obj_clear_flag(s_img_bf_face, LV_OBJ_FLAG_HIDDEN);
            lv_obj_set_style_opa(s_img_bf_face, LV_OPA_COVER, 0);
            lv_obj_set_pos(s_img_bf_face, BF_FACE_X, BF_FACE_Y);
            lv_obj_set_style_transform_scale(s_img_bf_face, 256, 0);
        }
        return;
    }

    int16_t up_range = BF_WING_Y + BUTTERFLY_WING_H + 20;
    int16_t down_range = LCD_V_RES - BF_WING_Y + 20;
    int16_t dy;
    uint8_t opa;
    uint16_t scl;
    if (phase <= BF_FLY_AWAY_TICKS) {
        int t = phase;
        dy   = (int16_t)(-(t * up_range) / BF_FLY_AWAY_TICKS);
        opa  = (uint8_t)(255 - (t * 230) / BF_FLY_AWAY_TICKS);
        scl  = (uint16_t)(256 - (t * 180) / BF_FLY_AWAY_TICKS);
    } else {
        int remain = BF_FLY_TOTAL_TICKS - phase;
        dy   = (int16_t)((remain * down_range) / BF_FLY_BACK_TICKS);
        opa  = (uint8_t)(255 - (remain * 230) / BF_FLY_BACK_TICKS);
        scl  = (uint16_t)(256 - (remain * 180) / BF_FLY_BACK_TICKS);
    }

    /* Keep the image objects alive and visible through the whole fly-away.
     * Toggling HIDDEN while the state renderer updates labels/styles can force
     * expensive object-tree invalidation on the LVGL task. On the real device
     * the double-tap listening→idle path could then stall the animation timer
     * long enough for the UI watchdog to reboot. Position + opacity changes
     * are sufficient here and keep the frame cost bounded. */
    if (opa < 30) opa = 30;
    lv_obj_clear_flag(s_img_bf_wing, LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(s_img_bf_body, LV_OBJ_FLAG_HIDDEN);

    lv_obj_set_style_opa(s_img_bf_wing, opa, 0);
    lv_obj_set_pos(s_img_bf_wing, BF_WING_X, BF_WING_Y + dy);

    lv_obj_set_style_opa(s_img_bf_body, opa, 0);
    lv_obj_set_style_transform_scale(s_img_bf_body, scl, 0);
    lv_obj_set_pos(s_img_bf_body, BF_BODY_X, BF_BODY_Y + dy);

    if (s_img_bf_face) {
        lv_obj_clear_flag(s_img_bf_face, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_opa(s_img_bf_face, opa, 0);
        lv_obj_set_style_transform_scale(s_img_bf_face, scl, 0);
        lv_obj_set_pos(s_img_bf_face, BF_FACE_X, BF_FACE_Y + dy);
    }
}

static void anim_butterfly_tick(void) {
    if (!s_img_bf_wing || !s_img_bf_body) {
        return;
    }

    if (s_bf_fly_phase > 0) {
        static const lv_image_dsc_t *const fly_wing_cycle[] = {
            &butterfly_wing_open, &butterfly_wing_mid, &butterfly_wing_close,
            &butterfly_wing_mid,
        };
        lv_image_set_src(s_img_bf_wing,
                         fly_wing_cycle[s_tick % CYCLE_LEN(fly_wing_cycle)]);
        bf_set_fly_transform(s_bf_fly_phase);
        s_bf_fly_phase++;
        if (s_bf_fly_phase > BF_FLY_TOTAL_TICKS) {
            s_bf_fly_phase = 0;
            bf_set_fly_transform(0);
            anim_reset_caches();
        }

        uint8_t hue_step = 12;
        s_bf_hue = (uint16_t)((s_bf_hue + hue_step) % 360u);
        s_last_bf_hue = -1;
        lv_color_t wc = lv_color_hsv_to_rgb(s_bf_hue, 92, 100);
        lv_obj_set_style_image_recolor(s_img_bf_wing, wc, 0);
        lv_obj_set_style_image_recolor_opa(s_img_bf_wing, LV_OPA_COVER, 0);
        lv_color_t bc = lv_color_hsv_to_rgb((s_bf_hue + 50u) % 360u, 65, 95);
        lv_obj_set_style_image_recolor(s_img_bf_body, bc, 0);
        lv_obj_set_style_image_recolor_opa(s_img_bf_body, LV_OPA_COVER, 0);
        return;
    }

    static const lv_image_dsc_t *const wing_cycle[] = {
        &butterfly_wing_open,     &butterfly_wing_open_mid,
        &butterfly_wing_mid,      &butterfly_wing_mid_close,
        &butterfly_wing_close,    &butterfly_wing_mid_close,
        &butterfly_wing_mid,      &butterfly_wing_open_mid,
    };

    const lv_image_dsc_t *wing = &butterfly_wing_open;
    const lv_image_dsc_t *face = &butterfly_face_idle;

    switch (s_cur_screen) {
        case VOICE_UI_SCREEN_RECORDING:
            face = &butterfly_face_focus;
            wing = wing_cycle[s_tick % CYCLE_LEN(wing_cycle)];
            break;
        case VOICE_UI_SCREEN_RECOGNIZING:
            face = &butterfly_face_curious;
            wing = wing_cycle[s_tick % CYCLE_LEN(wing_cycle)];
            break;
        case VOICE_UI_SCREEN_THINKING:
            face = &butterfly_face_think;
            wing = wing_cycle[(s_tick / 2u) % CYCLE_LEN(wing_cycle)];
            break;
        case VOICE_UI_SCREEN_PLAYBACK:
            face = &butterfly_face_talk;
            wing = wing_cycle[s_tick % CYCLE_LEN(wing_cycle)];
            break;
        case VOICE_UI_SCREEN_ERROR:
            face = &butterfly_face_sad;
            wing = &butterfly_wing_mid;
            break;
        case VOICE_UI_SCREEN_IDLE:
            face = (s_online && (s_tick % 61u == 0u)) ? &butterfly_face_talk
                                                       : &butterfly_face_idle;
            wing = wing_cycle[(s_tick / 3u) % CYCLE_LEN(wing_cycle)];
            break;
        default:
            face = &butterfly_face_idle;
            wing = wing_cycle[(s_tick / 4u) % CYCLE_LEN(wing_cycle)];
            break;
    }

    if (wing != s_last_bf_wing) {
        s_last_bf_wing = wing;
        lv_image_set_src(s_img_bf_wing, wing);
    }
    if (face != s_last_bf_face && s_img_bf_face) {
        s_last_bf_face = face;
        lv_image_set_src(s_img_bf_face, face);
    }

    uint8_t hue_step = (s_cur_screen == VOICE_UI_SCREEN_PLAYBACK) ? 9 : 6;
    if ((s_tick % 4u) == 0u) {
        s_bf_hue = (uint16_t)((s_bf_hue + hue_step) % 360u);
    }
    if ((int)s_bf_hue != s_last_bf_hue) {
        s_last_bf_hue = (int)s_bf_hue;
        lv_color_t wc = lv_color_hsv_to_rgb(s_bf_hue, 92, 100);
        lv_obj_set_style_image_recolor(s_img_bf_wing, wc, 0);
        lv_obj_set_style_image_recolor_opa(s_img_bf_wing, LV_OPA_COVER, 0);
        lv_color_t bc = lv_color_hsv_to_rgb((s_bf_hue + 50u) % 360u, 65, 95);
        lv_obj_set_style_image_recolor(s_img_bf_body, bc, 0);
        lv_obj_set_style_image_recolor_opa(s_img_bf_body, LV_OPA_COVER, 0);
    }

    if ((s_tick % 3u) == 0u) {
        sparkle_tick();
    }
}

static void anim_timer_cb(lv_timer_t *timer) {
    (void)timer;
    s_anim_heartbeat++;
    s_tick++;
    if (s_status_dot) {
        /* One existing 90ms timer drives the subtle state pulse too; no extra
         * task/timer means the UI remains cheap beside audio and WebSocket I/O. */
        uint8_t wave = (uint8_t)(s_tick % 12u);
        uint8_t opa = (uint8_t)(wave <= 6u ? 150u + wave * 16u
                                          : 150u + (12u - wave) * 16u);
        lv_obj_set_style_opa(s_status_dot, opa, 0);
    }
    if (s_ui_style == UI_STYLE_BUTTERFLY) {
        anim_butterfly_tick();
    } else {
        anim_face_tick();
    }
}

static void ui_watchdog_task(void *arg) {
    (void)arg;
    uint32_t last = 0;
    int stale_count = 0;
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(5000));
        if (!s_ready) {
            last = s_anim_heartbeat;
            stale_count = 0;
            continue;
        }
        uint32_t now = s_anim_heartbeat;
        if (now == last) {
            stale_count++;
            ESP_LOGW(TAG, "LVGL animation heartbeat stalled (%d)", stale_count);
            if (stale_count >= 2) {
                ESP_LOGE(TAG, "LVGL task appears stuck; rebooting UI device");
                esp_restart();
            }
        } else {
            stale_count = 0;
        }
        last = now;
    }
}

/* --- Settings dropdown (volume + network) ------------------------------- */

static void menu_open(void);
static void ui_style_apply(ui_style_t style);
static void idiom_overlay_close(void);

static void menu_refresh_net(void) {
    char ssid[33] = {0};
    char ip[16] = {0};
    int rssi = 0;
    char buf[96];
    if (voice_wifi_get_info(ssid, sizeof(ssid), ip, sizeof(ip), &rssi)) {
        snprintf(buf, sizeof(buf), "WiFi: %s\nIP: %s\n信号: %d dBm",
                 ssid[0] ? ssid : "-", ip[0] ? ip : "-", rssi);
    } else {
        snprintf(buf, sizeof(buf), "WiFi: 未连接");
    }
    lv_label_set_text(s_lbl_net, buf);
}

static void menu_close(void) {
    if (s_menu_overlay) {
        lv_obj_del(s_menu_overlay);
        s_menu_overlay = NULL;
        s_slider_vol = NULL;
        s_lbl_vol = NULL;
        s_lbl_net = NULL;
    }
}

static void menu_close_cb(lv_event_t *e) {
    (void)e;
    menu_close();
}

/* Top-down pull detection. We track the touch from the moment it lands inside
 * the hotspot and open the settings panel once it has been dragged downward by
 * MENU_PULL_TRIGGER pixels. This replaces LV_EVENT_GESTURE, which only fires on
 * a fast flick (its velocity gate resets the accumulator on slow drags), so the
 * pull would silently fail to trigger on a deliberate, steady swipe. */
static int32_t s_pull_start_y;
static bool s_pull_armed;

static void menu_pull_zone_press_cb(lv_event_t *e) {
    lv_event_code_t code = lv_event_get_code(e);
    lv_indev_t *indev = lv_event_get_indev(e);
    if (!indev) {
        return;
    }
    lv_point_t p;
    lv_indev_get_point(indev, &p);

    if (code == LV_EVENT_PRESSED) {
        s_pull_start_y = p.y;
        s_pull_armed = true;
        return;
    }
    if (code == LV_EVENT_PRESSING) {
        if (!s_pull_armed || s_menu_overlay) {
            return;
        }
        if (p.y - s_pull_start_y >= MENU_PULL_TRIGGER) {
            s_pull_armed = false; /* one open per press */
            menu_open();
        }
        return;
    }
    if (code == LV_EVENT_CLICKED) {
        s_pull_armed = false;
        if (!s_menu_overlay) {
            menu_open();
        }
        return;
    }
    if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        s_pull_armed = false;
    }
}

static void vol_slider_cb(lv_event_t *e) {
    (void)e;
    int v = (int)lv_slider_get_value(s_slider_vol);
    voice_es8311_set_volume(v);
    char buf[24];
    snprintf(buf, sizeof(buf), "音量 %d", v);
    lv_label_set_text(s_lbl_vol, buf);
}

/* Backend env switch (本地 / 云端): apply + reconnect, then close the menu. */
static void env_btn_cb(lv_event_t *e) {
    int env = (int)(intptr_t)lv_event_get_user_data(e);
    voice_ws_set_env(env);
    menu_close();
}

static void wifi_preset_btn_cb(lv_event_t *e) {
    int preset = (int)(intptr_t)lv_event_get_user_data(e);
    /* This call persists the selection and restarts the device. */
    voice_wifi_select_preset(preset);
}

static void ui_style_btn_cb(lv_event_t *e) {
    ui_style_t style = (ui_style_t)(intptr_t)lv_event_get_user_data(e);
    ui_style_apply(style);
    menu_close();
}

static void menu_open(void) {
    if (s_menu_overlay) {
        return;
    }
    idiom_overlay_close();
    lv_obj_t *scr = lv_screen_active();

    /* Dim full-screen mask; tapping the mask (outside the card) closes it. */
    s_menu_overlay = lv_obj_create(scr);
    lv_obj_remove_style_all(s_menu_overlay);
    lv_obj_set_size(s_menu_overlay, LCD_H_RES, LCD_V_RES);
    lv_obj_set_style_bg_color(s_menu_overlay, lv_color_hex(0x000000), 0);
    lv_obj_set_style_bg_opa(s_menu_overlay, LV_OPA_50, 0);
    lv_obj_clear_flag(s_menu_overlay, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(s_menu_overlay, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(s_menu_overlay, menu_close_cb, LV_EVENT_CLICKED, NULL);

    /* Card. */
    lv_obj_t *card = lv_obj_create(s_menu_overlay);
    lv_obj_set_size(card, LCD_H_RES - 24, 276);
    lv_obj_center(card);
    lv_obj_set_style_bg_color(card, lv_color_hex(0x232732), 0);
    lv_obj_set_style_border_width(card, 0, 0);
    lv_obj_set_style_radius(card, 14, 0);
    lv_obj_set_style_shadow_width(card, 0, 0);
    lv_obj_set_style_shadow_opa(card, LV_OPA_TRANSP, 0);
    lv_obj_set_style_pad_all(card, 12, 0);
    lv_obj_set_flex_flow(card, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(card, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER);
    /* Swallow clicks on the card so they don't reach the closing mask. */
    lv_obj_add_flag(card, LV_OBJ_FLAG_CLICKABLE);

    lv_obj_t *title = lv_label_create(card);
    lv_obj_set_style_text_font(title, &lv_font_cjk_sc_16, 0);
    lv_obj_set_style_text_color(title, lv_color_hex(0xF2F2F2), 0);
    lv_label_set_text(title, "设置");

    /* Battery percentage (AXP2101 fuel gauge). */
    {
        lv_obj_t *bat = lv_label_create(card);
        lv_obj_set_style_text_font(bat, &lv_font_cjk_sc_16, 0);
        lv_obj_set_style_text_color(bat, lv_color_hex(0xC8D0DC), 0);
        lv_obj_set_style_pad_top(bat, 4, 0);
        int pct = battery_get_percent();
        bool chg = battery_is_charging();
        char buf[32];
        if (pct < 0) {
            snprintf(buf, sizeof(buf), "电池：N/A");
        } else {
            snprintf(buf, sizeof(buf), "电池：%d%%%s", pct, chg ? " 充电中" : "");
        }
        lv_label_set_text(bat, buf);
    }

    /* Main-screen style switch (placed first for quick access). */
    lv_obj_t *ui_title = lv_label_create(card);
    lv_obj_set_style_text_font(ui_title, &lv_font_cjk_sc_16, 0);
    lv_obj_set_style_text_color(ui_title, lv_color_hex(0xC8D0DC), 0);
    lv_obj_set_style_pad_top(ui_title, 8, 0);
    lv_label_set_text(ui_title,
                      s_ui_style == UI_STYLE_BUTTERFLY ? "界面（当前：蝴蝶）"
                                                        : "界面（当前：经典脸）");

    lv_obj_t *ui_row = lv_obj_create(card);
    lv_obj_remove_style_all(ui_row);
    lv_obj_set_size(ui_row, LCD_H_RES - 56, LV_SIZE_CONTENT);
    lv_obj_set_style_pad_top(ui_row, 6, 0);
    lv_obj_set_flex_flow(ui_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(ui_row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(ui_row, 12, 0);
    const char *ui_names[2] = {"经典脸", "蝴蝶"};
    for (int i = 0; i < 2; i++) {
        lv_obj_t *b = lv_button_create(ui_row);
        lv_obj_set_size(b, 84, 38);
        lv_obj_set_ext_click_area(b, 6);
        lv_obj_set_style_bg_color(
            b, i == (int)s_ui_style ? lv_color_hex(0x1E9E6A) : lv_color_hex(0x3A4150), 0);
        lv_obj_set_style_radius(b, 8, 0);
        lv_obj_set_style_shadow_width(b, 0, 0);
        lv_obj_set_style_shadow_opa(b, LV_OPA_TRANSP, 0);
        lv_obj_add_event_cb(b, ui_style_btn_cb, LV_EVENT_CLICKED, (void *)(intptr_t)i);
        lv_obj_t *l = lv_label_create(b);
        lv_obj_set_style_text_font(l, &lv_font_cjk_sc_16, 0);
        lv_label_set_text(l, ui_names[i]);
        lv_obj_center(l);
    }

    s_lbl_vol = lv_label_create(card);
    lv_obj_set_style_text_font(s_lbl_vol, &lv_font_cjk_sc_16, 0);
    lv_obj_set_style_text_color(s_lbl_vol, lv_color_hex(0xC8D0DC), 0);
    lv_obj_set_style_pad_top(s_lbl_vol, 8, 0);

    s_slider_vol = lv_slider_create(card);
    lv_obj_set_width(s_slider_vol, LCD_H_RES - 72);
    lv_slider_set_range(s_slider_vol, 0, 100);
    lv_slider_set_value(s_slider_vol, voice_es8311_get_volume(), LV_ANIM_OFF);
    lv_obj_add_event_cb(s_slider_vol, vol_slider_cb, LV_EVENT_VALUE_CHANGED, NULL);
    {
        char buf[24];
        snprintf(buf, sizeof(buf), "音量 %d", voice_es8311_get_volume());
        lv_label_set_text(s_lbl_vol, buf);
    }

    s_lbl_net = lv_label_create(card);
    lv_obj_set_style_text_font(s_lbl_net, &lv_font_cjk_sc_16, 0);
    lv_obj_set_style_text_color(s_lbl_net, lv_color_hex(0xC8D0DC), 0);
    lv_obj_set_style_pad_top(s_lbl_net, 6, 0);
    menu_refresh_net();

    /* Wi-Fi presets: built-in network toggles for quick field testing. */
    lv_obj_t *wifi_title = lv_label_create(card);
    lv_obj_set_style_text_font(wifi_title, &lv_font_cjk_sc_16, 0);
    lv_obj_set_style_text_color(wifi_title, lv_color_hex(0xC8D0DC), 0);
    lv_obj_set_style_pad_top(wifi_title, 8, 0);
    lv_label_set_text(wifi_title, "WiFi");

    lv_obj_t *wifi_row = lv_obj_create(card);
    lv_obj_remove_style_all(wifi_row);
    lv_obj_set_size(wifi_row, LCD_H_RES - 56, LV_SIZE_CONTENT);
    lv_obj_set_style_pad_top(wifi_row, 4, 0);
    lv_obj_set_flex_flow(wifi_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(wifi_row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(wifi_row, 12, 0);
    int cur_wifi = voice_wifi_get_preset();
    for (int i = 0; i < 2; i++) {
        lv_obj_t *b = lv_button_create(wifi_row);
        lv_obj_set_size(b, 96, 38);
        lv_obj_set_ext_click_area(b, 6);
        lv_obj_set_style_bg_color(
            b, i == cur_wifi ? lv_color_hex(0x1E9E6A) : lv_color_hex(0x3A4150), 0);
        lv_obj_set_style_radius(b, 8, 0);
        lv_obj_set_style_shadow_width(b, 0, 0);
        lv_obj_set_style_shadow_opa(b, LV_OPA_TRANSP, 0);
        lv_obj_add_event_cb(b, wifi_preset_btn_cb, LV_EVENT_CLICKED, (void *)(intptr_t)i);
        lv_obj_t *l = lv_label_create(b);
        lv_obj_set_style_text_font(l, &lv_font_cjk_sc_16, 0);
        const char *ssid = voice_wifi_preset_ssid(i);
        lv_label_set_text(l, ssid[0] != '\0' ? ssid : (i == 0 ? "主网络" : "备用网络"));
        lv_obj_center(l);
    }

    /* Backend environment switch: 本地 (LAN dev server) / 远端 (cloud ECS).
     * Section title + two clearly-labelled buttons; the active one is green. */
    lv_obj_t *env_title = lv_label_create(card);
    lv_obj_set_style_text_font(env_title, &lv_font_cjk_sc_16, 0);
    lv_obj_set_style_text_color(env_title, lv_color_hex(0xC8D0DC), 0);
    lv_obj_set_style_pad_top(env_title, 8, 0);
    lv_label_set_text(env_title,
                      voice_ws_get_env() == 1 ? "服务器（当前：远端）" : "服务器（当前：本地）");

    lv_obj_t *env_row = lv_obj_create(card);
    lv_obj_remove_style_all(env_row);
    lv_obj_set_size(env_row, LCD_H_RES - 56, LV_SIZE_CONTENT);
    lv_obj_set_style_pad_top(env_row, 6, 0);
    lv_obj_set_flex_flow(env_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(env_row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(env_row, 12, 0);
    int cur_env = voice_ws_get_env();
    const char *env_names[2] = {"本地", "远端"};
    for (int i = 0; i < 2; i++) {
        lv_obj_t *b = lv_button_create(env_row);
        lv_obj_set_size(b, 84, 38);
        lv_obj_set_ext_click_area(b, 6);
        lv_obj_set_style_bg_color(
            b, i == cur_env ? lv_color_hex(0x1E9E6A) : lv_color_hex(0x3A4150), 0);
        lv_obj_set_style_radius(b, 8, 0);
        lv_obj_set_style_shadow_width(b, 0, 0);
        lv_obj_set_style_shadow_opa(b, LV_OPA_TRANSP, 0);
        lv_obj_add_event_cb(b, env_btn_cb, LV_EVENT_CLICKED, (void *)(intptr_t)i);
        lv_obj_t *l = lv_label_create(b);
        lv_obj_set_style_text_font(l, &lv_font_cjk_sc_16, 0);
        lv_label_set_text(l, env_names[i]);
        lv_obj_center(l);
    }

}

/* --- Character tracing overlay ----------------------------------------- */

static int32_t trace_map_x(int16_t x) {
    if (x < 0) {
        x = 0;
    } else if (x > 1000) {
        x = 1000;
    }
    return (x * TRACE_GRID_SIZE) / 1000;
}

static int32_t trace_map_y(int16_t y) {
    if (y < 0) {
        y = 0;
    } else if (y > 1000) {
        y = 1000;
    }
    return (y * TRACE_GRID_SIZE) / 1000;
}

static lv_obj_t *trace_create_line(lv_obj_t *parent,
                                   lv_point_precise_t points[2],
                                   lv_color_t color,
                                   int width) {
    lv_obj_t *line = lv_line_create(parent);
    lv_line_set_points(line, points, 2);
    lv_obj_set_style_line_color(line, color, 0);
    lv_obj_set_style_line_width(line, width, 0);
    lv_obj_set_style_line_rounded(line, true, 0);
    return line;
}

static void trace_add_dot(lv_obj_t *parent,
                          int32_t center_x,
                          int32_t center_y,
                          uint16_t size,
                          lv_color_t color,
                          lv_opa_t opa) {
    lv_obj_t *dot = lv_obj_create(parent);
    lv_obj_remove_style_all(dot);
    lv_obj_set_size(dot, size, size);
    lv_obj_set_pos(dot, center_x - (int32_t)size / 2, center_y - (int32_t)size / 2);
    lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(dot, color, 0);
    lv_obj_set_style_bg_opa(dot, opa, 0);
    lv_obj_clear_flag(dot, LV_OBJ_FLAG_SCROLLABLE);
}

static void trace_add_segment_cap(const lv_point_precise_t *point) {
    if (!s_trace_lines) {
        return;
    }
    trace_add_dot(s_trace_lines, point->x, point->y, TRACE_STROKE_CAP_SIZE,
                  lv_color_hex(TRACE_STROKE_COLOR), LV_OPA_COVER);
}

static void trace_add_stroke_joint(const lv_point_precise_t *point) {
    if (!s_trace_lines) {
        return;
    }
    trace_add_dot(s_trace_lines, point->x, point->y, TRACE_STROKE_JOINT_SIZE,
                  lv_color_hex(TRACE_STROKE_COLOR), LV_OPA_80);
}

static size_t trace_total_segments(void) {
    size_t total = 0;
    for (size_t i = 0; i < s_trace_stroke_count; i++) {
        uint8_t point_count = s_trace_strokes[i].point_count;
        if (point_count >= 2) {
            total += (size_t)point_count - 1;
        }
    }
    return total;
}

static size_t trace_outline_total_points(const voice_trace_outline_t *outlines,
                                         size_t stroke_count) {
    size_t total = 0;
    if (!outlines) {
        return 0;
    }
    for (size_t i = 0; i < stroke_count; i++) {
        total += outlines[i].point_count;
    }
    return total;
}

static void trace_overlay_close(void) {
    if (s_trace_timer) {
        lv_timer_del(s_trace_timer);
        s_trace_timer = NULL;
    }
    if (s_trace_overlay) {
        lv_obj_del(s_trace_overlay);  // deletes the canvas children too
        s_trace_overlay = NULL;
        s_trace_lines = NULL;
        s_trace_canvas = NULL;
    }
    if (s_trace_canvas_buf) {  // freed after the canvas obj is gone
        heap_caps_free(s_trace_canvas_buf);
        s_trace_canvas_buf = NULL;
    }
    s_trace_canvas_stride = 0;
    if (s_trace_outlines) {
        heap_caps_free(s_trace_outlines);
        s_trace_outlines = NULL;
    }
    trace_overlay_reset_state();
}

static void color_test_touch_cb(lv_event_t *event) {
    (void)event;
    color_test_overlay_close();
}

static void color_test_add_label(lv_obj_t *parent, const char *text,
                                 lv_color_t text_color) {
    lv_obj_t *label = lv_label_create(parent);
    lv_obj_set_style_text_font(label, &lv_font_cjk_sc_16, 0);
    lv_obj_set_style_text_color(label, text_color, 0);
    lv_label_set_text(label, text);
    lv_obj_align(label, LV_ALIGN_BOTTOM_MID, 0, -10);
}

static lv_color_t color_test_text_color(uint32_t bg) {
    return bg == 0xFFFFFF ? lv_color_hex(0x000000) : lv_color_hex(0xFFFFFF);
}

static void color_test_overlay_open(const char *color) {
    trace_overlay_close();
    color_test_overlay_close();
    idiom_overlay_close();
    menu_close();

    lv_obj_t *scr = lv_screen_active();
    s_color_test_overlay = lv_obj_create(scr);
    lv_obj_remove_style_all(s_color_test_overlay);
    lv_obj_set_size(s_color_test_overlay, LCD_H_RES, LCD_V_RES);
    lv_obj_clear_flag(s_color_test_overlay, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(s_color_test_overlay, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(s_color_test_overlay, color_test_touch_cb, LV_EVENT_CLICKED, NULL);

    const char *name = color && color[0] ? color : "bars";
    if (strcmp(name, "red") == 0 || strcmp(name, "green") == 0 ||
        strcmp(name, "blue") == 0 || strcmp(name, "black") == 0 ||
        strcmp(name, "white") == 0) {
        uint32_t bg = 0x000000;
        if (strcmp(name, "red") == 0) {
            bg = 0xFF0000;
        } else if (strcmp(name, "green") == 0) {
            bg = 0x00FF00;
        } else if (strcmp(name, "blue") == 0) {
            bg = 0x0000FF;
        } else if (strcmp(name, "white") == 0) {
            bg = 0xFFFFFF;
        }
        lv_obj_set_style_bg_color(s_color_test_overlay, lv_color_hex(bg), 0);
        lv_obj_set_style_bg_opa(s_color_test_overlay, LV_OPA_COVER, 0);
        color_test_add_label(s_color_test_overlay, name, color_test_text_color(bg));
        return;
    }

    lv_obj_set_style_bg_color(s_color_test_overlay, lv_color_hex(0x000000), 0);
    lv_obj_set_style_bg_opa(s_color_test_overlay, LV_OPA_COVER, 0);
    static const struct {
        const char *name;
        uint32_t color;
    } bars[] = {
        {"red", 0xFF0000},
        {"green", 0x00FF00},
        {"blue", 0x0000FF},
        {"black", 0x000000},
        {"white", 0xFFFFFF},
    };
    const int bar_h = LCD_V_RES / (int)(sizeof(bars) / sizeof(bars[0]));
    for (size_t i = 0; i < sizeof(bars) / sizeof(bars[0]); i++) {
        lv_obj_t *bar = lv_obj_create(s_color_test_overlay);
        lv_obj_remove_style_all(bar);
        lv_obj_set_size(bar, LCD_H_RES, i + 1 == sizeof(bars) / sizeof(bars[0])
                                       ? LCD_V_RES - (int)i * bar_h
                                       : bar_h);
        lv_obj_set_pos(bar, 0, (int)i * bar_h);
        lv_obj_set_style_bg_color(bar, lv_color_hex(bars[i].color), 0);
        lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, 0);
        lv_obj_clear_flag(bar, LV_OBJ_FLAG_SCROLLABLE);

        lv_obj_t *label = lv_label_create(bar);
        lv_obj_set_style_text_font(label, &lv_font_cjk_sc_16, 0);
        lv_obj_set_style_text_color(label, color_test_text_color(bars[i].color), 0);
        lv_label_set_text(label, bars[i].name);
        lv_obj_align(label, LV_ALIGN_CENTER, 0, 0);
    }
}

/* Even-odd scanline fill of one stroke's 楷体 polygon into an A8 mask buffer.
 * Points are 0..1000 device space mapped to canvas pixels. When `clip` is set
 * only pixels whose projection onto (dx,dy) is <= `thr` are written — this is
 * the along-median sweep that reveals a stroke like brush writing. */
static void trace_fill_outline_into(uint8_t *buf, const voice_trace_outline_t *poly,
                                    uint8_t alpha, bool clip,
                                    float dx, float dy, float thr) {
    if (!buf || !poly) {
        return;
    }
    const int n = poly->point_count;
    if (n < 3) {
        return;
    }
    float fx[VOICE_TRACE_OUTLINE_MAX_POINTS];
    float fy[VOICE_TRACE_OUTLINE_MAX_POINTS];
    const float scale = (float)TRACE_CANVAS_PX / 1000.0f;
    float ymin = (float)TRACE_CANVAS_PX;
    float ymax = 0.0f;
    for (int i = 0; i < n; i++) {
        fx[i] = (float)poly->points[i].x * scale;
        fy[i] = (float)poly->points[i].y * scale;
        if (fy[i] < ymin) {
            ymin = fy[i];
        }
        if (fy[i] > ymax) {
            ymax = fy[i];
        }
    }
    int y0i = (int)ymin;
    int y1i = (int)ymax + 1;
    if (y0i < 0) {
        y0i = 0;
    }
    if (y1i > TRACE_CANVAS_PX) {
        y1i = TRACE_CANVAS_PX;
    }
    for (int y = y0i; y < y1i; y++) {
        const float yc = (float)y + 0.5f;
        float xs[VOICE_TRACE_OUTLINE_MAX_POINTS];
        int cnt = 0;
        for (int i = 0; i < n; i++) {
            const int j = (i + 1) % n;
            const float ya = fy[i];
            const float yb = fy[j];
            if ((ya <= yc) == (yb <= yc)) {
                continue;  // edge does not cross this scanline
            }
            const float t = (yc - ya) / (yb - ya);
            xs[cnt++] = fx[i] + t * (fx[j] - fx[i]);
        }
        for (int a = 1; a < cnt; a++) {  // insertion sort the (small) crossing list
            float key = xs[a];
            int b = a - 1;
            while (b >= 0 && xs[b] > key) {
                xs[b + 1] = xs[b];
                b--;
            }
            xs[b + 1] = key;
        }
        uint8_t *row = buf + (size_t)y * s_trace_canvas_stride;
        for (int k = 0; k + 1 < cnt; k += 2) {
            int xa = (int)(xs[k] + 0.5f);
            int xb = (int)(xs[k + 1] + 0.5f);
            if (xa < 0) {
                xa = 0;
            }
            if (xb > TRACE_CANVAS_PX) {
                xb = TRACE_CANVAS_PX;
            }
            for (int x = xa; x < xb; x++) {
                if (clip && ((float)x * dx + (float)y * dy) > thr) {
                    continue;  // not yet swept past this pixel
                }
                row[x] = alpha;
            }
        }
    }
}

/* Reveal the current stroke's ink up to sweep fraction `frac` (0..1) along its
 * median direction. At frac>=1 the whole stroke is filled (caps included). */
static void trace_canvas_reveal_stroke(size_t stroke_index, float frac) {
    if (!s_trace_canvas_buf || !s_trace_outlines ||
        stroke_index >= s_trace_stroke_count) {
        return;
    }
    const voice_trace_outline_t *poly = &s_trace_outlines[stroke_index];
    const voice_trace_stroke_t *median = &s_trace_strokes[stroke_index];
    const float scale = (float)TRACE_CANVAS_PX / 1000.0f;
    bool clip = frac < 1.0f;
    float dx = 0.0f;
    float dy = 0.0f;
    float thr = 0.0f;
    if (clip && median->point_count >= 2) {
        float sx = (float)median->points[0].x * scale;
        float sy = (float)median->points[0].y * scale;
        float ex = (float)median->points[median->point_count - 1].x * scale;
        float ey = (float)median->points[median->point_count - 1].y * scale;
        float ddx = ex - sx;
        float ddy = ey - sy;
        float len = sqrtf(ddx * ddx + ddy * ddy);
        if (len < 1.0f) {
            clip = false;  // degenerate median → just fill the whole stroke
        } else {
            dx = ddx / len;
            dy = ddy / len;
            float p_start = sx * dx + sy * dy;
            float p_end = ex * dx + ey * dy;
            thr = p_start + frac * (p_end - p_start);
        }
    } else {
        clip = false;
    }
    trace_fill_outline_into(s_trace_canvas_buf, poly, 0xFF, clip, dx, dy, thr);
    if (s_trace_canvas) {
        lv_obj_invalidate(s_trace_canvas);
    }
}

static void trace_timer_cb(lv_timer_t *timer);

/* Restart the writing animation from scratch without freeing the stroke/outline
 * data (used by the "tap once to replay" gesture). Keeps the already-computed
 * tick interval. */
static void trace_replay(void) {
    if (s_trace_timer) {
        lv_timer_del(s_trace_timer);
        s_trace_timer = NULL;
    }
    s_trace_current_stroke = 0;
    s_trace_current_segment = 0;
    s_trace_kaiti_step = 0;
    s_trace_completed = false;

    if (s_trace_outlines && s_trace_canvas_buf) {
        // Clear the ink mask so strokes redraw from blank.
        memset(s_trace_canvas_buf,
               0, (size_t)s_trace_canvas_stride * TRACE_CANVAS_PX);
        if (s_trace_canvas) {
            lv_obj_invalidate(s_trace_canvas);
        }
    } else if (s_trace_lines) {
        // Skeleton fallback: drop the previously drawn line segments.
        lv_obj_clean(s_trace_lines);
    }

    if (s_trace_stroke_count == 0) {
        s_trace_completed = true;
        s_trace_first_done = true;
        return;
    }
    uint32_t tick = s_trace_tick_ms ? s_trace_tick_ms : TRACE_KAITI_MIN_TICK_MS;
    s_trace_timer = lv_timer_create(trace_timer_cb, tick, NULL);
}

static void trace_touch_cb(lv_event_t *event) {
    (void)event;
    // Ignore taps until the very first write has completed at least once.
    if (!s_trace_first_done) {
        return;
    }
    // A quick second tap (within TRACE_DOUBLE_TAP_MS of the previous one) exits;
    // a lone tap replays the animation. The first tap of a pair always replays
    // immediately for instant feedback, and the following tap (if quick) exits —
    // so single-tap = replay, fast double-tap = exit.
    uint32_t now = lv_tick_get();
    if (s_trace_last_tap_ms != 0 &&
        (now - s_trace_last_tap_ms) <= TRACE_DOUBLE_TAP_MS) {
        s_trace_last_tap_ms = 0;
        trace_overlay_close();
        return;
    }
    s_trace_last_tap_ms = now;
    trace_replay();
}

static void trace_add_segment(size_t stroke_index, uint8_t segment_index) {
    if (!s_trace_lines || stroke_index >= s_trace_stroke_count) {
        return;
    }
    const voice_trace_stroke_t *stroke = &s_trace_strokes[stroke_index];
    if (segment_index + 1 >= stroke->point_count) {
        return;
    }
    lv_point_precise_t *points = s_trace_line_points[stroke_index][segment_index];
    points[0].x = trace_map_x(stroke->points[segment_index].x);
    points[0].y = trace_map_y(stroke->points[segment_index].y);
    points[1].x = trace_map_x(stroke->points[segment_index + 1].x);
    points[1].y = trace_map_y(stroke->points[segment_index + 1].y);
    trace_create_line(s_trace_lines, points, lv_color_hex(TRACE_STROKE_COLOR),
                      TRACE_STROKE_MAIN_WIDTH);
    if (segment_index == 0) {
        trace_add_segment_cap(&points[0]);
    } else {
        trace_add_stroke_joint(&points[0]);
    }
    if (segment_index + 2 >= stroke->point_count) {
        trace_add_segment_cap(&points[1]);
    }
}

static void trace_timer_cb(lv_timer_t *timer) {
    (void)timer;
    if (s_trace_completed || s_trace_stroke_count == 0) {
        return;
    }
    if (s_trace_outlines) {
        // 真正正楷: sweep the current stroke along its median over several
        // sub-steps (brush writing), then advance to the next stroke.
        s_trace_kaiti_step++;
        float frac = (float)s_trace_kaiti_step / (float)TRACE_KAITI_SUBSTEPS;
        if (frac > 1.0f) {
            frac = 1.0f;
        }
        trace_canvas_reveal_stroke(s_trace_current_stroke, frac);
        if (s_trace_kaiti_step >= TRACE_KAITI_SUBSTEPS) {
            s_trace_kaiti_step = 0;
            s_trace_current_stroke++;
        }
        if (s_trace_current_stroke >= s_trace_stroke_count) {
            s_trace_completed = true;
            s_trace_first_done = true;
            if (s_trace_timer) {
                lv_timer_del(s_trace_timer);
                s_trace_timer = NULL;
            }
        }
        return;
    }
    const voice_trace_stroke_t *stroke = &s_trace_strokes[s_trace_current_stroke];
    trace_add_segment(s_trace_current_stroke, s_trace_current_segment);
    s_trace_current_segment++;
    if (s_trace_current_segment + 1 >= stroke->point_count) {
        s_trace_current_stroke++;
        s_trace_current_segment = 0;
    }
    if (s_trace_current_stroke >= s_trace_stroke_count) {
        s_trace_completed = true;
        s_trace_first_done = true;
        if (s_trace_timer) {
            lv_timer_del(s_trace_timer);
            s_trace_timer = NULL;
        }
    }
}

static void trace_overlay_open(void) {
    // NOTE: the caller (voice_display_lcd_show_trace) has already closed any prior
    // overlay and set s_trace_strokes/s_trace_outlines. Do NOT reset state here —
    // trace_overlay_close() clears the strokes we are about to render.
    color_test_overlay_close();
    idiom_overlay_close();
    menu_close();

    lv_obj_t *scr = lv_screen_active();
    s_trace_overlay = lv_obj_create(scr);
    lv_obj_remove_style_all(s_trace_overlay);
    lv_obj_set_size(s_trace_overlay, LCD_H_RES, LCD_V_RES);
    lv_obj_set_style_bg_color(s_trace_overlay, lv_color_hex(TRACE_BG_COLOR), 0);
    lv_obj_set_style_bg_opa(s_trace_overlay, LV_OPA_COVER, 0);
    lv_obj_clear_flag(s_trace_overlay, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(s_trace_overlay, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(s_trace_overlay, trace_touch_cb, LV_EVENT_CLICKED, NULL);

    lv_obj_t *grid = lv_obj_create(s_trace_overlay);
    lv_obj_remove_style_all(grid);
    lv_obj_set_size(grid, TRACE_GRID_SIZE, TRACE_GRID_SIZE);
    lv_obj_set_pos(grid, TRACE_GRID_X, TRACE_GRID_Y);
    lv_obj_set_style_border_width(grid, 3, 0);
    lv_obj_set_style_border_color(grid, lv_color_hex(TRACE_GRID_COLOR), 0);
    lv_obj_set_style_bg_opa(grid, LV_OPA_TRANSP, 0);
    lv_obj_clear_flag(grid, LV_OBJ_FLAG_SCROLLABLE);
    // The grid covers the center where the user naturally taps; keep it
    // non-clickable so taps fall through to the full-screen overlay handler
    // (LVGL hit-tests the top-most *clickable* object — a clickable grid would
    // otherwise swallow center taps and make tap-to-replay/exit feel dead).
    lv_obj_clear_flag(grid, LV_OBJ_FLAG_CLICKABLE);

    s_trace_grid_points[0][0] = (lv_point_precise_t){TRACE_GRID_SIZE / 2, 0};
    s_trace_grid_points[0][1] = (lv_point_precise_t){TRACE_GRID_SIZE / 2, TRACE_GRID_SIZE};
    s_trace_grid_points[1][0] = (lv_point_precise_t){0, TRACE_GRID_SIZE / 2};
    s_trace_grid_points[1][1] = (lv_point_precise_t){TRACE_GRID_SIZE, TRACE_GRID_SIZE / 2};
    s_trace_grid_points[2][0] = (lv_point_precise_t){0, 0};
    s_trace_grid_points[2][1] = (lv_point_precise_t){TRACE_GRID_SIZE, TRACE_GRID_SIZE};
    s_trace_grid_points[3][0] = (lv_point_precise_t){TRACE_GRID_SIZE, 0};
    s_trace_grid_points[3][1] = (lv_point_precise_t){0, TRACE_GRID_SIZE};
    for (int i = 0; i < 4; i++) {
        trace_create_line(grid, s_trace_grid_points[i],
                          lv_color_hex(TRACE_GRID_COLOR), 2);
    }

    // 真正正楷: when outlines are available, render filled 楷体 strokes into an
    // A8 ink-mask canvas (white recolor). Falls back to the median skeleton if
    // the mask buffer can't be allocated.
    if (s_trace_outlines) {
        s_trace_canvas_stride =
            lv_draw_buf_width_to_stride(TRACE_CANVAS_PX, LV_COLOR_FORMAT_A8);
        size_t buf_sz = (size_t)s_trace_canvas_stride * TRACE_CANVAS_PX;
        s_trace_canvas_buf =
            heap_caps_malloc(buf_sz, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!s_trace_canvas_buf) {
            s_trace_canvas_buf = heap_caps_malloc(buf_sz, MALLOC_CAP_8BIT);
        }
        if (s_trace_canvas_buf) {
            memset(s_trace_canvas_buf, 0, buf_sz);

            s_trace_canvas = lv_canvas_create(s_trace_overlay);  // white ink, on top
            lv_canvas_set_buffer(s_trace_canvas, s_trace_canvas_buf, TRACE_CANVAS_PX,
                                 TRACE_CANVAS_PX, LV_COLOR_FORMAT_A8);
            lv_obj_set_pos(s_trace_canvas, TRACE_GRID_X, TRACE_GRID_Y);
            lv_obj_set_style_image_recolor(s_trace_canvas,
                                           lv_color_hex(TRACE_STROKE_COLOR), 0);
            lv_obj_set_style_image_recolor_opa(s_trace_canvas, LV_OPA_COVER, 0);
            lv_obj_clear_flag(s_trace_canvas, LV_OBJ_FLAG_SCROLLABLE);
            lv_obj_clear_flag(s_trace_canvas, LV_OBJ_FLAG_CLICKABLE);
        } else {
            ESP_LOGW(TAG, "trace canvas alloc failed (%u B); skeleton fallback",
                     (unsigned)buf_sz);
            heap_caps_free(s_trace_outlines);
            s_trace_outlines = NULL;
        }
    }

    if (s_trace_outlines) {
        if (s_trace_stroke_count == 0) {
            s_trace_completed = true;
            s_trace_first_done = true;
            return;
        }
        size_t total_ticks = s_trace_stroke_count * TRACE_KAITI_SUBSTEPS;
        s_trace_tick_ms = s_trace_duration_ms / total_ticks;
        if (s_trace_tick_ms < TRACE_KAITI_MIN_TICK_MS) {
            s_trace_tick_ms = TRACE_KAITI_MIN_TICK_MS;
        } else if (s_trace_tick_ms > TRACE_KAITI_MAX_TICK_MS) {
            s_trace_tick_ms = TRACE_KAITI_MAX_TICK_MS;
        }
        s_trace_timer = lv_timer_create(trace_timer_cb, s_trace_tick_ms, NULL);
        return;
    }

    s_trace_lines = lv_obj_create(s_trace_overlay);
    lv_obj_remove_style_all(s_trace_lines);
    lv_obj_set_size(s_trace_lines, TRACE_GRID_SIZE, TRACE_GRID_SIZE);
    lv_obj_set_pos(s_trace_lines, TRACE_GRID_X, TRACE_GRID_Y);
    lv_obj_set_style_bg_opa(s_trace_lines, LV_OPA_TRANSP, 0);
    lv_obj_clear_flag(s_trace_lines, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_clear_flag(s_trace_lines, LV_OBJ_FLAG_CLICKABLE);

    size_t total_segments = trace_total_segments();
    if (total_segments == 0) {
        s_trace_completed = true;
        s_trace_first_done = true;
        return;
    }
    s_trace_tick_ms = s_trace_duration_ms / total_segments;
    if (s_trace_tick_ms < TRACE_MIN_TICK_MS) {
        s_trace_tick_ms = TRACE_MIN_TICK_MS;
    } else if (s_trace_tick_ms > TRACE_MAX_TICK_MS) {
        s_trace_tick_ms = TRACE_MAX_TICK_MS;
    }
    s_trace_timer = lv_timer_create(trace_timer_cb, s_trace_tick_ms, NULL);
}

static void build_widgets_face(lv_obj_t *scr) {
    s_img_eyes = lv_image_create(scr);
    lv_image_set_src(s_img_eyes, &face_eyes_neutral);
    lv_obj_align(s_img_eyes, LV_ALIGN_TOP_MID, 0, FACE_EYES_Y);

    s_img_mouth = lv_image_create(scr);
    lv_image_set_src(s_img_mouth, &face_mouth_smile_small);
    lv_obj_align(s_img_mouth, LV_ALIGN_TOP_MID, 0, FACE_MOUTH_Y);
}

static void build_widgets_butterfly(lv_obj_t *scr) {
    for (int i = 0; i < SPARKLE_MAX; i++) {
        s_sparkle_objs[i] = lv_obj_create(scr);
        lv_obj_remove_style_all(s_sparkle_objs[i]);
        int sz = 3 + sparkle_rand16() % 3;
        lv_obj_set_size(s_sparkle_objs[i], sz, sz);
        lv_obj_set_style_radius(s_sparkle_objs[i], LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_opa(s_sparkle_objs[i], LV_OPA_TRANSP, 0);
        lv_obj_add_flag(s_sparkle_objs[i], LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_sparkle_objs[i], LV_OBJ_FLAG_CLICKABLE);
        s_sparkle[i].life = 0;
    }

    s_img_bf_wing = lv_image_create(scr);
    lv_image_set_src(s_img_bf_wing, &butterfly_wing_open);
    lv_obj_set_pos(s_img_bf_wing, BF_WING_X, BF_WING_Y);
    lv_obj_set_style_image_recolor(s_img_bf_wing, lv_color_hsv_to_rgb(s_bf_hue, 92, 100), 0);
    lv_obj_set_style_image_recolor_opa(s_img_bf_wing, LV_OPA_COVER, 0);

    s_img_bf_body = lv_image_create(scr);
    lv_image_set_src(s_img_bf_body, &butterfly_body_base);
    lv_obj_set_pos(s_img_bf_body, BF_BODY_X, BF_BODY_Y);
    lv_image_set_pivot(s_img_bf_body, BUTTERFLY_BODY_W / 2, BUTTERFLY_BODY_H / 2);
    lv_obj_set_style_image_recolor(s_img_bf_body, lv_color_hsv_to_rgb((s_bf_hue + 50u) % 360u, 65, 95), 0);
    lv_obj_set_style_image_recolor_opa(s_img_bf_body, LV_OPA_COVER, 0);

    s_img_bf_face = lv_image_create(scr);
    lv_image_set_src(s_img_bf_face, &butterfly_face_idle);
    lv_image_set_pivot(s_img_bf_face, BUTTERFLY_FACE_W / 2, BUTTERFLY_FACE_H / 2);
    lv_obj_set_pos(s_img_bf_face, BF_FACE_X, BF_FACE_Y);
}

static void build_widgets_common(lv_obj_t *scr) {
    lv_obj_set_style_bg_color(scr, lv_color_hex(0x000000), 0);
    lv_obj_set_style_text_color(scr, lv_color_hex(0xF2F2F2), 0);
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

    /* Transparent tap layer over the main character area: tap to toggle the
     * conversation (sprites are decorative, this catches the touch). */
    s_face_hit = lv_obj_create(scr);
    lv_obj_remove_style_all(s_face_hit);
    lv_obj_set_size(s_face_hit, LCD_H_RES, FACE_HIT_H);
    lv_obj_align(s_face_hit, LV_ALIGN_TOP_MID, 0, FACE_HIT_Y);
    lv_obj_set_style_bg_opa(s_face_hit, LV_OPA_TRANSP, 0);
    lv_obj_add_flag(s_face_hit, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(s_face_hit, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_face_hit, main_button_cb, LV_EVENT_CLICKED, NULL);

    /* Top status capsule: semantic dot + concise Chinese state. It stays above
     * the animated character without covering the hidden settings pull zone. */
    s_status_pill = lv_obj_create(scr);
    lv_obj_remove_style_all(s_status_pill);
    lv_obj_set_size(s_status_pill, 156, 30);
    lv_obj_align(s_status_pill, LV_ALIGN_TOP_MID, 0, 3);
    lv_obj_set_style_radius(s_status_pill, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(s_status_pill, lv_color_hex(0x111722), 0);
    lv_obj_set_style_bg_opa(s_status_pill, LV_OPA_90, 0);
    lv_obj_set_style_border_width(s_status_pill, 1, 0);
    lv_obj_set_style_border_color(s_status_pill, lv_color_hex(0x3A4A60), 0);
    lv_obj_set_style_shadow_width(s_status_pill, 0, 0);
    lv_obj_clear_flag(s_status_pill, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(s_status_pill, LV_OBJ_FLAG_SCROLLABLE);

    s_status_dot = lv_obj_create(scr);
    lv_obj_remove_style_all(s_status_dot);
    lv_obj_set_size(s_status_dot, 8, 8);
    lv_obj_align(s_status_dot, LV_ALIGN_TOP_MID, -61, 14);
    lv_obj_set_style_radius(s_status_dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(s_status_dot, lv_color_hex(0x3A4A60), 0);
    lv_obj_set_style_bg_opa(s_status_dot, LV_OPA_COVER, 0);
    lv_obj_clear_flag(s_status_dot, LV_OBJ_FLAG_CLICKABLE);

    /* Top bar (center): current voice-interaction status in Chinese. */
    s_lbl_topbar = lv_label_create(scr);
    lv_obj_set_style_text_font(s_lbl_topbar, &lv_font_cjk_sc_16, 0);
    lv_obj_set_style_text_color(s_lbl_topbar, lv_color_hex(0xDCE2EA), 0);
    lv_obj_set_width(s_lbl_topbar, 126);
    lv_obj_set_style_text_align(s_lbl_topbar, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_lbl_topbar, LV_ALIGN_TOP_MID, 9, 8);
    lv_label_set_text(s_lbl_topbar, "启动中");

    /* Hidden top-right hotspot for settings. No visible button is drawn. */
    s_pull_zone = lv_obj_create(scr);
    lv_obj_remove_style_all(s_pull_zone);
    lv_obj_set_size(s_pull_zone, LCD_H_RES, MENU_PULL_ZONE_H);
    lv_obj_align(s_pull_zone, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_set_style_bg_opa(s_pull_zone, LV_OPA_TRANSP, 0);
    lv_obj_add_flag(s_pull_zone, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(s_pull_zone, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(s_pull_zone, menu_pull_zone_press_cb, LV_EVENT_ALL, NULL);

    /* Detail line for conversation/error hints. Network details live in the
     * settings panel, so Wi-Fi/IP states do not occupy the main screen. */
    s_lbl_detail = lv_label_create(scr);
    lv_obj_set_style_text_font(s_lbl_detail, &lv_font_cjk_sc_16, 0);
    lv_obj_set_style_text_color(s_lbl_detail, lv_color_hex(0x46505A), 0);
    lv_obj_set_width(s_lbl_detail, LCD_H_RES - 12);
    lv_obj_set_style_text_align(s_lbl_detail, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s_lbl_detail, LV_LABEL_LONG_DOT);
    lv_obj_align(s_lbl_detail, LV_ALIGN_BOTTOM_MID, 0, -55);
    lv_label_set_text(s_lbl_detail, "");

    /* Subtitle card separates transcript from the animated character. Hidden
     * when empty so idle remains visually quiet. */
    s_subtitle_card = lv_obj_create(scr);
    lv_obj_remove_style_all(s_subtitle_card);
    lv_obj_set_size(s_subtitle_card, LCD_H_RES - 14, 43);
    lv_obj_align(s_subtitle_card, LV_ALIGN_BOTTOM_MID, 0, -3);
    lv_obj_set_style_radius(s_subtitle_card, 12, 0);
    lv_obj_set_style_bg_color(s_subtitle_card, lv_color_hex(0x101620), 0);
    lv_obj_set_style_bg_opa(s_subtitle_card, LV_OPA_90, 0);
    lv_obj_set_style_border_width(s_subtitle_card, 1, 0);
    lv_obj_set_style_border_color(s_subtitle_card, lv_color_hex(0x263246), 0);
    lv_obj_set_style_shadow_width(s_subtitle_card, 0, 0);
    lv_obj_clear_flag(s_subtitle_card, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(s_subtitle_card, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(s_subtitle_card, LV_OBJ_FLAG_HIDDEN);

    /* Subtitle: voice recognition ("你：…") and bot reply ("我：…"). */
    s_lbl_subtitle = lv_label_create(scr);
    lv_obj_set_style_text_font(s_lbl_subtitle, &lv_font_cjk_sc_16, 0);
    lv_obj_set_style_text_color(s_lbl_subtitle, lv_color_hex(0xEAF0F6), 0);
    lv_obj_set_width(s_lbl_subtitle, LCD_H_RES - 30);
    lv_obj_set_style_text_align(s_lbl_subtitle, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_bg_opa(s_lbl_subtitle, LV_OPA_TRANSP, 0);
    lv_obj_set_style_shadow_width(s_lbl_subtitle, 0, 0);
    lv_obj_set_style_pad_all(s_lbl_subtitle, 0, 0);
    lv_label_set_long_mode(s_lbl_subtitle, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_align(s_lbl_subtitle, LV_ALIGN_BOTTOM_MID, 0, -10);
    lv_label_set_text(s_lbl_subtitle, "");
    lv_obj_add_flag(s_lbl_subtitle, LV_OBJ_FLAG_HIDDEN);
}

static void build_widgets(void) {
    lv_obj_t *scr = lv_screen_active();
    s_ui_style = ui_style_load();
    ESP_LOGI(TAG, "main UI style: %s", s_ui_style == UI_STYLE_BUTTERFLY ? "butterfly" : "classic-face");
    anim_reset_caches();
    s_img_eyes = NULL;
    s_img_mouth = NULL;
    s_img_bf_wing = NULL;
    s_img_bf_body = NULL;
    s_img_bf_face = NULL;
    memset(s_sparkle_objs, 0, sizeof(s_sparkle_objs));

    if (s_ui_style == UI_STYLE_BUTTERFLY) {
        build_widgets_butterfly(scr);
    } else {
        build_widgets_face(scr);
    }
    build_widgets_common(scr);

    /* Drive the main-character animation (face blink/talk or butterfly flap/hue) at ~11 Hz. */
    s_anim_timer = lv_timer_create(anim_timer_cb, 90, NULL);
}

static void common_widgets_to_front(void) {
    if (s_face_hit) {
        lv_obj_move_to_index(s_face_hit, -1);
    }
    if (s_status_pill) {
        lv_obj_move_to_index(s_status_pill, -1);
    }
    if (s_status_dot) {
        lv_obj_move_to_index(s_status_dot, -1);
    }
    if (s_lbl_topbar) {
        lv_obj_move_to_index(s_lbl_topbar, -1);
    }
    if (s_lbl_detail) {
        lv_obj_move_to_index(s_lbl_detail, -1);
    }
    if (s_subtitle_card) {
        lv_obj_move_to_index(s_subtitle_card, -1);
    }
    if (s_lbl_subtitle) {
        lv_obj_move_to_index(s_lbl_subtitle, -1);
    }
    if (s_pull_zone) {
        lv_obj_move_to_index(s_pull_zone, -1);
    }
}

static void ui_style_apply_ex(ui_style_t style, bool persist) {
    if (style != UI_STYLE_FACE && style != UI_STYLE_BUTTERFLY) {
        style = UI_STYLE_DEFAULT;
    }
    if (style == s_ui_style && (s_img_eyes || s_img_bf_wing)) {
        return;
    }

    if (persist) {
        ui_style_save(style);  // 用户在设置里选的默认风格才写 NVS；场景临时切换不写。
    }
    s_ui_style = style;
    anim_reset_caches();

    if (s_img_eyes) {
        lv_obj_del(s_img_eyes);
        s_img_eyes = NULL;
    }
    if (s_img_mouth) {
        lv_obj_del(s_img_mouth);
        s_img_mouth = NULL;
    }
    if (s_img_bf_wing) {
        lv_obj_del(s_img_bf_wing);
        s_img_bf_wing = NULL;
    }
    if (s_img_bf_body) {
        lv_obj_del(s_img_bf_body);
        s_img_bf_body = NULL;
    }
    if (s_img_bf_face) {
        lv_obj_del(s_img_bf_face);
        s_img_bf_face = NULL;
    }
    for (int i = 0; i < SPARKLE_MAX; i++) {
        if (s_sparkle_objs[i]) {
            lv_obj_del(s_sparkle_objs[i]);
            s_sparkle_objs[i] = NULL;
        }
    }

    lv_obj_t *scr = lv_screen_active();
    if (s_ui_style == UI_STYLE_BUTTERFLY) {
        build_widgets_butterfly(scr);
    } else {
        build_widgets_face(scr);
    }
    common_widgets_to_front();
}

static void ui_style_apply(ui_style_t style) {
    ui_style_apply_ex(style, true);
}

// 场景临时切「经典脸」：记住进入前的风格，切脸但不写 NVS。幂等。
void voice_display_lcd_scene_enter_face(void) {
    if (!s_ready) {
        return;
    }
    if (!lvgl_port_lock(100)) {
        ESP_LOGW(TAG, "LVGL busy; deferring scene enter");
        return;
    }
    if (!s_scene_face_active) {
        s_scene_face_active = true;
        s_scene_prev_style = s_ui_style;
    }
    if (s_ui_style != UI_STYLE_FACE) {
        ui_style_apply_ex(UI_STYLE_FACE, false);
    }
    lvgl_port_unlock();
}

// 退出场景：恢复进入前的风格（同样不写 NVS）。未进入场景时无副作用。
void voice_display_lcd_scene_exit_face(void) {
    if (!s_ready) {
        return;
    }
    if (!lvgl_port_lock(100)) {
        ESP_LOGW(TAG, "LVGL busy; deferring scene exit");
        return;
    }
    if (s_scene_face_active) {
        ui_style_t prev = s_scene_prev_style;
        s_scene_face_active = false;
        if (prev != s_ui_style) {
            ui_style_apply_ex(prev, false);
        }
    }
    lvgl_port_unlock();
}

static esp_err_t init_touch(void) {
    i2c_master_bus_handle_t bus = voice_i2c_bus_get();
    if (!bus) {
        return ESP_FAIL;
    }

    esp_lcd_panel_io_handle_t tp_io = NULL;
    esp_lcd_panel_io_i2c_config_t tp_io_cfg = ESP_LCD_TOUCH_IO_I2C_CST816S_CONFIG();
    tp_io_cfg.scl_speed_hz = 400000;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_i2c(bus, &tp_io_cfg, &tp_io),
                        TAG, "touch panel io failed");

    esp_lcd_touch_config_t tp_cfg = {
        .x_max = LCD_H_RES,
        .y_max = LCD_V_RES,
        .rst_gpio_num = TOUCH_PIN_RST,
        .int_gpio_num = TOUCH_PIN_INT,
        .levels = {
            .reset = 0,
            .interrupt = 0,
        },
    };
    ESP_RETURN_ON_ERROR(esp_lcd_touch_new_i2c_cst816s(tp_io, &tp_cfg, &s_touch),
                        TAG, "cst816s init failed");

    const lvgl_port_touch_cfg_t touch_cfg = {
        .disp = s_disp,
        .handle = s_touch,
    };
    if (!lvgl_port_add_touch(&touch_cfg)) {
        ESP_LOGE(TAG, "lvgl touch add failed");
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "cst816s touch ready: INT=%d RST=%d", TOUCH_PIN_INT, TOUCH_PIN_RST);
    return ESP_OK;
}

esp_err_t voice_display_lcd_start(void) {
    ESP_LOGI(TAG, "initializing ST7789 LCD on SPI2 + LVGL");

    gpio_config_t bk_cfg = {
        .pin_bit_mask = 1ULL << LCD_PIN_BACKLIGHT,
        .mode = GPIO_MODE_OUTPUT,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&bk_cfg), TAG, "backlight gpio config failed");
    gpio_set_level(LCD_PIN_BACKLIGHT, 0);

    spi_bus_config_t buscfg = {
        .sclk_io_num = LCD_PIN_SCLK,
        .mosi_io_num = LCD_PIN_MOSI,
        .miso_io_num = -1,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = LCD_H_RES * LCD_DRAW_BUF_LINES * sizeof(uint16_t),
    };
    esp_err_t ret = spi_bus_initialize(LCD_HOST, &buscfg, SPI_DMA_CH_AUTO);
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "spi_bus_initialize failed: %s", esp_err_to_name(ret));
        return ret;
    }

    esp_lcd_panel_io_handle_t io_handle = NULL;
    esp_lcd_panel_io_spi_config_t io_config = {
        .dc_gpio_num = LCD_PIN_DC,
        .cs_gpio_num = LCD_PIN_CS,
        .pclk_hz = LCD_SPI_CLOCK_HZ,
        .lcd_cmd_bits = LCD_CMD_BITS,
        .lcd_param_bits = LCD_PARAM_BITS,
        .spi_mode = 0,
        .trans_queue_depth = 10,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi(LCD_HOST, &io_config, &io_handle),
                        TAG, "panel io init failed");

    esp_lcd_panel_handle_t panel = NULL;
    esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = LCD_PIN_RST,
        /* This panel is wired RGB, not BGR: with BGR the red and blue channels
         * were swapped (saturated red rendered as blue/cyan; light-cyan eyes
         * leaned orange). White/black/grey are R/B-symmetric so the mistake hid
         * until the red 田字格/红舌 made it obvious. */
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
        .flags = {
            .reset_active_high = 0,
        },
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_st7789(io_handle, &panel_config, &panel),
                        TAG, "st7789 panel init failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(panel), TAG, "panel reset failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(panel), TAG, "panel init failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_invert_color(panel, true), TAG, "panel invert failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(panel, true), TAG, "panel display on failed");

    lvgl_port_cfg_t port_cfg = ESP_LVGL_PORT_INIT_CONFIG();
    port_cfg.task_stack = 10240;
    port_cfg.task_max_sleep_ms = 50;
    ESP_RETURN_ON_ERROR(lvgl_port_init(&port_cfg), TAG, "lvgl port init failed");

    const lvgl_port_display_cfg_t disp_cfg = {
        .io_handle = io_handle,
        .panel_handle = panel,
        .buffer_size = LCD_H_RES * LCD_DRAW_BUF_LINES,
        .double_buffer = true,
        .hres = LCD_H_RES,
        .vres = LCD_V_RES,
        .monochrome = false,
        .rotation = {
            .swap_xy = false,
            .mirror_x = false,
            .mirror_y = false,
        },
        .flags = {
            .buff_dma = true,
            .swap_bytes = true,
        },
    };
    s_disp = lvgl_port_add_disp(&disp_cfg);
    if (!s_disp) {
        ESP_LOGE(TAG, "lvgl display add failed");
        return ESP_FAIL;
    }

    if (init_touch() != ESP_OK) {
        ESP_LOGW(TAG, "touch unavailable; BOOT key remains the toggle");
    }

    lvgl_port_lock(0);
    build_widgets();
    lvgl_port_unlock();

    gpio_set_level(LCD_PIN_BACKLIGHT, 1);
    s_ready = true;
    if (!s_ui_watchdog_task) {
        if (xTaskCreate(ui_watchdog_task, "ui_wdog", 2048, NULL, 2,
                        &s_ui_watchdog_task) != pdPASS) {
            s_ui_watchdog_task = NULL;
            ESP_LOGW(TAG, "ui watchdog task create failed");
        }
    }
    ESP_LOGI(TAG, "LCD + LVGL ready");
    return ESP_OK;
}

void voice_display_lcd_render(const voice_ui_state_t *state) {
    if (!state || !s_ready) {
        return;
    }

    screen_style_t style = screen_style(state->screen);

    /* Never let a producer task wait forever behind a stalled LVGL callback.
     * A missed frame is harmless; blocking the UI/event pipeline until the
     * watchdog fires is not. The next state event or animation tick will
     * repaint the screen. */
    if (!lvgl_port_lock(100)) {
        ESP_LOGW(TAG, "LVGL busy; skip render for screen=%d", (int)state->screen);
        return;
    }

    bool was_busy = (s_prev_screen == VOICE_UI_SCREEN_RECORDING ||
                     s_prev_screen == VOICE_UI_SCREEN_RECOGNIZING ||
                     s_prev_screen == VOICE_UI_SCREEN_THINKING ||
                     s_prev_screen == VOICE_UI_SCREEN_PLAYBACK);
    bool now_idle = (state->screen == VOICE_UI_SCREEN_IDLE);
    if (was_busy && now_idle && s_ui_style == UI_STYLE_BUTTERFLY &&
        s_bf_fly_phase == 0 && s_img_bf_wing) {
        s_bf_fly_phase = 1;
    }
    s_prev_screen = state->screen;

    s_cur_screen = state->screen;
    s_online = state->authenticated && screen_is_online(state->screen);
    lv_label_set_text(s_lbl_detail,
                      screen_detail_is_dialog_context(state->screen) ? state->detail : "");
    lv_label_set_text(s_lbl_subtitle, state->subtitle);
    /* Top-left: short Chinese voice-interaction status (small font). */
    lv_label_set_text(s_lbl_topbar, style.status_cn);
    lv_obj_set_style_border_color(s_status_pill, style.color, 0);
    lv_obj_set_style_bg_color(s_status_dot, style.color, 0);
    lv_obj_set_style_text_color(s_lbl_detail, style.color, 0);
    if (state->subtitle[0] != '\0') {
        lv_obj_clear_flag(s_subtitle_card, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_lbl_subtitle, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_border_color(s_subtitle_card, style.color, 0);
    } else {
        lv_obj_add_flag(s_subtitle_card, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_lbl_subtitle, LV_OBJ_FLAG_HIDDEN);
    }

    lvgl_port_unlock();
}

/* ===== 成语大字（2×2 静态填充，无描红动画） ============================= */

#define IDIOM_MARGIN 14
#define IDIOM_GAP 8
#define IDIOM_COLS 2

static lv_obj_t *s_idiom_overlay;
static lv_obj_t *s_idiom_canvases[VOICE_IDIOM_MAX_GLYPHS];
static uint8_t *s_idiom_canvas_bufs[VOICE_IDIOM_MAX_GLYPHS];
static int s_idiom_cell_px;
static int s_idiom_origin_x;
static int s_idiom_origin_y;
static size_t s_idiom_count;

static void idiom_overlay_close(void) {
    if (s_idiom_overlay) {
        lv_obj_del(s_idiom_overlay);  // frees child canvases (objects) too
        s_idiom_overlay = NULL;
    }
    for (size_t i = 0; i < VOICE_IDIOM_MAX_GLYPHS; i++) {
        s_idiom_canvases[i] = NULL;
        if (s_idiom_canvas_bufs[i]) {
            heap_caps_free(s_idiom_canvas_bufs[i]);
            s_idiom_canvas_bufs[i] = NULL;
        }
    }
    s_idiom_count = 0;
}

static void idiom_touch_cb(lv_event_t *event) {
    (void)event;
    idiom_overlay_close();
}

/* Even-odd scanline fill of one 楷体 polygon (points 0..1000) into an A8 mask of
 * `px`×`px`. Static (no along-median sweep) — the whole stroke is filled. */
static void idiom_fill_outline_into(uint8_t *buf, uint32_t stride, int px,
                                    const voice_trace_outline_t *poly, uint8_t alpha) {
    if (!buf || !poly) {
        return;
    }
    const int n = poly->point_count;
    if (n < 3) {
        return;
    }
    float fx[VOICE_TRACE_OUTLINE_MAX_POINTS];
    float fy[VOICE_TRACE_OUTLINE_MAX_POINTS];
    const float scale = (float)px / 1000.0f;
    float ymin = (float)px;
    float ymax = 0.0f;
    for (int i = 0; i < n; i++) {
        fx[i] = (float)poly->points[i].x * scale;
        fy[i] = (float)poly->points[i].y * scale;
        if (fy[i] < ymin) {
            ymin = fy[i];
        }
        if (fy[i] > ymax) {
            ymax = fy[i];
        }
    }
    int y0i = (int)ymin;
    int y1i = (int)ymax + 1;
    if (y0i < 0) {
        y0i = 0;
    }
    if (y1i > px) {
        y1i = px;
    }
    for (int y = y0i; y < y1i; y++) {
        const float yc = (float)y + 0.5f;
        float xs[VOICE_TRACE_OUTLINE_MAX_POINTS];
        int cnt = 0;
        for (int i = 0; i < n; i++) {
            const int j = (i + 1) % n;
            const float ya = fy[i];
            const float yb = fy[j];
            if ((ya <= yc) == (yb <= yc)) {
                continue;
            }
            const float t = (yc - ya) / (yb - ya);
            xs[cnt++] = fx[i] + t * (fx[j] - fx[i]);
        }
        for (int a = 1; a < cnt; a++) {
            float key = xs[a];
            int b = a - 1;
            while (b >= 0 && xs[b] > key) {
                xs[b + 1] = xs[b];
                b--;
            }
            xs[b + 1] = key;
        }
        uint8_t *row = buf + (size_t)y * stride;
        for (int k = 0; k + 1 < cnt; k += 2) {
            int xa = (int)(xs[k] + 0.5f);
            int xb = (int)(xs[k + 1] + 0.5f);
            if (xa < 0) {
                xa = 0;
            }
            if (xb > px) {
                xb = px;
            }
            for (int x = xa; x < xb; x++) {
                row[x] = alpha;
            }
        }
    }
}

void voice_display_lcd_show_idiom_begin(size_t glyph_count) {
    if (!s_ready || glyph_count == 0) {
        return;
    }
    if (glyph_count > VOICE_IDIOM_MAX_GLYPHS) {
        glyph_count = VOICE_IDIOM_MAX_GLYPHS;
    }
    if (!lvgl_port_lock(100)) {
        ESP_LOGW(TAG, "LVGL busy; skipping frame render");
        return;
    }
    trace_overlay_close();
    color_test_overlay_close();
    idiom_overlay_close();
    menu_close();

    s_idiom_count = glyph_count;
    const int cols = IDIOM_COLS;
    const int rows = (int)((glyph_count + cols - 1) / cols);
    int cell_w = (LCD_H_RES - 2 * IDIOM_MARGIN - (cols - 1) * IDIOM_GAP) / cols;
    int cell_h = (LCD_V_RES - 2 * IDIOM_MARGIN - (rows - 1) * IDIOM_GAP) / rows;
    s_idiom_cell_px = cell_w < cell_h ? cell_w : cell_h;
    if (s_idiom_cell_px < 1) {
        s_idiom_cell_px = 1;
    }
    int grid_w = cols * s_idiom_cell_px + (cols - 1) * IDIOM_GAP;
    int grid_h = rows * s_idiom_cell_px + (rows - 1) * IDIOM_GAP;
    s_idiom_origin_x = (LCD_H_RES - grid_w) / 2;
    s_idiom_origin_y = (LCD_V_RES - grid_h) / 2;

    lv_obj_t *scr = lv_screen_active();
    s_idiom_overlay = lv_obj_create(scr);
    lv_obj_remove_style_all(s_idiom_overlay);
    lv_obj_set_size(s_idiom_overlay, LCD_H_RES, LCD_V_RES);
    lv_obj_set_style_bg_color(s_idiom_overlay, lv_color_hex(TRACE_BG_COLOR), 0);
    lv_obj_set_style_bg_opa(s_idiom_overlay, LV_OPA_COVER, 0);
    lv_obj_clear_flag(s_idiom_overlay, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(s_idiom_overlay, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(s_idiom_overlay, idiom_touch_cb, LV_EVENT_CLICKED, NULL);
    lvgl_port_unlock();
}

void voice_display_lcd_show_idiom_glyph(size_t index,
                                        const voice_trace_stroke_t *strokes,
                                        size_t stroke_count,
                                        const voice_trace_outline_t *outlines,
                                        const char *utf8_char) {
    (void)strokes;
    if (!s_ready || !s_idiom_overlay || index >= s_idiom_count ||
        index >= VOICE_IDIOM_MAX_GLYPHS) {
        return;
    }
    const int px = s_idiom_cell_px;
    const int col = (int)(index % IDIOM_COLS);
    const int row = (int)(index / IDIOM_COLS);
    const int x = s_idiom_origin_x + col * (px + IDIOM_GAP);
    const int y = s_idiom_origin_y + row * (px + IDIOM_GAP);

    if (!lvgl_port_lock(100)) {
        ESP_LOGW(TAG, "LVGL busy; skipping idiom glyph %u", (unsigned)index);
        return;
    }
    bool drew = false;
    if (outlines && stroke_count > 0) {
        uint32_t stride = lv_draw_buf_width_to_stride(px, LV_COLOR_FORMAT_A8);
        size_t buf_sz = (size_t)stride * px;
        uint8_t *buf = heap_caps_malloc(buf_sz, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!buf) {
            buf = heap_caps_malloc(buf_sz, MALLOC_CAP_8BIT);
        }
        if (buf) {
            memset(buf, 0, buf_sz);
            for (size_t s = 0; s < stroke_count; s++) {
                idiom_fill_outline_into(buf, stride, px, &outlines[s], 0xFF);
            }
            lv_obj_t *canvas = lv_canvas_create(s_idiom_overlay);
            lv_canvas_set_buffer(canvas, buf, px, px, LV_COLOR_FORMAT_A8);
            lv_obj_set_pos(canvas, x, y);
            lv_obj_set_style_image_recolor(canvas, lv_color_hex(TRACE_STROKE_COLOR), 0);
            lv_obj_set_style_image_recolor_opa(canvas, LV_OPA_COVER, 0);
            lv_obj_clear_flag(canvas, LV_OBJ_FLAG_SCROLLABLE);
            lv_obj_clear_flag(canvas, LV_OBJ_FLAG_CLICKABLE);
            s_idiom_canvases[index] = canvas;
            s_idiom_canvas_bufs[index] = buf;
            drew = true;
        }
    }
    if (!drew && utf8_char && utf8_char[0]) {
        // No outline (or alloc failed): fall back to the small CJK font label so
        // the character is at least legible in its cell.
        lv_obj_t *label = lv_label_create(s_idiom_overlay);
        lv_obj_set_style_text_font(label, &lv_font_cjk_sc_16, 0);
        lv_obj_set_style_text_color(label, lv_color_hex(TRACE_STROKE_COLOR), 0);
        lv_label_set_text(label, utf8_char);
        lv_obj_set_pos(label, x + px / 2 - 8, y + px / 2 - 8);
        s_idiom_canvases[index] = label;
    }
    lvgl_port_unlock();
}

void voice_display_lcd_show_trace(const char *character,
                                  const voice_trace_stroke_t *strokes,
                                  size_t stroke_count,
                                  const voice_trace_outline_t *outlines,
                                  uint32_t duration_ms) {
    (void)character;
    if (!s_ready || !strokes || stroke_count == 0) {
        return;
    }
    if (stroke_count > VOICE_TRACE_MAX_STROKES) {
        stroke_count = VOICE_TRACE_MAX_STROKES;
    }

    if (!lvgl_port_lock(100)) {
        ESP_LOGW(TAG, "LVGL busy; skipping trace render");
        return;
    }
    trace_overlay_close();  // frees any prior outline/canvas before we reuse state
    memset(s_trace_strokes, 0, sizeof(s_trace_strokes));
    memcpy(s_trace_strokes, strokes, stroke_count * sizeof(voice_trace_stroke_t));
    s_trace_stroke_count = stroke_count;
    s_trace_current_stroke = 0;
    s_trace_current_segment = 0;
    s_trace_duration_ms = (duration_ms ? duration_ms : 1800) *
                          TRACE_KAITI_DURATION_SCALE_NUM /
                          TRACE_KAITI_DURATION_SCALE_DEN;
    s_trace_completed = false;
    size_t outline_points = trace_outline_total_points(outlines, stroke_count);
    if (outline_points > TRACE_OUTLINE_MAX_TOTAL_POINTS) {
        ESP_LOGW(TAG, "trace outline too complex (%u pts); skeleton fallback",
                 (unsigned)outline_points);
        outlines = NULL;
    }

    // Copy the 楷体 outlines into our own heap storage (caller's buffer is
    // transient). NULL/alloc-failure/too-complex → skeleton render via medians.
    if (outlines) {
        s_trace_outlines = heap_caps_malloc(stroke_count * sizeof(voice_trace_outline_t),
                                            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!s_trace_outlines) {
            s_trace_outlines = heap_caps_malloc(stroke_count * sizeof(voice_trace_outline_t),
                                                MALLOC_CAP_8BIT);
        }
        if (s_trace_outlines) {
            memcpy(s_trace_outlines, outlines,
                   stroke_count * sizeof(voice_trace_outline_t));
        }
    }
    trace_overlay_open();
    lvgl_port_unlock();
}

void voice_display_lcd_show_color_test(const char *color) {
    if (!s_ready) {
        return;
    }
    if (!lvgl_port_lock(100)) {
        ESP_LOGW(TAG, "LVGL busy; skipping color test");
        return;
    }
    color_test_overlay_open(color);
    lvgl_port_unlock();
}
#else
esp_err_t voice_display_lcd_start(void) {
    ESP_LOGI(TAG, "LCD disabled by CONFIG_VR_BOARD_UI_ENABLE=n");
    return ESP_OK;
}

void voice_display_lcd_render(const voice_ui_state_t *state) {
    (void)state;
}

void voice_display_lcd_show_trace(const char *character,
                                  const voice_trace_stroke_t *strokes,
                                  size_t stroke_count,
                                  const voice_trace_outline_t *outlines,
                                  uint32_t duration_ms) {
    (void)character;
    (void)strokes;
    (void)stroke_count;
    (void)outlines;
    (void)duration_ms;
}

void voice_display_lcd_show_color_test(const char *color) {
    (void)color;
}

void voice_display_lcd_show_idiom_begin(size_t glyph_count) {
    (void)glyph_count;
}

void voice_display_lcd_show_idiom_glyph(size_t index,
                                        const voice_trace_stroke_t *strokes,
                                        size_t stroke_count,
                                        const voice_trace_outline_t *outlines,
                                        const char *utf8_char) {
    (void)index;
    (void)strokes;
    (void)stroke_count;
    (void)outlines;
    (void)utf8_char;
}

void voice_display_lcd_scene_enter_face(void) {}
void voice_display_lcd_scene_exit_face(void) {}
#endif
