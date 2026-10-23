#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "vad.h"

#define FRAME_SAMPLES 320

static void fill_frame(int16_t *frame, int16_t amplitude) {
    for (int i = 0; i < FRAME_SAMPLES; i++) {
        frame[i] = (i % 2 == 0) ? amplitude : (int16_t)-amplitude;
    }
}

static voice_vad_config_t test_config(void) {
    voice_vad_config_t config = {
        .start_threshold = 600,
        .end_threshold = 350,
        .start_frames = 3,
        .end_silence_frames = 5,
        .max_utterance_frames = 50,
        .end_noise_mult_x10 = 0, /* adaptation off: legacy fixed-threshold behavior */
    };
    return config;
}

static void energy_is_mean_abs(void) {
    int16_t frame[FRAME_SAMPLES];
    fill_frame(frame, 1000);
    assert(voice_vad_frame_energy(frame, FRAME_SAMPLES) == 1000);

    memset(frame, 0, sizeof(frame));
    assert(voice_vad_frame_energy(frame, FRAME_SAMPLES) == 0);

    assert(voice_vad_frame_energy(NULL, FRAME_SAMPLES) == 0);
    assert(voice_vad_frame_energy(frame, 0) == 0);
}

static void silence_never_starts_speech(void) {
    voice_vad_config_t config = test_config();
    voice_vad_t vad;
    voice_vad_init(&vad, &config);

    int16_t quiet[FRAME_SAMPLES];
    fill_frame(quiet, 100);
    for (int i = 0; i < 100; i++) {
        assert(voice_vad_process(&vad, quiet, FRAME_SAMPLES) == VOICE_VAD_SILENCE);
    }
}

static void speech_starts_after_consecutive_voiced_frames(void) {
    voice_vad_config_t config = test_config();
    voice_vad_t vad;
    voice_vad_init(&vad, &config);

    int16_t loud[FRAME_SAMPLES];
    int16_t quiet[FRAME_SAMPLES];
    fill_frame(loud, 2000);
    fill_frame(quiet, 100);

    /* A blip shorter than start_frames must not trigger. */
    assert(voice_vad_process(&vad, loud, FRAME_SAMPLES) == VOICE_VAD_SILENCE);
    assert(voice_vad_process(&vad, loud, FRAME_SAMPLES) == VOICE_VAD_SILENCE);
    assert(voice_vad_process(&vad, quiet, FRAME_SAMPLES) == VOICE_VAD_SILENCE);

    assert(voice_vad_process(&vad, loud, FRAME_SAMPLES) == VOICE_VAD_SILENCE);
    assert(voice_vad_process(&vad, loud, FRAME_SAMPLES) == VOICE_VAD_SILENCE);
    assert(voice_vad_process(&vad, loud, FRAME_SAMPLES) == VOICE_VAD_SPEECH_START);
    assert(voice_vad_process(&vad, loud, FRAME_SAMPLES) == VOICE_VAD_SPEECH);
}

static void utterance_ends_after_trailing_silence(void) {
    voice_vad_config_t config = test_config();
    voice_vad_t vad;
    voice_vad_init(&vad, &config);

    int16_t loud[FRAME_SAMPLES];
    int16_t quiet[FRAME_SAMPLES];
    fill_frame(loud, 2000);
    fill_frame(quiet, 100);

    for (int i = 0; i < 3; i++) {
        voice_vad_process(&vad, loud, FRAME_SAMPLES);
    }
    assert(vad.in_speech);

    /* Mid-utterance pause shorter than end_silence_frames keeps speech open. */
    for (int i = 0; i < 4; i++) {
        assert(voice_vad_process(&vad, quiet, FRAME_SAMPLES) == VOICE_VAD_SPEECH);
    }
    assert(voice_vad_process(&vad, loud, FRAME_SAMPLES) == VOICE_VAD_SPEECH);

    for (int i = 0; i < 4; i++) {
        assert(voice_vad_process(&vad, quiet, FRAME_SAMPLES) == VOICE_VAD_SPEECH);
    }
    assert(voice_vad_process(&vad, quiet, FRAME_SAMPLES) == VOICE_VAD_SPEECH_END);
    assert(!vad.in_speech);

    /* Back to silence; a new utterance can start again. */
    assert(voice_vad_process(&vad, quiet, FRAME_SAMPLES) == VOICE_VAD_SILENCE);
}

