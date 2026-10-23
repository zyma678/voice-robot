#include "ui_state.h"

#include <stdio.h>
#include <string.h>

static void copy_text(char *dst, size_t dst_size, const char *src) {
    if (!dst || dst_size == 0) {
        return;
    }
    if (!src) {
        src = "";
    }
    snprintf(dst, dst_size, "%s", src);
}

static void format_text(char *dst, size_t dst_size, const char *prefix, const char *value) {
    if (!value || value[0] == '\0') {
        copy_text(dst, dst_size, prefix);
        return;
    }
    snprintf(dst, dst_size, "%s%s", prefix, value);
}

static void append_subtitle(voice_ui_state_t *state, const char *text) {
    if (!text || text[0] == '\0') {
        return;
    }
    size_t used = strlen(state->subtitle);
    if (used + 1 >= sizeof(state->subtitle)) {
        return;
    }
    snprintf(state->subtitle + used, sizeof(state->subtitle) - used, "%s", text);
}

static void set_idle(voice_ui_state_t *state) {
    state->screen = VOICE_UI_SCREEN_IDLE;
    state->recording = false;
    state->playing = false;
    copy_text(state->title, sizeof(state->title), "在线");
    copy_text(state->detail, sizeof(state->detail), "轻触说话");
}

void voice_ui_state_init(voice_ui_state_t *state) {
    if (!state) {
        return;
    }
    memset(state, 0, sizeof(*state));
    state->screen = VOICE_UI_SCREEN_BOOT;
    copy_text(state->title, sizeof(state->title), "启动中");
    copy_text(state->detail, sizeof(state->detail), "正在初始化设备");
}

