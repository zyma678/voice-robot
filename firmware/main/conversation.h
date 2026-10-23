#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "ui_state.h"
#include "vad.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Half-duplex conversation-mode state machine (PRD 11.13).
 *
 * Toggle on -> LISTENING: capture runs, energy VAD segments utterances and
 * streams 20ms PCM frames upstream (audio_start / binary / audio_end).
 * Utterance end -> THINKING: capture paused, waiting for the server reply.
 * tts_start -> PLAYING: capture stays paused (no AEC yet).
 * tts_end -> back to LISTENING automatically. Toggle off from any state
 * stops capture/playback and returns to OFF.
 *
 * Pure logic: side effects go through voice_conversation_ops_t callbacks,
 * so the machine is host-testable. ESP glue lives behind ESP_PLATFORM.
 */

#define VOICE_CONV_FRAME_SAMPLES 320 /* 20ms @ 16kHz mono */
#define VOICE_CONV_PREROLL_FRAMES 6  /* 120ms kept before speech start */

typedef enum {
    VOICE_CONV_STATE_OFF = 0,
    VOICE_CONV_STATE_LISTENING,
    VOICE_CONV_STATE_THINKING,
    VOICE_CONV_STATE_PLAYING,
} voice_conversation_state_t;

typedef enum {
    VOICE_CONV_EVENT_TOGGLE = 0,
    VOICE_CONV_EVENT_TTS_STARTED,
    VOICE_CONV_EVENT_TTS_FINISHED,
    VOICE_CONV_EVENT_SERVER_ERROR,
    /* LLM text or another reply phase made progress while still THINKING.
     * The pure state machine keeps its state; the hardware wrapper uses the
     * event to refresh the bounded thinking watchdog. */
    VOICE_CONV_EVENT_REPLY_PROGRESS,
    VOICE_CONV_EVENT_THINKING_TIMEOUT,
    /* External barge-in trigger during playback (full-duplex). The logic input
     * for a wake-word detector (esp-sr WakeNet, phase 2): when in PLAYING it
     * stops playback and re-arms listening. Signal-agnostic — host tests inject
     * it directly; on device the detector calls it. See ADR 0002. */
    VOICE_CONV_EVENT_BARGE_DETECTED,
} voice_conversation_event_t;

typedef enum {
    /* Mic muted while the speaker plays (validated default, no AEC needed). */
    VOICE_CONV_DUPLEX_HALF = 0,
    /* Mic stays open during playback; an upstream AEC must clean the signal
     * before frames reach this machine, else the bot's own voice self-triggers.
     * Phase 3 (esp-sr AFE) on device; phase 1 uses the browser AEC in the sim. */
    VOICE_CONV_DUPLEX_FULL,
} voice_conversation_duplex_t;

typedef struct {
    void (*capture_set_enabled)(bool enabled, void *ctx);
    void (*send_audio_start)(void *ctx);
    void (*send_audio_frame)(const int16_t *samples, size_t count, void *ctx);
    void (*send_audio_end)(void *ctx);
    void (*send_barge_in)(const char *reason, void *ctx);
    void (*send_text_input)(const char *text, void *ctx);
    void (*stop_playback)(void *ctx);
    void (*ui_event)(voice_ui_event_type_t type, const char *detail, void *ctx);
    void *ctx;
} voice_conversation_ops_t;

typedef struct {
    voice_vad_config_t vad;
    const char *greeting_text; /* NULL or "" disables the spoken greeting */
    voice_conversation_duplex_t duplex; /* default HALF */
    int barge_guard_frames; /* full-duplex: ignore barge for N frames after playback starts */
} voice_conversation_config_t;

typedef struct {
    voice_conversation_ops_t ops;
    voice_conversation_config_t config;
    voice_conversation_state_t state;
    voice_vad_t vad;
    bool utterance_open; /* audio_start sent, audio_end pending */
    int playing_frames;  /* frames seen since entering PLAYING (guard window) */
    int16_t preroll[VOICE_CONV_PREROLL_FRAMES][VOICE_CONV_FRAME_SAMPLES];
    size_t preroll_lens[VOICE_CONV_PREROLL_FRAMES];
    size_t preroll_count;
    size_t preroll_head;
} voice_conversation_t;

voice_conversation_config_t voice_conversation_default_config(void);
void voice_conversation_init(voice_conversation_t *conv,
                             const voice_conversation_ops_t *ops,
                             const voice_conversation_config_t *config);
voice_conversation_state_t voice_conversation_state(const voice_conversation_t *conv);
bool voice_conversation_active(const voice_conversation_t *conv);
void voice_conversation_post_event(voice_conversation_t *conv,
                                   voice_conversation_event_t event);
void voice_conversation_on_audio_frame(voice_conversation_t *conv,
                                       const int16_t *samples,
                                       size_t count);

#ifdef ESP_PLATFORM
#include "esp_err.h"

/** Wire the singleton machine to ws/audio/UI and start the thinking timer. */
esp_err_t voice_conversation_hw_init(void);
/** Thread-safe entry points (button task / ws task / capture task). */
void voice_conversation_hw_toggle(void);
/** Defer a toggle to the conversation task; safe to call from LVGL callbacks. */
void voice_conversation_hw_toggle_deferred(void);
void voice_conversation_hw_post(voice_conversation_event_t event);
void voice_conversation_hw_on_frame(const int16_t *samples, size_t count);
bool voice_conversation_hw_active(void);
#endif

#ifdef __cplusplus
}
#endif
