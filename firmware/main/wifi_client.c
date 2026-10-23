#include "wifi_client.h"

#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "network_provisioning/manager.h"
#include "network_provisioning/scheme_ble.h"
#include "nvs.h"
#include "nvs_flash.h"

#include "sdkconfig.h"
#include "ui_screen.h"

static const char *TAG = "wifi";

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

#define VR_NVS_NAMESPACE "vr"
#define VR_NVS_FORCE_PROV_KEY "force_prov"
#define VR_NVS_WIFI_PRESET_KEY "wifi_preset"
#define PROV_TIMEOUT_MS (10 * 60 * 1000)
#define WIFI_CONNECT_TIMEOUT_MS 65000

static EventGroupHandle_t s_wifi_event_group;
static int s_retry_num = 0;
static volatile bool s_provisioning = false;
static volatile bool s_wifi_boot_done = false;
static volatile uint8_t s_wifi_preset = 0;
static volatile bool s_wifi_using_runtime_preset = false;

#define WIFI_PRESET_PRIMARY_SSID CONFIG_VR_WIFI_SSID
#define WIFI_PRESET_PRIMARY_PASSWORD CONFIG_VR_WIFI_PASSWORD
#define WIFI_PRESET_HOTSPOT_SSID CONFIG_VR_WIFI_HOTSPOT_SSID
#define WIFI_PRESET_HOTSPOT_PASSWORD CONFIG_VR_WIFI_HOTSPOT_PASSWORD

const char *voice_wifi_preset_ssid(int preset) {
    return preset == VOICE_WIFI_PRESET_HOTSPOT ? WIFI_PRESET_HOTSPOT_SSID
                                               : WIFI_PRESET_PRIMARY_SSID;
}

static const char *wifi_preset_password(int preset) {
    return preset == VOICE_WIFI_PRESET_HOTSPOT ? WIFI_PRESET_HOTSPOT_PASSWORD
                                               : WIFI_PRESET_PRIMARY_PASSWORD;
}

int voice_wifi_get_preset(void) {
    return (int)s_wifi_preset;
}

static esp_err_t apply_wifi_preset_config(uint8_t preset) {
    wifi_config_t wifi_config = {0};
    const char *ssid = voice_wifi_preset_ssid(preset);
    const char *pwd = wifi_preset_password(preset);
    strncpy((char *)wifi_config.sta.ssid, ssid, sizeof(wifi_config.sta.ssid) - 1);
    strncpy((char *)wifi_config.sta.password, pwd,
            sizeof(wifi_config.sta.password) - 1);
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    return esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
}

static bool wifi_try_next_preset_after_failure(void) {
    if (!s_wifi_using_runtime_preset || s_wifi_preset != VOICE_WIFI_PRESET_PRIMARY ||
        WIFI_PRESET_HOTSPOT_SSID[0] == '\0') {
        return false;
    }

    s_wifi_preset = VOICE_WIFI_PRESET_HOTSPOT;
    s_retry_num = 0;
    if (apply_wifi_preset_config(s_wifi_preset) != ESP_OK) {
        ESP_LOGE(TAG, "failed to apply hotspot preset");
        return false;
    }
    ESP_LOGW(TAG, "primary preset failed; trying preset=%d ssid=\"%s\"",
             s_wifi_preset, voice_wifi_preset_ssid(s_wifi_preset));
    voice_ui_post_event(VOICE_UI_EVENT_WIFI_CONNECTING, NULL);
    esp_wifi_connect();
    return true;
}

void voice_wifi_select_preset(int preset) {
    uint8_t want = preset == VOICE_WIFI_PRESET_HOTSPOT ? VOICE_WIFI_PRESET_HOTSPOT
                                                       : VOICE_WIFI_PRESET_PRIMARY;
    if (want == s_wifi_preset) {
        return;
    }
    s_wifi_preset = want;
    nvs_handle_t handle;
    if (nvs_open(VR_NVS_NAMESPACE, NVS_READWRITE, &handle) == ESP_OK) {
        nvs_set_u8(handle, VR_NVS_WIFI_PRESET_KEY, s_wifi_preset);
        nvs_commit(handle);
        nvs_close(handle);
    }
    ESP_LOGW(TAG, "wifi preset switched -> %d (%s); restarting", s_wifi_preset,
             voice_wifi_preset_ssid(s_wifi_preset));
    vTaskDelay(pdMS_TO_TICKS(200));
    esp_restart();
}