void voice_ui_state_apply(voice_ui_state_t *state, const voice_ui_event_t *event) {
    if (!state || !event) {
        return;
    }

    const char *detail = event->detail ? event->detail : "";

    switch (event->type) {
        case VOICE_UI_EVENT_BOOT:
            voice_ui_state_init(state);
            break;
        case VOICE_UI_EVENT_WIFI_CONNECTING:
            state->screen = VOICE_UI_SCREEN_WIFI;
            copy_text(state->title, sizeof(state->title), "Wi-Fi");
            format_text(state->detail, sizeof(state->detail), "正在连接 ", detail);
            break;
        case VOICE_UI_EVENT_WIFI_CONNECTED:
            state->screen = VOICE_UI_SCREEN_CLOUD;
            copy_text(state->title, sizeof(state->title), "网络已连接");
            format_text(state->detail, sizeof(state->detail), "IP ", detail);
            break;
        case VOICE_UI_EVENT_WIFI_FAILED:
            state->screen = VOICE_UI_SCREEN_ERROR;
            copy_text(state->title, sizeof(state->title), "Wi-Fi 失败");
            copy_text(state->detail, sizeof(state->detail), detail[0] ? detail : "检查账号或密码");
            copy_text(state->last_error, sizeof(state->last_error), state->detail);
            break;
        case VOICE_UI_EVENT_WS_CONNECTING:
            state->screen = VOICE_UI_SCREEN_CLOUD;
            copy_text(state->title, sizeof(state->title), "云端");
            copy_text(state->detail, sizeof(state->detail), "正在连接");
            break;
        case VOICE_UI_EVENT_WS_CONNECTED:
            state->screen = VOICE_UI_SCREEN_CLOUD;
            state->cloud_connected = true;
            state->authenticated = false;
            copy_text(state->title, sizeof(state->title), "云端");
            format_text(state->detail, sizeof(state->detail), "正在认证 ", detail);
            if (detail[0] != '\0') {
                copy_text(state->device_id, sizeof(state->device_id), detail);
            }
            break;
        case VOICE_UI_EVENT_WS_DISCONNECTED:
            state->screen = VOICE_UI_SCREEN_CLOUD;
            state->cloud_connected = false;
            state->authenticated = false;
            state->playing = false;
            copy_text(state->title, sizeof(state->title), "连接断开");
            copy_text(state->detail, sizeof(state->detail), "正在重连");
            break;
        case VOICE_UI_EVENT_AUTH_OK:
            state->cloud_connected = true;
            state->authenticated = true;
            if (detail[0] != '\0') {
                copy_text(state->device_id, sizeof(state->device_id), detail);
            }
            set_idle(state);
            break;
        case VOICE_UI_EVENT_RECORDING_START:
            state->screen = VOICE_UI_SCREEN_RECORDING;
            state->recording = true;
            state->playing = false;
            state->subtitle[0] = '\0';
            copy_text(state->title, sizeof(state->title), "收听中");
            copy_text(state->detail, sizeof(state->detail), "请说话");
            break;
        case VOICE_UI_EVENT_RECORDING_END:
            state->screen = VOICE_UI_SCREEN_RECOGNIZING;
            state->recording = false;
            copy_text(state->title, sizeof(state->title), "识别中");
            copy_text(state->detail, sizeof(state->detail), "正在识别语音");
            break;
        case VOICE_UI_EVENT_ASR_PARTIAL:
            /* Partial text can arrive both while the mic is still open and
             * after audio_end. Preserve that phase instead of jumping screens. */
            state->screen = state->recording ? VOICE_UI_SCREEN_RECORDING
                                             : VOICE_UI_SCREEN_RECOGNIZING;
            copy_text(state->title, sizeof(state->title),
                      state->recording ? "收听中" : "识别中");
            copy_text(state->detail, sizeof(state->detail), "已听到");
            format_text(state->subtitle, sizeof(state->subtitle), "你：", detail);
            break;
        case VOICE_UI_EVENT_ASR_RESULT:
            state->screen = VOICE_UI_SCREEN_THINKING;
            state->recording = false;
            copy_text(state->title, sizeof(state->title), "思考中");
            copy_text(state->detail, sizeof(state->detail), "正在生成回复");
            /* Show what the user said ("你：…") while we wait for the reply. */
            format_text(state->subtitle, sizeof(state->subtitle), "你：", detail);
            break;
        case VOICE_UI_EVENT_LLM_START:
            state->screen = VOICE_UI_SCREEN_THINKING;
            copy_text(state->title, sizeof(state->title), "思考中");
            copy_text(state->detail, sizeof(state->detail), "正在生成回复");
            /* Keep the user's line on screen until the first reply token arrives. */
            break;
        case VOICE_UI_EVENT_LLM_DELTA:
            state->screen = VOICE_UI_SCREEN_THINKING;
            /* First token of a new reply: switch the subtitle to "我：…". */
            if (strcmp(state->title, "回复中") != 0) {
                copy_text(state->subtitle, sizeof(state->subtitle), "我：");
            }
            copy_text(state->title, sizeof(state->title), "回复中");
            append_subtitle(state, detail);
            break;
        case VOICE_UI_EVENT_LLM_END:
            if (detail[0] != '\0') {
                format_text(state->subtitle, sizeof(state->subtitle), "我：", detail);
            }
            break;
        case VOICE_UI_EVENT_TTS_START:
            state->screen = VOICE_UI_SCREEN_PLAYBACK;
            state->playing = true;
            state->recording = false;
            copy_text(state->title, sizeof(state->title), "播放中");
            copy_text(state->detail, sizeof(state->detail), "轻触打断");
            break;
        case VOICE_UI_EVENT_TTS_END:
            if (strcmp(detail, "barge_in") == 0) {
                state->screen = VOICE_UI_SCREEN_RECORDING;
                state->playing = false;
                state->recording = true;
                copy_text(state->title, sizeof(state->title), "已打断");
                copy_text(state->detail, sizeof(state->detail), "收听中");
            } else {
                /* The server's tts_end only means input is done — the speaker tail
                 * is still draining. Don't jump to idle here: that races the
                 * conversation FSM, which (after the drain) posts RECORDING_START
                 * to re-arm listening (or CONVERSATION_OFF for a console say). That
                 * race is why the screen sometimes flickered 识别中→点击开始对话 vs
                 * →收听中. Just clear the playing flag; the FSM owns the next screen. */
                state->playing = false;
            }
            break;
        case VOICE_UI_EVENT_BARGE_IN:
            state->screen = VOICE_UI_SCREEN_RECORDING;
            state->playing = false;
            state->recording = true;
            copy_text(state->title, sizeof(state->title), "已打断");
            copy_text(state->detail, sizeof(state->detail), "收听中");
            break;
        case VOICE_UI_EVENT_CONVERSATION_OFF:
            set_idle(state);
            break;
        case VOICE_UI_EVENT_BLE_FALLBACK_READY:
            state->screen = VOICE_UI_SCREEN_BLE_GATEWAY;
            state->cloud_connected = false;
            state->authenticated = false;
            state->playing = false;
            state->recording = false;
            copy_text(state->title, sizeof(state->title), "蓝牙");
            format_text(state->detail, sizeof(state->detail), "等待手机 ", detail);
            break;
        case VOICE_UI_EVENT_PROVISIONING:
            state->screen = VOICE_UI_SCREEN_PROVISIONING;
            state->cloud_connected = false;
            state->authenticated = false;
            state->playing = false;
            state->recording = false;
            copy_text(state->title, sizeof(state->title), "配网中");
            format_text(state->detail, sizeof(state->detail), "BLE ", detail);
            break;
        case VOICE_UI_EVENT_RETRY:
            state->screen = VOICE_UI_SCREEN_RECORDING;
            state->recording = true;
            state->playing = false;
            copy_text(state->title, sizeof(state->title), "请再说一次");
            copy_text(state->detail, sizeof(state->detail),
                      detail[0] ? detail : "我没有听清");
            copy_text(state->last_error, sizeof(state->last_error), state->detail);
            break;
        case VOICE_UI_EVENT_ERROR:
            state->screen = VOICE_UI_SCREEN_ERROR;
            state->recording = false;
            state->playing = false;
            copy_text(state->title, sizeof(state->title), "出错了");
            copy_text(state->detail, sizeof(state->detail), detail[0] ? detail : "未知错误");
            copy_text(state->last_error, sizeof(state->last_error), state->detail);
            break;
    }
}

const char *voice_ui_screen_name(voice_ui_screen_t screen) {
    switch (screen) {
        case VOICE_UI_SCREEN_BOOT:
            return "boot";
        case VOICE_UI_SCREEN_WIFI:
            return "wifi";
        case VOICE_UI_SCREEN_CLOUD:
            return "cloud";
        case VOICE_UI_SCREEN_IDLE:
            return "idle";
        case VOICE_UI_SCREEN_RECORDING:
            return "recording";
        case VOICE_UI_SCREEN_RECOGNIZING:
            return "recognizing";
        case VOICE_UI_SCREEN_THINKING:
            return "thinking";
        case VOICE_UI_SCREEN_PLAYBACK:
            return "playback";
        case VOICE_UI_SCREEN_BLE_GATEWAY:
            return "ble_gateway";
        case VOICE_UI_SCREEN_PROVISIONING:
            return "provisioning";
        case VOICE_UI_SCREEN_ERROR:
            return "error";
    }
    return "unknown";
}
