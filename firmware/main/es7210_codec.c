#include "es7210_codec.h"

#ifdef ESP_PLATFORM
#include "driver/i2c_master.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "i2c_bus.h"

/* ES7210 4-channel ADC (mic). 7-bit I2C address 0x40 on this board.
 * Register map + init sequence mirror Espressif's esp_codec_dev es7210 driver,
 * specialized here for: slave mode, single MIC1, I2S-normal, 16-bit, 16kHz. */
#define ES7210_I2C_ADDR 0x40
#define ES7210_I2C_TIMEOUT_MS 1000

#define ES7210_RESET_REG00 0x00
#define ES7210_CLOCK_OFF_REG01 0x01
#define ES7210_MAINCLK_REG02 0x02
#define ES7210_POWER_DOWN_REG06 0x06
#define ES7210_OSR_REG07 0x07
#define ES7210_MODE_CONFIG_REG08 0x08
#define ES7210_TIME_CONTROL0_REG09 0x09
#define ES7210_TIME_CONTROL1_REG0A 0x0A
#define ES7210_SDP_INTERFACE1_REG11 0x11
#define ES7210_SDP_INTERFACE2_REG12 0x12
#define ES7210_ADC34_HPF2_REG20 0x20
#define ES7210_ADC34_HPF1_REG21 0x21
#define ES7210_ADC12_HPF1_REG22 0x22
#define ES7210_ADC12_HPF2_REG23 0x23
#define ES7210_ANALOG_REG40 0x40
#define ES7210_MIC12_BIAS_REG41 0x41
#define ES7210_MIC34_BIAS_REG42 0x42
#define ES7210_MIC1_GAIN_REG43 0x43
#define ES7210_MIC1_POWER_REG47 0x47
#define ES7210_MIC2_POWER_REG48 0x48
#define ES7210_MIC3_POWER_REG49 0x49
#define ES7210_MIC4_POWER_REG4A 0x4A
#define ES7210_MIC12_POWER_REG4B 0x4B
#define ES7210_MIC34_POWER_REG4C 0x4C

/* PGA gain code (0x0f field of REGn). ES7210 map: 0x00=0dB, +3dB/step,
 * 0x0a=+30dB ... 0x0e=+37.5dB (max). Use max analog gain: amplifying in the
 * analog domain (before the ADC) lifts the signal above the ADC quantization
 * floor, improving SNR — unlike digital make-up gain which scales noise too. */
#define ES7210_MIC1_GAIN 0x0E

static const char *TAG = "es7210";
static i2c_master_dev_handle_t s_i2c_dev;
static bool s_ready;

static esp_err_t es7210_write_reg(uint8_t reg, uint8_t value) {
    uint8_t buf[2] = {reg, value};
    return i2c_master_transmit(s_i2c_dev, buf, sizeof(buf), ES7210_I2C_TIMEOUT_MS);
}

static esp_err_t es7210_read_reg(uint8_t reg, uint8_t *value) {
    return i2c_master_transmit_receive(s_i2c_dev, &reg, 1, value, 1,
                                       ES7210_I2C_TIMEOUT_MS);
}

/* read-modify-write: keep bits outside update_mask, set masked bits from data. */
static esp_err_t es7210_update_bits(uint8_t reg, uint8_t update_mask, uint8_t data) {
    uint8_t regv = 0;
    esp_err_t ret = es7210_read_reg(reg, &regv);
    if (ret != ESP_OK) {
        return ret;
    }
    regv = (regv & (~update_mask)) | (update_mask & data);
    return es7210_write_reg(reg, regv);
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
        .device_address = ES7210_I2C_ADDR,
        .scl_speed_hz = 400000,
    };
    return i2c_master_bus_add_device(bus, &dev_cfg, &s_i2c_dev);
}

/* Power on MIC1 only, route it to the I2S-normal left slot, set PGA gain.
 * Called both during initial config and on (re)start, like the vendor driver. */
static esp_err_t es7210_mic1_select(void) {
    esp_err_t ret = ESP_OK;
    /* Clear the per-channel enable bit on all four channels first. */
    for (uint8_t i = 0; i < 4; i++) {
        ret |= es7210_update_bits(ES7210_MIC1_GAIN_REG43 + i, 0x10, 0x00);
    }
    ret |= es7210_write_reg(ES7210_MIC12_POWER_REG4B, 0xff);
    ret |= es7210_write_reg(ES7210_MIC34_POWER_REG4C, 0xff);
    /* MIC1: ungate its clock, power the MIC12 block, enable + set gain. */
    ret |= es7210_update_bits(ES7210_CLOCK_OFF_REG01, 0x0b, 0x00);
    ret |= es7210_write_reg(ES7210_MIC12_POWER_REG4B, 0x00);
    ret |= es7210_update_bits(ES7210_MIC1_GAIN_REG43, 0x10, 0x10);
    ret |= es7210_update_bits(ES7210_MIC1_GAIN_REG43, 0x0f, ES7210_MIC1_GAIN);
    /* Single mic (< 3) → non-TDM, standard I2S frame (MIC1 on left slot). */
    ret |= es7210_write_reg(ES7210_SDP_INTERFACE2_REG12, 0x00);
    return ret;
}