static void wifi_event_handler(void *arg,
                               esp_event_base_t base,
                               int32_t event_id,
                               void *event_data) {
    if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        if (s_provisioning) {
            return; /* provisioning manager owns the connection attempts */
        }
        voice_ui_post_event(VOICE_UI_EVENT_WIFI_CONNECTING, NULL);
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_provisioning) {
            return;
        }
        wifi_event_sta_disconnected_t *dis =
            (wifi_event_sta_disconnected_t *)event_data;
        if (dis) {
            /* reason码定位连不上的根因：201=NO_AP_FOUND（SSID找不到/5GHz热点，
             * S3只支持2.4GHz）；15=4WAY_HANDSHAKE_TIMEOUT、2/204=密码错；
             * 205=CONNECTION_FAIL。 */
            ESP_LOGW(TAG, "disconnected from \"%s\" reason=%d", (char *)dis->ssid,
                     dis->reason);
        }
        if (s_wifi_boot_done) {
            s_retry_num++;
            ESP_LOGW(TAG, "wifi lost post-boot; reconnecting (%d)", s_retry_num);
            esp_wifi_connect();
            voice_ui_post_event(VOICE_UI_EVENT_WIFI_CONNECTING, NULL);
        } else if (s_retry_num < 8) {
            esp_wifi_connect();
            s_retry_num++;
            ESP_LOGW(TAG, "retry to connect to AP (%d)", s_retry_num);
            voice_ui_post_event(VOICE_UI_EVENT_WIFI_CONNECTING, NULL);
        } else if (wifi_try_next_preset_after_failure()) {
            ESP_LOGW(TAG, "retrying Wi-Fi with fallback preset");
        } else {
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
            voice_ui_post_event(VOICE_UI_EVENT_WIFI_FAILED, "retry limit reached");
        }
    } else if (base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *ev = (ip_event_got_ip_t *)event_data;
        char ip[24];
        snprintf(ip, sizeof(ip), IPSTR, IP2STR(&ev->ip_info.ip));
        ESP_LOGI(TAG, "got ip: " IPSTR, IP2STR(&ev->ip_info.ip));
        s_retry_num = 0;
        voice_ui_post_event(VOICE_UI_EVENT_WIFI_CONNECTED, ip);
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static bool consume_force_prov_flag(void) {
    nvs_handle_t handle;
    if (nvs_open(VR_NVS_NAMESPACE, NVS_READWRITE, &handle) != ESP_OK) {
        return false;
    }
    uint8_t flag = 0;
    bool forced = nvs_get_u8(handle, VR_NVS_FORCE_PROV_KEY, &flag) == ESP_OK && flag == 1;
    if (forced) {
        nvs_erase_key(handle, VR_NVS_FORCE_PROV_KEY);
        nvs_commit(handle);
    }
    nvs_close(handle);
    return forced;
}

static void wifi_preset_load(void) {
    s_wifi_preset = VOICE_WIFI_PRESET_PRIMARY;
    nvs_handle_t handle;
    if (nvs_open(VR_NVS_NAMESPACE, NVS_READWRITE, &handle) == ESP_OK) {
        uint8_t preset = VOICE_WIFI_PRESET_PRIMARY;
        if (nvs_get_u8(handle, VR_NVS_WIFI_PRESET_KEY, &preset) == ESP_OK) {
            s_wifi_preset = preset == VOICE_WIFI_PRESET_HOTSPOT
                                ? VOICE_WIFI_PRESET_HOTSPOT
                                : VOICE_WIFI_PRESET_PRIMARY;
        } else {
            nvs_set_u8(handle, VR_NVS_WIFI_PRESET_KEY, s_wifi_preset);
            nvs_commit(handle);
        }
        nvs_close(handle);
    }
    ESP_LOGI(TAG, "wifi preset=%d ssid=\"%s\"", s_wifi_preset,
             voice_wifi_preset_ssid(s_wifi_preset));
}

void voice_robot_wifi_request_provisioning(void) {
    nvs_handle_t handle;
    if (nvs_open(VR_NVS_NAMESPACE, NVS_READWRITE, &handle) == ESP_OK) {
        nvs_set_u8(handle, VR_NVS_FORCE_PROV_KEY, 1);
        nvs_commit(handle);
        nvs_close(handle);
    }
    ESP_LOGW(TAG, "re-provisioning requested; restarting");
    vTaskDelay(pdMS_TO_TICKS(200));
    esp_restart();
}

bool voice_wifi_get_info(char *ssid, int ssid_len, char *ip, int ip_len, int *rssi) {
    if (ssid && ssid_len > 0) {
        ssid[0] = '\0';
    }
    if (ip && ip_len > 0) {
        ip[0] = '\0';
    }
    if (rssi) {
        *rssi = 0;
    }

    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) {
        return false;
    }
    if (ssid && ssid_len > 0) {
        snprintf(ssid, ssid_len, "%s", (const char *)ap.ssid);
    }
    if (rssi) {
        *rssi = ap.rssi;
    }
    if (ip && ip_len > 0) {
        esp_netif_t *netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
        esp_netif_ip_info_t ip_info;
        if (netif && esp_netif_get_ip_info(netif, &ip_info) == ESP_OK) {
            snprintf(ip, ip_len, IPSTR, IP2STR(&ip_info.ip));
        }
    }
    return true;
}

