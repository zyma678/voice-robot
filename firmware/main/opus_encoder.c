#include "opus_encoder.h"

#include "sdkconfig.h"

#ifdef CONFIG_VR_UPLINK_CODEC_OPUS
#include "esp_log.h"
#include "opus.h"

#define OPUS_SAMPLE_RATE 16000
#define OPUS_CHANNELS 1

static const char *TAG = "opus_enc";
static OpusEncoder *s_encoder;

esp_err_t voice_opus_encoder_init(void) {
    if (s_encoder) {
        return ESP_OK;
    }
    int err = 0;
    s_encoder = opus_encoder_create(OPUS_SAMPLE_RATE, OPUS_CHANNELS,
                                    OPUS_APPLICATION_VOIP, &err);
    if (err != OPUS_OK || !s_encoder) {
        ESP_LOGE(TAG, "opus_encoder_create failed: %s", opus_strerror(err));
        s_encoder = NULL;
        return ESP_FAIL;
    }
    opus_encoder_ctl(s_encoder, OPUS_SET_BITRATE(CONFIG_VR_OPUS_BITRATE));
    opus_encoder_ctl(s_encoder, OPUS_SET_SIGNAL(OPUS_SIGNAL_VOICE));
    opus_encoder_ctl(s_encoder, OPUS_SET_COMPLEXITY(5));
    ESP_LOGI(TAG, "opus encoder ready: %dHz mono %dbps VOIP",
             OPUS_SAMPLE_RATE, CONFIG_VR_OPUS_BITRATE);
    return ESP_OK;
}

int voice_opus_encode_frame(const int16_t *pcm, size_t sample_count,
                            uint8_t *out, size_t out_capacity) {
    if (!s_encoder || !pcm || !out) {
        return -1;
    }
    int len = opus_encode(s_encoder, pcm, (int)sample_count, out, (int)out_capacity);
    if (len < 0) {
        ESP_LOGW(TAG, "opus_encode failed: %s", opus_strerror(len));
    }
    return len;
}
#else
esp_err_t voice_opus_encoder_init(void) {
    return ESP_OK;
}

int voice_opus_encode_frame(const int16_t *pcm, size_t sample_count,
                            uint8_t *out, size_t out_capacity) {
    (void)pcm;
    (void)sample_count;
    (void)out;
    (void)out_capacity;
    return -1;
}
#endif
