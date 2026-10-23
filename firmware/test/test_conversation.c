#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "conversation.h"

#define FRAME_SAMPLES VOICE_CONV_FRAME_SAMPLES

typedef struct {
    bool capture_enabled;
    int capture_calls;
    int audio_start_calls;
    int audio_end_calls;
    int frame_calls;
    size_t frame_samples_total;
    int barge_in_calls;
    char barge_in_reason[64];
    int text_input_calls;
    char text_input_text[128];
    int stop_playback_calls;
    int ui_recording_start;
    int ui_recording_end;
    int ui_conversation_off;
} fake_ops_log_t;

static fake_ops_log_t s_log;

static void fake_capture_set_enabled(bool enabled, void *ctx) {
    (void)ctx;
    s_log.capture_enabled = enabled;
    s_log.capture_calls++;
}

static void fake_send_audio_start(void *ctx) {
    (void)ctx;
    s_log.audio_start_calls++;
}

static void fake_send_audio_frame(const int16_t *samples, size_t count, void *ctx) {
    (void)ctx;
    assert(samples != NULL);
    s_log.frame_calls++;
    s_log.frame_samples_total += count;
}

static void fake_send_audio_end(void *ctx) {
    (void)ctx;
    s_log.audio_end_calls++;
}

static void fake_send_barge_in(const char *reason, void *ctx) {
    (void)ctx;
    s_log.barge_in_calls++;
    snprintf(s_log.barge_in_reason, sizeof(s_log.barge_in_reason), "%s", reason ? reason : "");
}

static void fake_send_text_input(const char *text, void *ctx) {
    (void)ctx;
    s_log.text_input_calls++;
    snprintf(s_log.text_input_text, sizeof(s_log.text_input_text), "%s", text ? text : "");
}

static void fake_stop_playback(void *ctx) {
    (void)ctx;
    s_log.stop_playback_calls++;
}

static void fake_ui_event(voice_ui_event_type_t type, const char *detail, void *ctx) {
    (void)detail;
    (void)ctx;
    if (type == VOICE_UI_EVENT_RECORDING_START) {
        s_log.ui_recording_start++;
    } else if (type == VOICE_UI_EVENT_RECORDING_END) {
        s_log.ui_recording_end++;
    } else if (type == VOICE_UI_EVENT_CONVERSATION_OFF) {
        s_log.ui_conversation_off++;
    }
}

static voice_conversation_ops_t fake_ops(void) {
    voice_conversation_ops_t ops = {
        .capture_set_enabled = fake_capture_set_enabled,
        .send_audio_start = fake_send_audio_start,
        .send_audio_frame = fake_send_audio_frame,
        .send_audio_end = fake_send_audio_end,
        .send_barge_in = fake_send_barge_in,
        .send_text_input = fake_send_text_input,
        .stop_playback = fake_stop_playback,
        .ui_event = fake_ui_event,
    };
    return ops;
}

static voice_conversation_config_t fast_config(void) {
    voice_conversation_config_t config = voice_conversation_default_config();
    config.vad.start_threshold = 600;
    config.vad.end_threshold = 350;
    config.vad.start_frames = 2;
    config.vad.end_silence_frames = 3;
    config.vad.max_utterance_frames = 100;
    return config;
}

static voice_conversation_config_t full_duplex_config(void) {
    voice_conversation_config_t config = fast_config();
    config.duplex = VOICE_CONV_DUPLEX_FULL;
    config.barge_guard_frames = 3;
    return config;
}

static void feed(voice_conversation_t *conv, int16_t amplitude, int frames) {
    int16_t frame[FRAME_SAMPLES];
    for (int i = 0; i < FRAME_SAMPLES; i++) {
        frame[i] = (i % 2 == 0) ? amplitude : (int16_t)-amplitude;
    }
    for (int i = 0; i < frames; i++) {
        voice_conversation_on_audio_frame(conv, frame, FRAME_SAMPLES);
    }
}

