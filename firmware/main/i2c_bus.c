#include "i2c_bus.h"

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#define VOICE_I2C_PORT 0
#define VOICE_I2C_SDA 15
#define VOICE_I2C_SCL 14

static const char *TAG = "i2c_bus";

static i2c_master_bus_handle_t s_bus;
static SemaphoreHandle_t s_lock;

static SemaphoreHandle_t lock_get(void) {
    static StaticSemaphore_t buf;
    if (!s_lock) {
        s_lock = xSemaphoreCreateMutexStatic(&buf);
    }
    return s_lock;
}

i2c_master_bus_handle_t voice_i2c_bus_get(void) {
    xSemaphoreTake(lock_get(), portMAX_DELAY);
    if (!s_bus) {
        i2c_master_bus_config_t bus_cfg = {
            .i2c_port = VOICE_I2C_PORT,
            .sda_io_num = VOICE_I2C_SDA,
            .scl_io_num = VOICE_I2C_SCL,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .flags.enable_internal_pullup = true,
        };
        esp_err_t ret = i2c_new_master_bus(&bus_cfg, &s_bus);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "i2c bus create failed: %s", esp_err_to_name(ret));
            s_bus = NULL;
        } else {
            ESP_LOGI(TAG, "shared i2c bus ready: port=%d SDA=%d SCL=%d",
                     VOICE_I2C_PORT, VOICE_I2C_SDA, VOICE_I2C_SCL);
        }
    }
    i2c_master_bus_handle_t bus = s_bus;
    xSemaphoreGive(s_lock);
    return bus;
}
