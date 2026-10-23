#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "ble_gateway.h"

static void parses_set_volume_command(void) {
    int volume = -1;
    bool ok = voice_ble_gateway_parse_set_volume(
        "{\"type\":\"set_volume\",\"volume\":42}",
        &volume
    );

    assert(ok);
    assert(volume == 42);
}

static void clamps_set_volume_command(void) {
    int volume = -1;
    bool ok = voice_ble_gateway_parse_set_volume(
        "{\"type\":\"set_volume\",\"volume\":180}",
        &volume
    );

    assert(ok);
    assert(volume == 100);
}

static void rejects_unknown_command(void) {
    int volume = -1;
    bool ok = voice_ble_gateway_parse_set_volume(
        "{\"type\":\"say\",\"text\":\"hi\"}",
        &volume
    );

    assert(!ok);
    assert(volume == -1);
}

static void builds_short_device_name_that_fits_adv_payload(void) {
    char short_name[16] = {0};

    voice_ble_gateway_build_short_device_name("esp32-001",
                                              short_name,
                                              sizeof(short_name));

    assert(strcmp(short_name, "VR-001") == 0);
    assert(strlen(short_name) <= 8);
}

int main(void) {
    parses_set_volume_command();
    clamps_set_volume_command();
    rejects_unknown_command();
    builds_short_device_name_that_fits_adv_payload();
    puts("ble_gateway tests passed");
    return 0;
}