static void reset_log(void) {
    memset(&s_log, 0, sizeof(s_log));
}

/* Drive a full utterance and the reply lifecycle up to PLAYING. */
static void drive_to_playing(voice_conversation_t *conv) {
    voice_conversation_post_event(conv, VOICE_CONV_EVENT_TOGGLE);
    feed(conv, 2000, 2); /* speech start */
    feed(conv, 100, 3);  /* trailing silence -> audio_end -> thinking */
    voice_conversation_post_event(conv, VOICE_CONV_EVENT_TTS_STARTED);
}

static void toggle_on_enters_listening(void) {
    reset_log();
    voice_conversation_t conv;
    voice_conversation_ops_t ops = fake_ops();
    voice_conversation_config_t config = fast_config();
    voice_conversation_init(&conv, &ops, &config);

    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_OFF);
    assert(!voice_conversation_active(&conv));

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TOGGLE);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_LISTENING);
    assert(voice_conversation_active(&conv));
    assert(s_log.capture_enabled);
    assert(s_log.ui_recording_start == 1);
    assert(s_log.text_input_calls == 0);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TOGGLE);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_OFF);
    assert(!s_log.capture_enabled);
    assert(s_log.ui_conversation_off == 1);
    /* Idle listening produced no utterance: nothing was sent. */
    assert(s_log.audio_start_calls == 0);
    assert(s_log.barge_in_calls == 0);
}

static void utterance_streams_frames_then_thinks(void) {
    reset_log();
    voice_conversation_t conv;
    voice_conversation_ops_t ops = fake_ops();
    voice_conversation_config_t config = fast_config();
    voice_conversation_init(&conv, &ops, &config);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TOGGLE);

    /* Quiet frames: captured into pre-roll only, never sent. */
    feed(&conv, 100, 5);
    assert(s_log.audio_start_calls == 0);
    assert(s_log.frame_calls == 0);

    /* Speech start after start_frames voiced frames; pre-roll flushed. */
    feed(&conv, 2000, 2);
    assert(s_log.audio_start_calls == 1);
    assert(s_log.frame_calls > 0);
    int flushed = s_log.frame_calls;
    /* Pre-roll is capped, includes the trigger frames. */
    assert(flushed <= VOICE_CONV_PREROLL_FRAMES);

    /* Ongoing speech streams every frame. */
    feed(&conv, 2000, 10);
    assert(s_log.frame_calls == flushed + 10);

    /* Trailing silence closes the utterance: audio_end + thinking. */
    feed(&conv, 100, 3);
    assert(s_log.audio_end_calls == 1);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_THINKING);
    assert(!s_log.capture_enabled);
    assert(s_log.ui_recording_end == 1);

    /* Frames while thinking are ignored (capture should be off anyway). */
    int frames_before = s_log.frame_calls;
    feed(&conv, 2000, 5);
    assert(s_log.frame_calls == frames_before);

    /* Reply lifecycle: tts start -> playing, tts end -> listening again. */
    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TTS_STARTED);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_PLAYING);
    assert(!s_log.capture_enabled);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TTS_FINISHED);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_LISTENING);
    assert(s_log.capture_enabled);
    assert(s_log.ui_recording_start == 2);

    /* Second utterance works after re-arm. */
    feed(&conv, 2000, 2);
    assert(s_log.audio_start_calls == 2);
}

static void toggle_off_while_playing_stops_everything(void) {
    reset_log();
    voice_conversation_t conv;
    voice_conversation_ops_t ops = fake_ops();
    voice_conversation_config_t config = fast_config();
    voice_conversation_init(&conv, &ops, &config);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TOGGLE);
    feed(&conv, 2000, 2);
    feed(&conv, 100, 3);
    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TTS_STARTED);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_PLAYING);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TOGGLE);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_OFF);
    assert(s_log.stop_playback_calls == 1);
    assert(s_log.barge_in_calls == 1);
    assert(strcmp(s_log.barge_in_reason, "conversation_off") == 0);
    assert(!s_log.capture_enabled);
    assert(s_log.ui_conversation_off == 1);
}