static void max_utterance_forces_end(void) {
    voice_vad_config_t config = test_config();
    voice_vad_t vad;
    voice_vad_init(&vad, &config);

    int16_t loud[FRAME_SAMPLES];
    fill_frame(loud, 2000);

    voice_vad_result_t result = VOICE_VAD_SILENCE;
    int frames = 0;
    for (int i = 0; i < 200; i++) {
        result = voice_vad_process(&vad, loud, FRAME_SAMPLES);
        frames++;
        if (result == VOICE_VAD_SPEECH_END) {
            break;
        }
    }
    assert(result == VOICE_VAD_SPEECH_END);
    assert(frames <= 50);
}

/* In a noisy room the ambient energy sits permanently above the fixed
 * end_threshold, so without adaptation trailing silence never accumulates and
 * the utterance hangs until max_utterance_frames. With end_noise_mult_x10 the
 * effective threshold tracks the noise floor and end-of-speech fires normally. */
static void noisy_room_still_ends(void) {
    voice_vad_config_t config = test_config();
    config.end_noise_mult_x10 = 18; /* 1.8× noise floor */
    config.max_utterance_frames = 0; /* prove the silence path ends it, not the cap */
    voice_vad_t vad;
    voice_vad_init(&vad, &config);

    int16_t loud[FRAME_SAMPLES];
    int16_t noisy[FRAME_SAMPLES];
    fill_frame(loud, 2000);
    fill_frame(noisy, 400); /* ambient floor above end_threshold(350) but below start(600) */

    /* Pre-speech: let the noise floor settle near 400 without starting speech. */
    for (int i = 0; i < 30; i++) {
        assert(voice_vad_process(&vad, noisy, FRAME_SAMPLES) == VOICE_VAD_SILENCE);
    }

    for (int i = 0; i < 3; i++) {
        voice_vad_process(&vad, loud, FRAME_SAMPLES);
    }
    assert(vad.in_speech);

    /* User stops: energy falls back to the 400 floor. With the old fixed 350
     * threshold this would count as voiced forever; now it ends after the
     * trailing-silence window. */
    voice_vad_result_t result = VOICE_VAD_SILENCE;
    for (int i = 0; i < config.end_silence_frames + 2; i++) {
        result = voice_vad_process(&vad, noisy, FRAME_SAMPLES);
        if (result == VOICE_VAD_SPEECH_END) {
            break;
        }
    }
    assert(result == VOICE_VAD_SPEECH_END);
    assert(!vad.in_speech);
}

static void reset_clears_state(void) {
    voice_vad_config_t config = test_config();
    voice_vad_t vad;
    voice_vad_init(&vad, &config);

    int16_t loud[FRAME_SAMPLES];
    fill_frame(loud, 2000);
    for (int i = 0; i < 3; i++) {
        voice_vad_process(&vad, loud, FRAME_SAMPLES);
    }
    assert(vad.in_speech);

    voice_vad_reset(&vad);
    assert(!vad.in_speech);
    assert(vad.voiced_streak == 0);
    assert(vad.silence_streak == 0);
    assert(vad.speech_frames == 0);
}

int main(void) {
    energy_is_mean_abs();
    silence_never_starts_speech();
    speech_starts_after_consecutive_voiced_frames();
    utterance_ends_after_trailing_silence();
    noisy_room_still_ends();
    max_utterance_forces_end();
    reset_clears_state();
    printf("test_vad: all tests passed\n");
    return 0;
}
