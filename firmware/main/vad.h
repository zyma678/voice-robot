#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Energy-threshold VAD over 20ms PCM frames (16k/16bit/mono).
 * Pure logic: host-testable, no ESP dependencies.
 */

typedef struct {
    int start_threshold;       /* mean |sample| at/above which a frame counts as voiced */
    int end_threshold;         /* mean |sample| below which a frame counts as silence (floor) */
    int start_frames;          /* consecutive voiced frames required to enter speech */
    int end_silence_frames;    /* consecutive silent frames required to end the utterance */
    int max_utterance_frames;  /* hard cap forcing utterance end (0 = unlimited) */
    /* Adaptive end-of-speech: the effective silence threshold is lifted to
     * (estimated room noise floor) * end_noise_mult_x10 / 10, clamped to
     * [end_threshold, start_threshold]. Keeps end detection robust in noisy
     * rooms where a fixed end_threshold would never let silence accumulate.
     * 0 disables adaptation (effective threshold == end_threshold). */
    int end_noise_mult_x10;
} voice_vad_config_t;

typedef enum {
    VOICE_VAD_SILENCE = 0,  /* outside speech */
    VOICE_VAD_SPEECH_START, /* this frame confirmed the start of an utterance */
    VOICE_VAD_SPEECH,       /* inside an utterance */
    VOICE_VAD_SPEECH_END,   /* this frame confirmed the end of the utterance */
} voice_vad_result_t;

typedef struct {
    voice_vad_config_t config;
    bool in_speech;
    int voiced_streak;
    int silence_streak;
    int speech_frames;
    int noise_floor;       /* EMA of frame energy estimated while not in speech */
} voice_vad_t;

voice_vad_config_t voice_vad_default_config(void);
void voice_vad_init(voice_vad_t *vad, const voice_vad_config_t *config);
void voice_vad_reset(voice_vad_t *vad);
int voice_vad_frame_energy(const int16_t *samples, size_t count);
voice_vad_result_t voice_vad_process(voice_vad_t *vad, const int16_t *samples, size_t count);

#ifdef __cplusplus
}
#endif