static void mid_utterance_toggle_off_closes_stream(void) {
    reset_log();
    voice_conversation_t conv;
    voice_conversation_ops_t ops = fake_ops();
    voice_conversation_config_t config = fast_config();
    voice_conversation_init(&conv, &ops, &config);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TOGGLE);
    feed(&conv, 2000, 5); /* speech open, no end yet */
    assert(s_log.audio_start_calls == 1);
    assert(s_log.audio_end_calls == 0);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TOGGLE);
    assert(s_log.audio_end_calls == 1);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_OFF);
}

static void thinking_timeout_and_error_rearm_listening(void) {
    reset_log();
    voice_conversation_t conv;
    voice_conversation_ops_t ops = fake_ops();
    voice_conversation_config_t config = fast_config();
    voice_conversation_init(&conv, &ops, &config);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TOGGLE);
    feed(&conv, 2000, 2);
    feed(&conv, 100, 3);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_THINKING);

    /* Reply progress refreshes the hardware watchdog but must not alter the
     * pure conversation state or accidentally reopen capture. */
    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_REPLY_PROGRESS);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_THINKING);
    assert(!s_log.capture_enabled);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_THINKING_TIMEOUT);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_LISTENING);
    assert(s_log.capture_enabled);

    /* Server error while playing also re-arms listening. */
    feed(&conv, 2000, 2);
    feed(&conv, 100, 3);
    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TTS_STARTED);
    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_SERVER_ERROR);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_LISTENING);

    /* A streaming ASR failure can arrive while the utterance is still open.
     * Close that segment and immediately start a clean listening cycle. */
    feed(&conv, 2000, 2);
    int ends_before = s_log.audio_end_calls;
    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_SERVER_ERROR);
    assert(s_log.audio_end_calls == ends_before + 1);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_LISTENING);
    assert(s_log.capture_enabled);
}

static void tts_outside_conversation_is_ignored(void) {
    reset_log();
    voice_conversation_t conv;
    voice_conversation_ops_t ops = fake_ops();
    voice_conversation_config_t config = fast_config();
    voice_conversation_init(&conv, &ops, &config);

    /* Console-initiated say while conversation mode is off. */
    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TTS_STARTED);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_OFF);
    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TTS_FINISHED);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_OFF);
    assert(s_log.capture_calls == 0);
    /* FSM stays OFF but now owns the return-to-idle screen (ui_state's tts_end no
     * longer idles), so the console say returns the device to 点击开始对话. */
    assert(s_log.ui_conversation_off == 1);
}

static void greeting_enters_thinking_and_speaks(void) {
    reset_log();
    voice_conversation_t conv;
    voice_conversation_ops_t ops = fake_ops();
    voice_conversation_config_t config = fast_config();
    config.greeting_text = "你好";
    voice_conversation_init(&conv, &ops, &config);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TOGGLE);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_THINKING);
    assert(s_log.text_input_calls == 1);
    assert(strcmp(s_log.text_input_text, "你好") == 0);
    /* Mic stays off until the greeting reply finishes (half-duplex). */
    assert(!s_log.capture_enabled);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TTS_STARTED);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_PLAYING);
    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TTS_FINISHED);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_LISTENING);
    assert(s_log.capture_enabled);
}

static void playing_interrupts_open_utterance(void) {
    reset_log();
    voice_conversation_t conv;
    voice_conversation_ops_t ops = fake_ops();
    voice_conversation_config_t config = fast_config();
    voice_conversation_init(&conv, &ops, &config);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TOGGLE);
    feed(&conv, 2000, 5); /* utterance open */
    assert(s_log.audio_start_calls == 1);

    /* Server starts speaking (e.g. console say): close utterance, half-duplex. */
    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_TTS_STARTED);
    assert(s_log.audio_end_calls == 1);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_PLAYING);
    assert(!s_log.capture_enabled);
}

