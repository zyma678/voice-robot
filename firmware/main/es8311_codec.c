#include "es8311_codec.h"

#ifdef ESP_PLATFORM
#include "driver/i2c_master.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"

#include "i2c_bus.h"

#define ES8311_I2C_ADDR 0x18
#define ES8311_I2C_TIMEOUT_MS 1000

/* Persist the last applied playback volume so it survives reboots. Shares the
 * "vrcfg" NVS namespace used for the ws-env selection (see ws_client.c). */
#define ES8311_VOL_NVS_NS "vrcfg"
#define ES8311_VOL_NVS_KEY "volume"
#define ES8311_VOL_DEFAULT 63

#define ES8311_RESET_REG00 0x00
#define ES8311_CLK_MANAGER_REG01 0x01
#define ES8311_CLK_MANAGER_REG02 0x02
#define ES8311_CLK_MANAGER_REG03 0x03
#define ES8311_CLK_MANAGER_REG04 0x04
#define ES8311_CLK_MANAGER_REG05 0x05
#define ES8311_CLK_MANAGER_REG06 0x06
#define ES8311_CLK_MANAGER_REG07 0x07
#define ES8311_CLK_MANAGER_REG08 0x08
#define ES8311_SDPIN_REG09 0x09
#define ES8311_SDPOUT_REG0A 0x0A
#define ES8311_SYSTEM_REG0D 0x0D
#define ES8311_SYSTEM_REG0E 0x0E
#define ES8311_SYSTEM_REG12 0x12
#define ES8311_SYSTEM_REG13 0x13
#define ES8311_SYSTEM_REG14 0x14
#define ES8311_ADC_REG17 0x17
#define ES8311_ADC_REG1C 0x1C
#define ES8311_DAC_REG31 0x31
#define ES8311_DAC_REG32 0x32
#define ES8311_DAC_REG37 0x37
#define ES8311_CHD1_REGFD 0xFD
#define ES8311_CHD2_REGFE 0xFE
#define ES8311_CHVER_REGFF 0xFF

static const char *TAG = "es8311";
static i2c_master_dev_handle_t s_i2c_dev;
static bool s_codec_ready;
static int s_volume = 80;  /* last applied volume (0-100), mirrors REG32 */

static esp_err_t es8311_write_reg(uint8_t reg, uint8_t value) {
    uint8_t buf[2] = {reg, value};
    return i2c_master_transmit(s_i2c_dev, buf, sizeof(buf), ES8311_I2C_TIMEOUT_MS);
}

static esp_err_t es8311_read_reg(uint8_t reg, uint8_t *value) {
    return i2c_master_transmit_receive(s_i2c_dev,
                                       &reg,
                                       1,
                                       value,
                                       1,
                                       ES8311_I2C_TIMEOUT_MS);
}

static esp_err_t init_i2c(void) {
    if (s_i2c_dev) {
        return ESP_OK;
    }

    i2c_master_bus_handle_t bus = voice_i2c_bus_get();
    if (!bus) {
        return ESP_FAIL;
    }

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = ES8311_I2C_ADDR,
        .scl_speed_hz = 400000,
    };
    return i2c_master_bus_add_device(bus, &dev_cfg, &s_i2c_dev);
}

