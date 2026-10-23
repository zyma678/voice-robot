#pragma once

#include "driver/i2c_master.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Shared I2C master bus (port 0, SDA GPIO15 / SCL GPIO14) used by the
 * ES8311 codec and the CST816 touch controller. Created on first use;
 * safe to call from multiple tasks.
 */
i2c_master_bus_handle_t voice_i2c_bus_get(void);

#ifdef __cplusplus
}
#endif
