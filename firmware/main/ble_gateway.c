#include "ble_gateway.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef ESP_PLATFORM
#include "esp_log.h"
#include "esp_err.h"
#include "host/ble_att.h"
#include "host/ble_gap.h"
#include "host/ble_gatt.h"
#include "host/ble_hs.h"
#include "host/ble_hs_id.h"
#include "host/ble_uuid.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "os/os_mbuf.h"
#include "sdkconfig.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "store/config/ble_store_config.h"

#include "es8311_codec.h"
#include "ui_screen.h"
#endif

#ifdef ESP_PLATFORM
static const char *TAG = "ble_gateway";
static uint8_t s_own_addr_type;
static uint16_t s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
static uint16_t s_status_val_handle;
static bool s_started;
static bool s_subscribed;
static bool s_publish_ui_on_adv;
static char s_device_name[32];
static char s_short_device_name[11];
static int gap_event_cb(struct ble_gap_event *event, void *arg);
void ble_store_config_init(void);
#endif

static const char *find_json_field(const char *json, const char *field) {
    if (!json || !field) {
        return NULL;
    }
    char needle[48];
    int n = snprintf(needle, sizeof(needle), "\"%s\"", field);
    if (n <= 0 || n >= (int)sizeof(needle)) {
        return NULL;
    }
    const char *p = strstr(json, needle);
    if (!p) {
        return NULL;
    }
    p += n;
    while (*p == ' ' || *p == '\t' || *p == ':') {
        p++;
    }
    return p;
}

static bool json_string_equals(const char *json, const char *field, const char *expected) {
    const char *p = find_json_field(json, field);
    if (!p || *p != '"') {
        return false;
    }
    p++;
    size_t expected_len = strlen(expected);
    return strncmp(p, expected, expected_len) == 0 && p[expected_len] == '"';
}

static bool json_extract_int_field(const char *json, const char *field, int *out) {
    const char *p = find_json_field(json, field);
    if (!p) {
        return false;
    }
    char *end = NULL;
    long value = strtol(p, &end, 10);
    if (end == p) {
        return false;
    }
    *out = (int)value;
    return true;
}

bool voice_ble_gateway_parse_set_volume(const char *json, int *out_volume) {
    if (!out_volume || !json_string_equals(json, "type", "set_volume")) {
        return false;
    }
    int volume = 0;
    if (!json_extract_int_field(json, "volume", &volume)) {
        return false;
    }
    if (volume < 0) {
        volume = 0;
    } else if (volume > 100) {
        volume = 100;
    }
    *out_volume = volume;
    return true;
}

void voice_ble_gateway_build_short_device_name(const char *device_id,
                                               char *out,
                                               size_t out_size) {
    if (!out || out_size == 0) {
        return;
    }
    out[0] = '\0';

    const char *clean = device_id ? device_id : "";
    size_t len = strlen(clean);
    if (len >= 3) {
        clean += len - 3;
    }
    snprintf(out, out_size, "VR-%s", clean);
}

#ifdef ESP_PLATFORM
static void notify_status(const char *status) {
    if (!status || !s_subscribed || s_conn_handle == BLE_HS_CONN_HANDLE_NONE) {
        return;
    }
    struct os_mbuf *om = ble_hs_mbuf_from_flat(status, strlen(status));
    if (!om) {
        return;
    }
    int rc = ble_gatts_notify_custom(s_conn_handle, s_status_val_handle, om);
    if (rc != 0) {
        ESP_LOGW(TAG, "status notify failed rc=%d", rc);
    }
}

static int write_mbuf_to_string(struct os_mbuf *om, char *out, size_t out_size) {
    if (!out || out_size == 0) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    uint16_t len = OS_MBUF_PKTLEN(om);
    if (len == 0 || len >= out_size) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    int rc = ble_hs_mbuf_to_flat(om, out, out_size - 1, &len);
    if (rc != 0) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    out[len] = '\0';
    return 0;
}

