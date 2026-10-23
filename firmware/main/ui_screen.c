#include "ui_screen.h"

#include <stdio.h>
#include <string.h>

#include "display_lcd.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

static const char *TAG = "ui";

typedef struct {
    voice_ui_event_type_t type;
    char detail[VOICE_UI_TEXT_MAX];
} queued_ui_event_t;

static QueueHandle_t s_ui_queue;
static TaskHandle_t s_ui_task;
static voice_ui_state_t s_ui_state;

/* Rendering now includes the CJK subtitle/font path and the animated widget
 * tree. 4 KiB was enough for the old log-only renderer but can overflow when
 * LVGL walks a long label, leaving the device looking like a hard UI freeze.
 * Keep the task in internal RAM and give it a small safety margin. */
#define UI_TASK_STACK_WORDS 6144

static void render_state(void) {
    ESP_LOGI(TAG, "[%s] %s | %s | %s",
             voice_ui_screen_name(s_ui_state.screen),
             s_ui_state.title,
             s_ui_state.detail,
             s_ui_state.subtitle);
    voice_display_lcd_render(&s_ui_state);
}

static void init_display(void) {
    esp_err_t ret = voice_display_lcd_start();
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "display init failed: %s; using log renderer", esp_err_to_name(ret));
    }
}

static void ui_task(void *arg) {
    (void)arg;
    init_display();
    voice_ui_state_init(&s_ui_state);
    render_state();

    queued_ui_event_t queued;
    while (true) {
        if (xQueueReceive(s_ui_queue, &queued, portMAX_DELAY) == pdTRUE) {
            voice_ui_event_t event = {
                .type = queued.type,
                .detail = queued.detail,
            };
            voice_ui_state_apply(&s_ui_state, &event);
            render_state();
        }
    }
}

esp_err_t voice_ui_start(void) {
    if (s_ui_queue) {
        return ESP_OK;
    }

    s_ui_queue = xQueueCreate(16, sizeof(queued_ui_event_t));
    if (!s_ui_queue) {
        ESP_LOGE(TAG, "queue create failed");
        return ESP_FAIL;
    }

    if (xTaskCreate(ui_task, "ui", UI_TASK_STACK_WORDS, NULL, 3, &s_ui_task) != pdPASS) {
        ESP_LOGE(TAG, "task create failed");
        return ESP_FAIL;
    }
    return ESP_OK;
}

void voice_ui_post_event(voice_ui_event_type_t type, const char *detail) {
    if (!s_ui_queue) {
        return;
    }

    queued_ui_event_t queued = {
        .type = type,
    };
    if (detail) {
        snprintf(queued.detail, sizeof(queued.detail), "%s", detail);
    }

    if (xQueueSend(s_ui_queue, &queued, 0) == pdTRUE) {
        return;
    }

    /* Partial ASR/LLM deltas are best-effort and may legitimately be dropped
     * under burst load.  Lifecycle/error events are not: dropping an
     * audio_end, TTS_END or CONVERSATION_OFF leaves the state machine stranded
     * on a busy screen.  Make room by evicting one queued event, then enqueue
     * the newest event with a short bounded wait (never block a producer).
     */
    bool critical = type != VOICE_UI_EVENT_ASR_PARTIAL &&
                    type != VOICE_UI_EVENT_LLM_DELTA;
    if (!critical) {
        ESP_LOGW(TAG, "drop transient UI event type=%d", (int)type);
        return;
    }
    queued_ui_event_t discarded;
    (void)xQueueReceive(s_ui_queue, &discarded, 0);
    if (xQueueSend(s_ui_queue, &queued, pdMS_TO_TICKS(20)) != pdTRUE) {
        ESP_LOGW(TAG, "drop critical UI event type=%d (queue busy)", (int)type);
    }
}

const voice_ui_state_t *voice_ui_get_state(void) {
    return &s_ui_state;
}