esp_err_t voice_es8311_set_volume(int volume) {
    if (volume < 0) {
        volume = 0;
    } else if (volume > 100) {
        volume = 100;
    }

    // ES8311 DAC volume (REG32) is 0.5 dB/step: 0x00 ≈ mute, 0xBF (191) = 0 dB,
    // and 0xBF..0xFF is a POSITIVE digital-gain region (up to +32 dB at 0xFF).
    // The old map (volume*256/100 - 1) ran 0-100 across the full 0x00..0xFF, so
    // any volume above ~74 pushed the codec into positive gain and volume=100
    // applied +32 dB on top of near-full-scale TTS PCM → internal digital
    // clipping → harsh/distorted playback at high volume.
    // Cap the loudest setting at 0 dB (0xBF) and map 1..100 linearly onto
    // 0x6F(111)≈-40 dB .. 0xBF(191)=0 dB. This keeps the previous mid-range feel
    // (e.g. boot default 63 → ~161 ≈ -15 dB, matching the old 0xA0) while
    // removing the clipping-prone positive-gain region entirely.
    uint8_t reg32;
    if (volume == 0) {
        reg32 = 0;
    } else {
        reg32 = (uint8_t)(111 + ((volume - 1) * 80) / 99);  // 0x6F..0xBF
    }
    esp_err_t ret = es8311_write_reg(ES8311_DAC_REG32, reg32);
    if (ret == ESP_OK) {
        s_volume = volume;
        ESP_LOGI(TAG, "volume set to %d (reg32=0x%02x)", volume, reg32);
        /* Persist so the level is restored after a reboot. Best-effort: a NVS
         * failure must not fail the volume change itself. */
        nvs_handle_t h;
        if (nvs_open(ES8311_VOL_NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
            nvs_set_u8(h, ES8311_VOL_NVS_KEY, (uint8_t)volume);
            nvs_commit(h);
            nvs_close(h);
        }
    }
    return ret;
}

/* Read the persisted volume (0-100), or ES8311_VOL_DEFAULT when unset/invalid. */
static int es8311_volume_load(void) {
    nvs_handle_t h;
    if (nvs_open(ES8311_VOL_NVS_NS, NVS_READONLY, &h) != ESP_OK) {
        return ES8311_VOL_DEFAULT;
    }
    uint8_t stored = 0;
    esp_err_t ret = nvs_get_u8(h, ES8311_VOL_NVS_KEY, &stored);
    nvs_close(h);
    if (ret != ESP_OK || stored > 100) {
        return ES8311_VOL_DEFAULT;
    }
    return (int)stored;
}

int voice_es8311_get_volume(void) {
    return s_volume;
}

esp_err_t voice_es8311_init_16k(void) {
    if (s_codec_ready) {
        return ESP_OK;
    }

    esp_err_t ret = init_i2c();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2c init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    uint8_t id1 = 0;
    uint8_t id2 = 0;
    uint8_t ver = 0;
    ret = es8311_read_reg(ES8311_CHD1_REGFD, &id1);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "es8311 not responding at i2c addr 0x%02x: %s",
                 ES8311_I2C_ADDR, esp_err_to_name(ret));
        return ret;
    }
    (void)es8311_read_reg(ES8311_CHD2_REGFE, &id2);
    (void)es8311_read_reg(ES8311_CHVER_REGFF, &ver);
    ESP_LOGI(TAG, "chip id: fd=0x%02x fe=0x%02x ff=0x%02x", id1, id2, ver);

    // Register sequence follows Espressif's ES8311 component for the actual
    // MCLK the I2S driver emits: 4.096 MHz (256 × 16 kHz, the I2S_STD default
    // mclk_multiple), 16 kHz sample rate, 16-bit I2S, codec in slave mode.
    // REG02 pre_div must be 1 (0x00) for a 4.096 MHz MCLK; the previous 0x40
    // (pre_div=3) was tuned for a 12.288 MHz MCLK we never produce, so the
    // codec ran ~3× slow → low-pitched, distorted playback.
    ESP_ERROR_CHECK_WITHOUT_ABORT(es8311_write_reg(ES8311_RESET_REG00, 0x1F));
    vTaskDelay(pdMS_TO_TICKS(20));
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_RESET_REG00, 0x00), TAG, "reset");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_RESET_REG00, 0x80), TAG, "power on");

    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_CLK_MANAGER_REG01, 0x3F), TAG, "clock 01");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_CLK_MANAGER_REG02, 0x00), TAG, "clock 02");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_CLK_MANAGER_REG03, 0x10), TAG, "clock 03");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_CLK_MANAGER_REG04, 0x10), TAG, "clock 04");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_CLK_MANAGER_REG05, 0x00), TAG, "clock 05");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_CLK_MANAGER_REG06, 0x03), TAG, "clock 06");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_CLK_MANAGER_REG07, 0x00), TAG, "clock 07");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_CLK_MANAGER_REG08, 0xFF), TAG, "clock 08");

    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_SDPIN_REG09, 0x0C), TAG, "sdpin");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_SDPOUT_REG0A, 0x0C), TAG, "sdpout");

    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_SYSTEM_REG0D, 0x01), TAG, "system 0d");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_SYSTEM_REG0E, 0x02), TAG, "system 0e");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_SYSTEM_REG12, 0x00), TAG, "dac power");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_SYSTEM_REG13, 0x10), TAG, "hp drive");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_ADC_REG1C, 0x6A), TAG, "adc eq");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_DAC_REG37, 0x08), TAG, "dac eq");

    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_ADC_REG17, 0xC8), TAG, "adc gain");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_SYSTEM_REG14, 0x1A), TAG, "analog mic");
    // Boot to the last volume the user set (persisted in NVS), or a clearly
    // audible default with TTS headroom on first boot. Server/console can still
    // adjust it at runtime via set_volume (which re-persists).
    ESP_RETURN_ON_ERROR(voice_es8311_set_volume(es8311_volume_load()), TAG, "volume");
    ESP_RETURN_ON_ERROR(es8311_write_reg(ES8311_DAC_REG31, 0x00), TAG, "unmute");

    s_codec_ready = true;
    ESP_LOGI(TAG, "es8311 ready: 4.096MHz MCLK (256x), 16kHz, 16bit, i2s slave");
    return ESP_OK;
}
#endif