static int control_access_cb(uint16_t conn_handle,
                             uint16_t attr_handle,
                             struct ble_gatt_access_ctxt *ctxt,
                             void *arg) {
    (void)conn_handle;
    (void)attr_handle;
    (void)arg;

    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) {
        return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    }

    char command[160] = {0};
    int rc = write_mbuf_to_string(ctxt->om, command, sizeof(command));
    if (rc != 0) {
        return rc;
    }
    ESP_LOGI(TAG, "control write: %s", command);

    int volume = 0;
    if (voice_ble_gateway_parse_set_volume(command, &volume)) {
        esp_err_t ret = voice_es8311_set_volume(volume);
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "set volume failed: %s", esp_err_to_name(ret));
            notify_status("{\"type\":\"error\",\"message\":\"set_volume_failed\"}");
            return BLE_ATT_ERR_UNLIKELY;
        }
        char status[64];
        snprintf(status, sizeof(status),
                 "{\"type\":\"volume\",\"volume\":%d}", volume);
        ESP_LOGI(TAG, "volume command applied over BLE: %d", volume);
        notify_status(status);
        return 0;
    }

    ESP_LOGW(TAG, "unsupported BLE control command");
    notify_status("{\"type\":\"error\",\"message\":\"unsupported_command\"}");
    return BLE_ATT_ERR_UNLIKELY;
}

static int status_access_cb(uint16_t conn_handle,
                            uint16_t attr_handle,
                            struct ble_gatt_access_ctxt *ctxt,
                            void *arg) {
    (void)conn_handle;
    (void)attr_handle;
    (void)arg;
    if (ctxt->op != BLE_GATT_ACCESS_OP_READ_CHR) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    const char *status = "{\"type\":\"status\",\"state\":\"ble_ready\"}";
    int rc = os_mbuf_append(ctxt->om, status, strlen(status));
    return rc == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

static const ble_uuid128_t s_service_uuid =
    BLE_UUID128_INIT(0x01, 0x00, 0x00, 0x74, 0x6f, 0x62, 0x65, 0x63,
                     0x69, 0x6f, 0x76, 0x7d, 0x9e, 0x5a, 0x4d, 0x6f);
static const ble_uuid128_t s_control_uuid =
    BLE_UUID128_INIT(0x02, 0x00, 0x00, 0x74, 0x6f, 0x62, 0x65, 0x63,
                     0x69, 0x6f, 0x76, 0x7d, 0x9e, 0x5a, 0x4d, 0x6f);
static const ble_uuid128_t s_status_uuid =
    BLE_UUID128_INIT(0x03, 0x00, 0x00, 0x74, 0x6f, 0x62, 0x65, 0x63,
                     0x69, 0x6f, 0x76, 0x7d, 0x9e, 0x5a, 0x4d, 0x6f);

static const struct ble_gatt_svc_def s_services[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &s_service_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = &s_control_uuid.u,
                .access_cb = control_access_cb,
                .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                .uuid = &s_status_uuid.u,
                .access_cb = status_access_cb,
                .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
                .val_handle = &s_status_val_handle,
            },
            {0},
        },
    },
    {0},
};

static void start_advertising(void) {
    struct ble_hs_adv_fields fields;
    memset(&fields, 0, sizeof(fields));
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.name = (uint8_t *)s_short_device_name;
    fields.name_len = strlen(s_short_device_name);
    fields.name_is_complete = 1;
    fields.uuids128 = &s_service_uuid;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;

    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv fields failed rc=%d", rc);
        return;
    }

    struct ble_hs_adv_fields rsp_fields;
    memset(&rsp_fields, 0, sizeof(rsp_fields));
    rsp_fields.name = (uint8_t *)s_device_name;
    rsp_fields.name_len = strlen(s_device_name);
    rsp_fields.name_is_complete = 1;

    rc = ble_gap_adv_rsp_set_fields(&rsp_fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv rsp fields failed rc=%d", rc);
        return;
    }

    struct ble_gap_adv_params params;
    memset(&params, 0, sizeof(params));
    params.conn_mode = BLE_GAP_CONN_MODE_UND;
    params.disc_mode = BLE_GAP_DISC_MODE_GEN;

    rc = ble_gap_adv_start(s_own_addr_type, NULL, BLE_HS_FOREVER, &params,
                           gap_event_cb, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv start failed rc=%d", rc);
    } else {
        ESP_LOGI(TAG, "advertising as %s", s_device_name);
        if (s_publish_ui_on_adv) {
            voice_ui_post_event(VOICE_UI_EVENT_BLE_FALLBACK_READY, s_device_name);
        }
    }
}

