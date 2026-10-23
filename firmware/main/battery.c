#include "battery.h"

#include "driver/i2c_master.h"
#include "esp_log.h"
#include "i2c_bus.h"

#define AXP2101_ADDR        0x34
#define AXP2101_STATUS1     0x00
#define AXP2101_GAUGE_CTRL  0xA2
#define AXP2101_GAUGE_PCT   0xA4

static const char *TAG = "battery";
static i2c_master_dev_handle_t s_dev;

static esp_err_t reg_read(uint8_t reg, uint8_t *val) {
    if (!s_dev) return ESP_ERR_INVALID_STATE;
    return i2c_master_transmit_receive(s_dev, &reg, 1, val, 1, 100);
}

static esp_err_t reg_write(uint8_t reg, uint8_t val) {
    if (!s_dev) return ESP_ERR_INVALID_STATE;
    uint8_t buf[2] = {reg, val};
    return i2c_master_transmit(s_dev, buf, 2, 100);
}

void battery_init(void) {
    i2c_master_bus_handle_t bus = voice_i2c_bus_get();
    if (!bus) {
        ESP_LOGW(TAG, "no i2c bus");
        return;
    }
    i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = AXP2101_ADDR,
        .scl_speed_hz = 100000,
    };
    if (i2c_master_bus_add_device(bus, &cfg, &s_dev) != ESP_OK) {
        ESP_LOGW(TAG, "axp2101 not found at 0x%02x", AXP2101_ADDR);
        s_dev = NULL;
        return;
    }
    uint8_t gc;
    if (reg_read(AXP2101_GAUGE_CTRL, &gc) == ESP_OK && !(gc & 0x01)) {
        reg_write(AXP2101_GAUGE_CTRL, gc | 0x01);
        ESP_LOGI(TAG, "fuel gauge enabled");
    }
    ESP_LOGI(TAG, "axp2101 pmu ready");
}

int battery_get_percent(void) {
    uint8_t pct;
    if (reg_read(AXP2101_GAUGE_PCT, &pct) != ESP_OK) return -1;
    return pct > 100 ? 100 : (int)pct;
}

bool battery_is_charging(void) {
    uint8_t st;
    if (reg_read(AXP2101_STATUS1, &st) != ESP_OK) return false;
    return (st >> 5) & 0x01;
}
