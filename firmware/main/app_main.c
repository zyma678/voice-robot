#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "audio_capture.h"
#include "audio_playback.h"
#include "battery.h"
#include "ble_gateway.h"
#include "conversation.h"
#include "sdkconfig.h"
#include "ui_screen.h"
#include "wifi_client.h"
#include "ws_client.h"

#define BOOT_BUTTON_GPIO 0
#define BOOT_POLL_MS 20
#define BOOT_DEBOUNCE_MS 30
#define BOOT_LONG_PRESS_MS 3000

static const char *TAG = "app";

static void boot_trace(const char *msg) {
    if (!msg) {
        return;
    }
    esp_rom_printf("[boot_trace] %s\r\n", msg);
}

void app_main(void) {
    boot_trace("app_main_enter");
    ESP_LOGI(TAG, "voice-robot M0 firmware booting");
    ESP_LOGI(TAG, "device_id=%s ws_url=%s", CONFIG_VR_DEVICE_ID, CONFIG_VR_WS_URL);
    esp_rom_printf("[boot_trace] device_id=%s ws_url=%s\r\n",
                   CONFIG_VR_DEVICE_ID, CONFIG_VR_WS_URL);

    // Initialize NVS early: the audio codec init (below) restores the persisted
    // playback volume from NVS, and that must happen before Wi-Fi (which also
    // calls nvs_flash_init — harmless, the call is idempotent). Without this the
    // codec read the default volume on every boot because NVS wasn't ready yet.
    boot_trace("nvs_init_begin");
    esp_err_t nvs_ret = nvs_flash_init();
    if (nvs_ret == ESP_ERR_NVS_NO_FREE_PAGES || nvs_ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_ret = nvs_flash_init();
    }
    if (nvs_ret != ESP_OK) {
        ESP_LOGW(TAG, "nvs init failed (%s); persisted volume/config unavailable",
                 esp_err_to_name(nvs_ret));
        boot_trace("nvs_init_failed");
    } else {
        boot_trace("nvs_init_ok");
    }

    boot_trace("ui_start_begin");
    if (voice_ui_start() != ESP_OK) {
        ESP_LOGW(TAG, "ui start failed; continuing with serial logs only");
        boot_trace("ui_start_failed");
    } else {
        boot_trace("ui_start_ok");
    }

    battery_init();

    boot_trace("audio_init_begin");
    if (voice_audio_playback_start_hardware() != ESP_OK) {
        ESP_LOGW(TAG, "audio playback init failed; TTS binary frames will be logged only");
        voice_ui_post_event(VOICE_UI_EVENT_ERROR, "audio playback init failed");
        boot_trace("audio_init_failed");
    } else {
        boot_trace("audio_init_ok");
        if (voice_audio_capture_start_hardware() != ESP_OK) {
            ESP_LOGW(TAG, "audio capture init failed; conversation mode unavailable");
            boot_trace("capture_init_failed");
        } else {
            boot_trace("capture_init_ok");
        }
    }

    boot_trace("wifi_start_begin");
    if (voice_robot_wifi_start() != ESP_OK) {
        ESP_LOGE(TAG, "wifi failed; starting BLE fallback");
        voice_ui_post_event(VOICE_UI_EVENT_WIFI_FAILED, "connect timeout");
        boot_trace("wifi_start_failed");
        if (voice_ble_gateway_start() != ESP_OK) {
            ESP_LOGE(TAG, "BLE fallback start failed; keeping diagnostics alive");
            voice_ui_post_event(VOICE_UI_EVENT_ERROR, "BLE fallback failed");
            boot_trace("ble_fallback_failed");
        } else {
            boot_trace("ble_fallback_ok");
        }
        while (true) {
            vTaskDelay(pdMS_TO_TICKS(60000));
        }
    }
    boot_trace("wifi_start_ok");

    /*
     * BLE is a Wi-Fi *fallback* only (see voice_ble_gateway_start() above on the
     * Wi-Fi-failure path). Starting the BLE controller in the background while
     * Wi-Fi is already up OOMs the controller's internal malloc
     * (`BLE_INIT: Malloc failed` -> `BLE assert emi.c 164` -> interrupt-wdt panic
     * -> boot loop). This board DOES have 8MB PSRAM, but PSRAM can't help here:
     * the BLE controller needs DMA-capable *internal* RAM, which Wi-Fi + LVGL +
     * audio + the hanzi-trace canvases have already largely consumed (internal
     * heap is only ~179 KiB at boot). So we skip background advertising when
     * Wi-Fi connected; BLE still comes up on the Wi-Fi-failure fallback path
     * where Wi-Fi has freed its internal RAM.
     */
    boot_trace("ble_background_skip");
    ESP_LOGI(TAG, "Wi-Fi up; skipping background BLE advertising (fallback-only to avoid OOM)");

    boot_trace("conv_init_begin");
    if (voice_conversation_hw_init() != ESP_OK) {
        ESP_LOGW(TAG, "conversation init failed; voice turn-taking unavailable");
        boot_trace("conv_init_failed");
    } else {
        boot_trace("conv_init_ok");
    }

    boot_trace("ws_start_begin");
    if (voice_ws_start() != ESP_OK) {
        ESP_LOGE(TAG, "websocket start failed");
        voice_ui_post_event(VOICE_UI_EVENT_ERROR, "websocket start failed");
        boot_trace("ws_start_failed");
        return;
    }
    boot_trace("ws_start_ok");

    // BOOT key = backup conversation-mode toggle until touch lands (M4).
    gpio_config_t boot_cfg = {
        .pin_bit_mask = 1ULL << BOOT_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    if (gpio_config(&boot_cfg) != ESP_OK) {
        ESP_LOGW(TAG, "BOOT button config failed; toggle via backend only");
        while (true) {
            vTaskDelay(pdMS_TO_TICKS(60000));
        }
    }

    // Short press: conversation-mode toggle. Long press (>=3s): forget Wi-Fi
    // and reboot into BLE provisioning (PRD 11.14).
    int last_level = 1;
    while (true) {
        int level = gpio_get_level(BOOT_BUTTON_GPIO);
        if (last_level == 1 && level == 0) {
            vTaskDelay(pdMS_TO_TICKS(BOOT_DEBOUNCE_MS));
            if (gpio_get_level(BOOT_BUTTON_GPIO) == 0) {
                int held_ms = BOOT_DEBOUNCE_MS;
                bool long_press = false;
                while (gpio_get_level(BOOT_BUTTON_GPIO) == 0) {
                    vTaskDelay(pdMS_TO_TICKS(BOOT_POLL_MS));
                    held_ms += BOOT_POLL_MS;
                    if (held_ms >= BOOT_LONG_PRESS_MS) {
                        long_press = true;
                        break;
                    }
                }
                if (long_press) {
                    ESP_LOGW(TAG, "BOOT long press: re-provisioning Wi-Fi");
                    voice_robot_wifi_request_provisioning(); /* restarts */
                } else if (voice_ws_ready()) {
                    voice_conversation_hw_toggle_deferred();
                } else {
                    ESP_LOGW(TAG, "ignore toggle: not authenticated yet");
                }
                while (gpio_get_level(BOOT_BUTTON_GPIO) == 0) {
                    vTaskDelay(pdMS_TO_TICKS(BOOT_POLL_MS));
                }
                level = 1;
            }
        }
        last_level = level;
        vTaskDelay(pdMS_TO_TICKS(BOOT_POLL_MS));
    }
}
