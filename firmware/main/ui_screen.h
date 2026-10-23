#pragma once

#include "esp_err.h"
#include "ui_state.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t voice_ui_start(void);
void voice_ui_post_event(voice_ui_event_type_t type, const char *detail);
const voice_ui_state_t *voice_ui_get_state(void);

#ifdef __cplusplus
}
#endif
