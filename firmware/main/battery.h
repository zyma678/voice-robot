#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

void battery_init(void);

/* Battery percentage 0-100, or -1 if unavailable. */
int battery_get_percent(void);

bool battery_is_charging(void);

#ifdef __cplusplus
}
#endif