/* Pinned 128-bit provisioning service UUID = 021a9004-0382-4aea-bff4-6b3f1c5adfb4
 * (bytes are little-endian, as the BLE scheme expects). Pinned — not left to the
 * default — so the Web Bluetooth console can declare and discover it (Web
 * Bluetooth can't enumerate undeclared services). Must match
 * PROV_SERVICE_UUID in web_console/provisioning.js. */
static uint8_t s_prov_service_uuid[16] = {
    0xb4, 0xdf, 0x5a, 0x1c, 0x3f, 0x6b, 0xf4, 0xbf,
    0xea, 0x4a, 0x82, 0x03, 0x04, 0x90, 0x1a, 0x02,
};

/** Run blocking BLE provisioning. Returns ESP_OK once credentials are applied. */
static esp_err_t run_ble_provisioning(bool reset_old) {
    char service_name[32];
    snprintf(service_name, sizeof(service_name), "PROV_%s", CONFIG_VR_DEVICE_ID);

    network_prov_scheme_ble_set_service_uuid(s_prov_service_uuid);

    network_prov_mgr_config_t prov_cfg = {
        .scheme = network_prov_scheme_ble,
        .scheme_event_handler = NETWORK_PROV_EVENT_HANDLER_NONE,
    };
    ESP_ERROR_CHECK(network_prov_mgr_init(prov_cfg));

    if (reset_old) {
        network_prov_mgr_reset_wifi_provisioning();
    }

    s_provisioning = true;
    voice_ui_post_event(VOICE_UI_EVENT_PROVISIONING, service_name);
    ESP_LOGI(TAG, "starting BLE provisioning: service=%s pop=%s",
             service_name, CONFIG_VR_PROV_POP);

    esp_err_t ret = network_prov_mgr_start_provisioning(
        NETWORK_PROV_SECURITY_1, CONFIG_VR_PROV_POP, service_name, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "provisioning start failed: %s", esp_err_to_name(ret));
        s_provisioning = false;
        network_prov_mgr_deinit();
        return ret;
    }

    network_prov_mgr_wait();
    s_provisioning = false;
    ESP_LOGI(TAG, "provisioning finished");
    network_prov_mgr_deinit();
    return ESP_OK;
}

esp_err_t voice_robot_wifi_start(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_preset_load();

    s_wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_t inst_any_id;
    esp_event_handler_instance_t inst_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, &inst_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, &inst_got_ip));

    bool force_prov = consume_force_prov_flag();
    bool legacy_creds = voice_wifi_preset_ssid(s_wifi_preset)[0] != '\0';
    ESP_LOGI(TAG, "wifi source: built_in=%d force_prov=%d", legacy_creds, force_prov);

    if (force_prov || !legacy_creds) {
        /* PRD 11.14: no usable credentials (or user forced) -> BLE provisioning.
         * The manager applies credentials and starts the STA connection itself. */
        esp_err_t prov_ret = run_ble_provisioning(force_prov);
        if (prov_ret != ESP_OK) {
            return ESP_FAIL;
        }
    } else {
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
        /* For deterministic lab testing, always apply the currently selected
         * built-in Wi-Fi preset here. This intentionally overrides any stored
         * provisioning credentials so the user can switch networks from the
         * on-device Settings menu without re-provisioning. */
        ESP_ERROR_CHECK(apply_wifi_preset_config(s_wifi_preset));
        s_wifi_using_runtime_preset = true;
        ESP_LOGI(TAG, "connecting with preset=%d ssid=\"%s\"",
                 s_wifi_preset, voice_wifi_preset_ssid(s_wifi_preset));
        ESP_ERROR_CHECK(esp_wifi_start());
        // Disable Wi-Fi modem power-save: the default min-modem PS sleeps the
        // radio between beacons, which delays/drops buffered packets during the
        // sustained TTS audio download and breaks the TCP/WebSocket mid-stream
        // ("data transfer failed" on the server). The robot is wall-powered, so
        // trade power for a reliable always-on link.
        ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
        voice_ui_post_event(VOICE_UI_EVENT_WIFI_CONNECTING, NULL);
    }

    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_event_group, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
        pdFALSE, pdFALSE, pdMS_TO_TICKS(WIFI_CONNECT_TIMEOUT_MS));

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "wifi connected");
        s_wifi_boot_done = true;
        return ESP_OK;
    }
    ESP_LOGE(TAG, "wifi connect failed");
    voice_ui_post_event(VOICE_UI_EVENT_WIFI_FAILED, "connect timeout");
    esp_wifi_disconnect();
    esp_wifi_stop();
    return ESP_FAIL;
}