esp_err_t voice_es7210_init_16k(void) {
    if (s_ready) {
        return ESP_OK;
    }
    esp_err_t ret = init_i2c();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2c init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    /* Probe: REG00 should be readable if the chip is present. */
    uint8_t reg00 = 0;
    ret = es7210_read_reg(ES7210_RESET_REG00, &reg00);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "es7210 not responding at i2c 0x%02x: %s",
                 ES7210_I2C_ADDR, esp_err_to_name(ret));
        return ret;
    }
    ESP_LOGI(TAG, "es7210 present (reg00=0x%02x)", reg00);

    /* ---- config (vendor es7210_open, slave mode) ---- */
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_RESET_REG00, 0xff), TAG, "reset");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_RESET_REG00, 0x41), TAG, "reset2");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_CLOCK_OFF_REG01, 0x3f), TAG, "clk off");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_TIME_CONTROL0_REG09, 0x30), TAG, "t0");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_TIME_CONTROL1_REG0A, 0x30), TAG, "t1");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_ADC12_HPF2_REG23, 0x2a), TAG, "hpf");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_ADC12_HPF1_REG22, 0x0a), TAG, "hpf");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_ADC34_HPF2_REG20, 0x0a), TAG, "hpf");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_ADC34_HPF1_REG21, 0x2a), TAG, "hpf");
    /* Slave mode (ESP32 supplies MCLK/BCLK/WS). */
    ESP_RETURN_ON_ERROR(es7210_update_bits(ES7210_MODE_CONFIG_REG08, 0x01, 0x00), TAG, "slave");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_ANALOG_REG40, 0x43), TAG, "analog");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_MIC12_BIAS_REG41, 0x70), TAG, "bias12");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_MIC34_BIAS_REG42, 0x70), TAG, "bias34");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_OSR_REG07, 0x20), TAG, "osr");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_MAINCLK_REG02, 0xc1), TAG, "mainclk");
    ESP_RETURN_ON_ERROR(es7210_mic1_select(), TAG, "mic select");

    /* ---- format: 16-bit, I2S-normal (vendor es7210_set_fs, slave→no clkdiv) ---- */
    ESP_RETURN_ON_ERROR(es7210_update_bits(ES7210_SDP_INTERFACE1_REG11, 0xe0, 0x60), TAG, "bits16");
    ESP_RETURN_ON_ERROR(es7210_update_bits(ES7210_SDP_INTERFACE1_REG11, 0x03, 0x00), TAG, "fmt i2s");

    /* off_reg: REG01 after mic-select gating, replayed by start. */
    uint8_t off_reg = 0x34;
    (void)es7210_read_reg(ES7210_CLOCK_OFF_REG01, &off_reg);

    /* ---- start (vendor es7210_start) ---- */
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_CLOCK_OFF_REG01, off_reg), TAG, "start clk");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_POWER_DOWN_REG06, 0x00), TAG, "pwr up");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_ANALOG_REG40, 0x43), TAG, "analog2");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_MIC1_POWER_REG47, 0x08), TAG, "mic1 pwr");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_MIC2_POWER_REG48, 0x08), TAG, "mic2 pwr");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_MIC3_POWER_REG49, 0x08), TAG, "mic3 pwr");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_MIC4_POWER_REG4A, 0x08), TAG, "mic4 pwr");
    ESP_RETURN_ON_ERROR(es7210_mic1_select(), TAG, "mic select2");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_ANALOG_REG40, 0x43), TAG, "analog3");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_RESET_REG00, 0x71), TAG, "csm on");
    ESP_RETURN_ON_ERROR(es7210_write_reg(ES7210_RESET_REG00, 0x41), TAG, "csm run");

    s_ready = true;
    ESP_LOGI(TAG, "es7210 ready: MIC1, 16kHz/16bit, I2S-normal, slave, +37.5dB");
    return ESP_OK;
}

bool voice_es7210_ready(void) {
    return s_ready;
}
#endif
