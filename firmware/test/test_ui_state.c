#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "ui_state.h"

static void boot_to_idle_flow_sets_screen_and_detail(void) {
    voice_ui_state_t state;
    voice_ui_state_init(&state);

    assert(state.screen == VOICE_UI_SCREEN_BOOT);
    assert(strcmp(state.title, "启动中") == 0);

    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_WIFI_CONNECTING,
        .detail = "HomeWiFi",
    });
    assert(state.screen == VOICE_UI_SCREEN_WIFI);
    assert(strcmp(state.title, "Wi-Fi") == 0);
    assert(strcmp(state.detail, "正在连接 HomeWiFi") == 0);

    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_WIFI_CONNECTED,
        .detail = "192.168.1.23",
    });
    assert(state.screen == VOICE_UI_SCREEN_CLOUD);
    assert(strcmp(state.title, "网络已连接") == 0);
    assert(strcmp(state.detail, "IP 192.168.1.23") == 0);

    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_WS_CONNECTED,
        .detail = "esp32-001",
    });
    assert(state.screen == VOICE_UI_SCREEN_CLOUD);
    assert(strcmp(state.title, "云端") == 0);
    assert(strcmp(state.detail, "正在认证 esp32-001") == 0);

    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_AUTH_OK,
        .detail = "esp32-001",
    });
    assert(state.screen == VOICE_UI_SCREEN_IDLE);
    assert(strcmp(state.title, "在线") == 0);
    assert(strcmp(state.detail, "轻触说话") == 0);
    assert(strcmp(state.device_id, "esp32-001") == 0);
}

static void speech_events_drive_thinking_and_playback(void) {
    voice_ui_state_t state;
    voice_ui_state_init(&state);

    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_AUTH_OK});
    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_RECORDING_START});
    assert(state.screen == VOICE_UI_SCREEN_RECORDING);
    assert(strcmp(state.title, "收听中") == 0);

    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_ASR_PARTIAL,
        .detail = "tell a",
    });
    assert(state.screen == VOICE_UI_SCREEN_RECORDING);
    assert(strcmp(state.subtitle, "你：tell a") == 0);

    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_RECORDING_END});
    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_ASR_PARTIAL,
        .detail = "tell a dino",
    });
    assert(state.screen == VOICE_UI_SCREEN_RECOGNIZING);
    assert(strcmp(state.subtitle, "你：tell a dino") == 0);

    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_ASR_RESULT,
        .detail = "tell a dino story",
    });
    assert(state.screen == VOICE_UI_SCREEN_THINKING);
    assert(strcmp(state.subtitle, "你：tell a dino story") == 0);

    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_LLM_DELTA,
        .detail = "once ",
    });
    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_LLM_DELTA,
        .detail = "dino.",
    });
    assert(strcmp(state.subtitle, "我：once dino.") == 0);

    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_TTS_START});
    assert(state.screen == VOICE_UI_SCREEN_PLAYBACK);
    assert(strcmp(state.title, "播放中") == 0);

    /* tts_end no longer jumps to idle on its own (that raced the conversation
     * FSM's re-arm). It only clears the playing flag; the FSM posts the next
     * screen (RECORDING_START to re-listen, or CONVERSATION_OFF when off). */
    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_TTS_END,
        .detail = "natural_end",
    });
    assert(state.screen == VOICE_UI_SCREEN_PLAYBACK);
    assert(!state.playing);

    /* The conversation FSM then re-arms listening for the next utterance. */
    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_RECORDING_START});
    assert(state.screen == VOICE_UI_SCREEN_RECORDING);
    assert(strcmp(state.title, "收听中") == 0);
}

static void error_and_reconnect_are_visible(void) {
    voice_ui_state_t state;
    voice_ui_state_init(&state);

    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_ERROR,
        .detail = "auth failed",
    });
    assert(state.screen == VOICE_UI_SCREEN_ERROR);
    assert(strcmp(state.title, "出错了") == 0);
    assert(strcmp(state.last_error, "auth failed") == 0);

    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_WS_DISCONNECTED});
    assert(state.screen == VOICE_UI_SCREEN_CLOUD);
    assert(strcmp(state.title, "连接断开") == 0);
}

static void retry_hint_keeps_capture_ready(void) {
    voice_ui_state_t state;
    voice_ui_state_init(&state);

    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_AUTH_OK});
    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_RETRY,
        .detail = "没有听清，请再说一次",
    });
    assert(state.screen == VOICE_UI_SCREEN_RECORDING);
    assert(state.recording);
    assert(strcmp(state.title, "请再说一次") == 0);
    assert(strcmp(state.detail, "没有听清，请再说一次") == 0);
}

static void conversation_off_returns_to_idle(void) {
    voice_ui_state_t state;
    voice_ui_state_init(&state);

    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_AUTH_OK});
    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_RECORDING_START});
    assert(state.screen == VOICE_UI_SCREEN_RECORDING);

    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_CONVERSATION_OFF});
    assert(state.screen == VOICE_UI_SCREEN_IDLE);
    assert(!state.recording);
    assert(!state.playing);
    assert(strcmp(state.title, "在线") == 0);
}

static void ble_fallback_ready_is_visible(void) {
    voice_ui_state_t state;
    voice_ui_state_init(&state);

    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_BLE_FALLBACK_READY,
        .detail = "VR-esp32-001",
    });

    assert(state.screen == VOICE_UI_SCREEN_BLE_GATEWAY);
    assert(strcmp(state.title, "蓝牙") == 0);
    assert(strcmp(state.detail, "等待手机 VR-esp32-001") == 0);
}

static void provisioning_screen_is_visible(void) {
    voice_ui_state_t state;
    voice_ui_state_init(&state);

    voice_ui_state_apply(&state, &(voice_ui_event_t){
        .type = VOICE_UI_EVENT_PROVISIONING,
        .detail = "PROV_esp32-001",
    });
    assert(state.screen == VOICE_UI_SCREEN_PROVISIONING);
    assert(strcmp(state.title, "配网中") == 0);
    assert(strcmp(state.detail, "BLE PROV_esp32-001") == 0);
    assert(strcmp(voice_ui_screen_name(state.screen), "provisioning") == 0);
}

/* RECORDING_END splits into a dedicated "识别中" screen, distinct from the
 * "生成中" THINKING screen, so the device can label ASR vs LLM separately. */
static void recording_end_drives_recognizing(void) {
    voice_ui_state_t state;
    voice_ui_state_init(&state);

    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_AUTH_OK});
    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_RECORDING_START});
    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_RECORDING_END});
    assert(state.screen == VOICE_UI_SCREEN_RECOGNIZING);
    assert(strcmp(voice_ui_screen_name(state.screen), "recognizing") == 0);

    voice_ui_state_apply(&state, &(voice_ui_event_t){.type = VOICE_UI_EVENT_LLM_START});
    assert(state.screen == VOICE_UI_SCREEN_THINKING);
}

int main(void) {
    boot_to_idle_flow_sets_screen_and_detail();
    speech_events_drive_thinking_and_playback();
    recording_end_drives_recognizing();
    error_and_reconnect_are_visible();
    retry_hint_keeps_capture_ready();
    conversation_off_returns_to_idle();
    ble_fallback_ready_is_visible();
    provisioning_screen_is_visible();
    puts("ui_state tests passed");
    return 0;
}
