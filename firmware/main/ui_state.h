#pragma once

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define VOICE_UI_TEXT_MAX 128
#define VOICE_UI_SUBTITLE_MAX 256

typedef enum {
    VOICE_UI_SCREEN_BOOT = 0,
    VOICE_UI_SCREEN_WIFI,
    VOICE_UI_SCREEN_CLOUD,
    VOICE_UI_SCREEN_IDLE,
    VOICE_UI_SCREEN_RECORDING,
    VOICE_UI_SCREEN_RECOGNIZING,
    VOICE_UI_SCREEN_THINKING,
    VOICE_UI_SCREEN_PLAYBACK,
    VOICE_UI_SCREEN_BLE_GATEWAY,
    VOICE_UI_SCREEN_PROVISIONING,
    VOICE_UI_SCREEN_ERROR,
} voice_ui_screen_t;

typedef enum {
    VOICE_UI_EVENT_BOOT = 0,
    VOICE_UI_EVENT_WIFI_CONNECTING,
    VOICE_UI_EVENT_WIFI_CONNECTED,
    VOICE_UI_EVENT_WIFI_FAILED,
    VOICE_UI_EVENT_WS_CONNECTING,
    VOICE_UI_EVENT_WS_CONNECTED,
    VOICE_UI_EVENT_WS_DISCONNECTED,
    VOICE_UI_EVENT_AUTH_OK,
    VOICE_UI_EVENT_RECORDING_START,
    VOICE_UI_EVENT_RECORDING_END,
    VOICE_UI_EVENT_ASR_PARTIAL,
    VOICE_UI_EVENT_ASR_RESULT,
    VOICE_UI_EVENT_LLM_START,
    VOICE_UI_EVENT_LLM_DELTA,
    VOICE_UI_EVENT_LLM_END,
    VOICE_UI_EVENT_TTS_START,
    VOICE_UI_EVENT_TTS_END,
    VOICE_UI_EVENT_BARGE_IN,
    VOICE_UI_EVENT_CONVERSATION_OFF,
    VOICE_UI_EVENT_BLE_FALLBACK_READY,
    VOICE_UI_EVENT_PROVISIONING,
    VOICE_UI_EVENT_RETRY,
    VOICE_UI_EVENT_ERROR,
} voice_ui_event_type_t;

typedef struct {
    voice_ui_event_type_t type;
    const char *detail;
} voice_ui_event_t;

typedef struct {
    voice_ui_screen_t screen;
    char title[VOICE_UI_TEXT_MAX];
    char detail[VOICE_UI_TEXT_MAX];
    char subtitle[VOICE_UI_SUBTITLE_MAX];
    char device_id[VOICE_UI_TEXT_MAX];
    char last_error[VOICE_UI_TEXT_MAX];
    bool cloud_connected;
    bool authenticated;
    bool recording;
    bool playing;
} voice_ui_state_t;

void voice_ui_state_init(voice_ui_state_t *state);
void voice_ui_state_apply(voice_ui_state_t *state, const voice_ui_event_t *event);
const char *voice_ui_screen_name(voice_ui_screen_t screen);

#ifdef __cplusplus
}
#endif