static void half_duplex_ignores_speech_during_playback(void) {
    reset_log();
    voice_conversation_t conv;
    voice_conversation_ops_t ops = fake_ops();
    voice_conversation_config_t config = fast_config(); /* HALF */
    voice_conversation_init(&conv, &ops, &config);

    drive_to_playing(&conv);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_PLAYING);
    assert(!s_log.capture_enabled); /* mic off during playback */

    int starts_before = s_log.audio_start_calls;
    feed(&conv, 2000, 20); /* shouting during playback */
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_PLAYING);
    assert(s_log.audio_start_calls == starts_before); /* no barge */
    assert(s_log.barge_in_calls == 0);
}

static void full_duplex_keeps_mic_on_and_barges_on_speech(void) {
    reset_log();
    voice_conversation_t conv;
    voice_conversation_ops_t ops = fake_ops();
    voice_conversation_config_t config = full_duplex_config();
    voice_conversation_init(&conv, &ops, &config);

    drive_to_playing(&conv);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_PLAYING);
    assert(s_log.capture_enabled); /* full-duplex: mic stays on */

    int stop_before = s_log.stop_playback_calls;
    int starts_before = s_log.audio_start_calls;
    feed(&conv, 2000, 5); /* speak during playback -> barge-in */
    assert(s_log.stop_playback_calls == stop_before + 1);
    assert(s_log.barge_in_calls >= 1);
    assert(strcmp(s_log.barge_in_reason, "user_speech") == 0);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_LISTENING);
    assert(s_log.capture_enabled);
    /* A fresh utterance can be captured after the barge re-arms listening. */
    feed(&conv, 2000, 2);
    assert(s_log.audio_start_calls == starts_before + 1);
}

static void full_duplex_guard_window_ignores_early_speech(void) {
    reset_log();
    voice_conversation_t conv;
    voice_conversation_ops_t ops = fake_ops();
    voice_conversation_config_t config = full_duplex_config(); /* guard=3 */
    voice_conversation_init(&conv, &ops, &config);

    drive_to_playing(&conv);
    /* Loud frames within the guard window must not barge. */
    feed(&conv, 2000, 3);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_PLAYING);
    assert(s_log.barge_in_calls == 0);
    /* Past the guard, speech barges in. */
    feed(&conv, 2000, 3);
    assert(s_log.barge_in_calls >= 1);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_LISTENING);
}

static void wake_event_barges_during_playback_any_duplex(void) {
    reset_log();
    voice_conversation_t conv;
    voice_conversation_ops_t ops = fake_ops();
    voice_conversation_config_t config = fast_config(); /* HALF, wake still works */
    voice_conversation_init(&conv, &ops, &config);

    drive_to_playing(&conv);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_PLAYING);

    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_BARGE_DETECTED);
    assert(s_log.stop_playback_calls >= 1);
    assert(strcmp(s_log.barge_in_reason, "wake_word") == 0);
    assert(voice_conversation_state(&conv) == VOICE_CONV_STATE_LISTENING);

    /* Wake event outside playback is a no-op. */
    reset_log();
    voice_conversation_post_event(&conv, VOICE_CONV_EVENT_BARGE_DETECTED);
    assert(s_log.barge_in_calls == 0);
}

int main(void) {
    toggle_on_enters_listening();
    half_duplex_ignores_speech_during_playback();
    full_duplex_keeps_mic_on_and_barges_on_speech();
    full_duplex_guard_window_ignores_early_speech();
    wake_event_barges_during_playback_any_duplex();
    utterance_streams_frames_then_thinks();
    toggle_off_while_playing_stops_everything();
    mid_utterance_toggle_off_closes_stream();
    thinking_timeout_and_error_rearm_listening();
    tts_outside_conversation_is_ignored();
    greeting_enters_thinking_and_speaks();
    playing_interrupts_open_utterance();
    printf("test_conversation: all tests passed\n");
    return 0;
}
