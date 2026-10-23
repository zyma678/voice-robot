#include "vad.h"

#include <string.h>

voice_vad_config_t voice_vad_default_config(void) {
    voice_vad_config_t config = {
        .start_threshold = 600,
        .end_threshold = 350,
        .start_frames = 3,        /* 60ms */
        .end_silence_frames = 22, /* 440ms — snappier turn-taking */
        .max_utterance_frames = 750, /* 15s */
        .end_noise_mult_x10 = 18, /* effective end threshold ≥ 1.8× room noise floor */
    };
    return config;
}

void voice_vad_init(voice_vad_t *vad, const voice_vad_config_t *config) {
    if (!vad) {
        return;
    }
    memset(vad, 0, sizeof(*vad));
    vad->config = config ? *config : voice_vad_default_config();
}

void voice_vad_reset(voice_vad_t *vad) {
    if (!vad) {
        return;
    }
    vad->in_speech = false;
    vad->voiced_streak = 0;
    vad->silence_streak = 0;
    vad->speech_frames = 0;
}

int voice_vad_frame_energy(const int16_t *samples, size_t count) {
    if (!samples || count == 0) {
        return 0;
    }
    int64_t sum = 0;
    for (size_t i = 0; i < count; i++) {
        int32_t v = samples[i];
        sum += v < 0 ? -v : v;
    }
    return (int)(sum / (int64_t)count);
}

voice_vad_result_t voice_vad_process(voice_vad_t *vad, const int16_t *samples, size_t count) {
    if (!vad || !samples || count == 0) {
        return VOICE_VAD_SILENCE;
    }

    int energy = voice_vad_frame_energy(samples, count);

    if (!vad->in_speech) {
        /* Track the room noise floor from the quiet listening window that
         * precedes speech (fast EMA, ~4-frame time constant). This is the
         * baseline the end-of-speech test is made relative to. */
        vad->noise_floor += (energy - vad->noise_floor) / 4;
        if (energy >= vad->config.start_threshold) {
            vad->voiced_streak++;
            if (vad->voiced_streak >= vad->config.start_frames) {
                vad->in_speech = true;
                vad->silence_streak = 0;
                vad->speech_frames = vad->voiced_streak;
                return VOICE_VAD_SPEECH_START;
            }
        } else {
            vad->voiced_streak = 0;
        }
        return VOICE_VAD_SILENCE;
    }

    /* Effective silence threshold: lift the fixed floor to a multiple of the
     * estimated noise floor so a noisy room (energy permanently above
     * end_threshold) can still detect the energy drop when the user stops.
     * Clamped to start_threshold so a frame loud enough to *start* speech is
     * never counted as silence. */
    int end_threshold = vad->config.end_threshold;
    if (vad->config.end_noise_mult_x10 > 0) {
        int relative = vad->noise_floor * vad->config.end_noise_mult_x10 / 10;
        if (relative > end_threshold) {
            end_threshold = relative;
        }
        if (end_threshold > vad->config.start_threshold) {
            end_threshold = vad->config.start_threshold;
        }
    }

    vad->speech_frames++;
    if (energy < end_threshold) {
        vad->silence_streak++;
    } else {
        vad->silence_streak = 0;
    }

    bool trailing_silence = vad->silence_streak >= vad->config.end_silence_frames;
    bool over_max = vad->config.max_utterance_frames > 0 &&
                    vad->speech_frames >= vad->config.max_utterance_frames;
    if (trailing_silence || over_max) {
        voice_vad_reset(vad);
        return VOICE_VAD_SPEECH_END;
    }
    return VOICE_VAD_SPEECH;
}
