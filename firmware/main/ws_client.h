#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/**
 * Connect to the backend /ws endpoint, send the auth control message and
 * keep the connection alive with periodic heartbeats. Reconnects forever
 * with exponential backoff on disconnects.
 *
 * Wi-Fi must be ready (got IP) before calling this.
 */
esp_err_t voice_ws_start(void);

/** True once connected and auth_ok received. */
bool voice_ws_ready(void);

/* Backend environment selection: 0 = local LAN dev server (CONFIG_VR_WS_URL),
 * 1 = cloud ECS. Persisted in NVS; set reconnects to the new endpoint. */
int voice_ws_get_env(void);
void voice_ws_set_env(int env);
const char *voice_ws_env_url(int env);

/* Upstream voice turn: audio_start -> binary PCM frames -> audio_end. */
void voice_ws_send_audio_start(void);
void voice_ws_send_audio_frame(const uint8_t *data, size_t len);
void voice_ws_send_audio_end(void);
void voice_ws_send_barge_in(const char *reason);
void voice_ws_send_text_input(const char *text);

/* Push a free-form diagnostic line to the backend (republished to SSE monitors
 * as device_debug). Serial is unreliable on this USB-Serial-JTAG board, so this
 * is the reliable observability path. No-op until the link is ready. */
void voice_ws_send_debug(const char *text);

#ifdef __cplusplus
}
#endif