static int gap_event_cb(struct ble_gap_event *event, void *arg) {
    (void)arg;
    switch (event->type) {
        case BLE_GAP_EVENT_CONNECT:
            if (event->connect.status == 0) {
                s_conn_handle = event->connect.conn_handle;
                ESP_LOGI(TAG, "BLE gateway connected");
                notify_status("{\"type\":\"status\",\"state\":\"connected\"}");
            } else {
                ESP_LOGW(TAG, "BLE connect failed status=%d", event->connect.status);
                start_advertising();
            }
            return 0;
        case BLE_GAP_EVENT_DISCONNECT:
            ESP_LOGI(TAG, "BLE gateway disconnected reason=%d",
                     event->disconnect.reason);
            s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
            s_subscribed = false;
            start_advertising();
            return 0;
        case BLE_GAP_EVENT_SUBSCRIBE:
            if (event->subscribe.attr_handle == s_status_val_handle) {
                s_subscribed = event->subscribe.cur_notify != 0;
                ESP_LOGI(TAG, "status notify subscribed=%d", s_subscribed);
            }
            return 0;
        default:
            return 0;
    }
}

static void on_reset(int reason) {
    ESP_LOGE(TAG, "NimBLE reset reason=%d", reason);
}

static void on_sync(void) {
    int rc = ble_hs_util_ensure_addr(0);
    if (rc != 0) {
        ESP_LOGE(TAG, "ensure addr failed rc=%d", rc);
        return;
    }
    rc = ble_hs_id_infer_auto(0, &s_own_addr_type);
    if (rc != 0) {
        ESP_LOGE(TAG, "infer addr type failed rc=%d", rc);
        return;
    }
    start_advertising();
}

static void host_task(void *param) {
    (void)param;
    ESP_LOGI(TAG, "NimBLE host task started");
    nimble_port_run();
    nimble_port_freertos_deinit();
}

static esp_err_t start_gateway(bool publish_ui_on_adv) {
    if (s_started) {
        s_publish_ui_on_adv = s_publish_ui_on_adv || publish_ui_on_adv;
        return ESP_OK;
    }

    s_publish_ui_on_adv = publish_ui_on_adv;
    snprintf(s_device_name, sizeof(s_device_name), "VR-%s", CONFIG_VR_DEVICE_ID);
    voice_ble_gateway_build_short_device_name(CONFIG_VR_DEVICE_ID,
                                              s_short_device_name,
                                              sizeof(s_short_device_name));

    esp_err_t ret = nimble_port_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "nimble_port_init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    ble_hs_cfg.reset_cb = on_reset;
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;

    ble_svc_gap_init();
    ble_svc_gatt_init();
    int rc = ble_svc_gap_device_name_set(s_device_name);
    if (rc != 0) {
        ESP_LOGW(TAG, "set device name rc=%d", rc);
    }
    rc = ble_gatts_count_cfg(s_services);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_gatts_count_cfg rc=%d", rc);
        nimble_port_deinit();
        return ESP_FAIL;
    }
    rc = ble_gatts_add_svcs(s_services);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_gatts_add_svcs rc=%d", rc);
        nimble_port_deinit();
        return ESP_FAIL;
    }

    ble_store_config_init();
    nimble_port_freertos_init(host_task);
    s_started = true;
    ESP_LOGI(TAG, "BLE gateway %s starting",
             publish_ui_on_adv ? "fallback" : "background advertising");
    return ESP_OK;
}

esp_err_t voice_ble_gateway_start(void) {
#if CONFIG_VR_BLE_FALLBACK_ENABLE
    return start_gateway(true);
#else
    ESP_LOGW(TAG, "BLE fallback disabled by config");
    return ESP_FAIL;
#endif
}

esp_err_t voice_ble_gateway_start_background(void) {
#if CONFIG_VR_BLE_FALLBACK_ENABLE
    return start_gateway(false);
#else
    ESP_LOGW(TAG, "BLE fallback disabled by config");
    return ESP_FAIL;
#endif
}
#else
esp_err_t voice_ble_gateway_start(void) {
    return ESP_FAIL;
}

esp_err_t voice_ble_gateway_start_background(void) {
    return ESP_FAIL;
}
#endif
