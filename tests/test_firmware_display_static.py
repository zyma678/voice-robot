import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPLAY = ROOT / "firmware" / "main" / "display_lcd.c"
CJK_FONT = ROOT / "firmware" / "main" / "lv_font_cjk_sc_16.c"
FACE_GENERATOR = ROOT / "firmware" / "tools" / "gen_face_sprites.py"
FACE_EYES = ROOT / "firmware" / "main" / "face_eyes_sprites.h"
UI_STATE = ROOT / "firmware" / "main" / "ui_state.c"
WS_CLIENT = ROOT / "firmware" / "main" / "ws_client.c"
ES8311_CODEC = ROOT / "firmware" / "main" / "es8311_codec.c"
APP_MAIN = ROOT / "firmware" / "main" / "app_main.c"
BLE_GATEWAY = ROOT / "firmware" / "main" / "ble_gateway.c"
BLE_GATEWAY_H = ROOT / "firmware" / "main" / "ble_gateway.h"
SDKCONFIG_DEFAULTS = ROOT / "firmware" / "sdkconfig.defaults"
WIFI_CLIENT = ROOT / "firmware" / "main" / "wifi_client.c"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_cjk_font_falls_back_to_lvgl_symbols() -> None:
    font = _source(CJK_FONT)
    assert ".fallback = &lv_font_montserrat_14" in font


def test_cjk_font_bitmap_format_matches_lvgl_config() -> None:
    font = _source(CJK_FONT)
    sdkconfig_defaults = _source(ROOT / "firmware" / "sdkconfig.defaults")
    compressed = re.search(r"\.bitmap_format\s*=\s*([12])\s*,", font)
    if compressed:
        assert "CONFIG_LV_USE_FONT_COMPRESSED=y" in sdkconfig_defaults


def test_display_uses_plain_cjk_text_for_chinese_labels() -> None:
    display = _source(DISPLAY)
    for match in re.finditer(r'lv_label_set_text\([^;]+;', display, flags=re.S):
        call = match.group(0)
        has_chinese = any("\u4e00" <= char <= "\u9fff" for char in call)
        assert not (has_chinese and "LV_SYMBOL_" in call), call


def test_display_buttons_disable_default_shadow() -> None:
    display = _source(DISPLAY)
    for match in re.finditer(r"lv_obj_t \*(\w+) = lv_button_create\(", display):
        name = match.group(1)
        block = display[match.start(): match.start() + 420]
        assert f"lv_obj_set_style_shadow_width({name}, 0, 0)" in block


def test_status_detail_line_uses_cjk_font() -> None:
    display = _source(DISPLAY)
    detail_block = display[
        display.index("s_lbl_detail = lv_label_create(scr);"):
        display.index("/* Subtitle:", display.index("s_lbl_detail = lv_label_create(scr);"))
    ]
    assert "lv_obj_set_style_text_font(s_lbl_detail, &lv_font_cjk_sc_16, 0)" in detail_block


def test_main_screen_uses_semantic_status_capsule_and_subtitle_card() -> None:
    display = _source(DISPLAY)
    widgets_block = display[
        display.index("static void build_widgets_common(lv_obj_t *scr)"):
        display.index("static void build_widgets(void)")
    ]
    assert "s_status_pill = lv_obj_create(scr);" in widgets_block
    assert "s_status_dot = lv_obj_create(scr);" in widgets_block
    assert "s_subtitle_card = lv_obj_create(scr);" in widgets_block
    assert "LV_OBJ_FLAG_HIDDEN" in widgets_block
    render_block = display[
        display.index("void voice_display_lcd_render(const voice_ui_state_t *state)"):
        display.index("/* ===== 成语大字", display.index("void voice_display_lcd_render"))
    ]
    assert "lv_obj_set_style_border_color(s_status_pill, style.color, 0)" in render_block
    assert "lv_obj_set_style_bg_color(s_status_dot, style.color, 0)" in render_block
    assert "state->subtitle[0] != '\\0'" in render_block


def test_hardware_ui_reuses_existing_animation_timer_for_status_pulse() -> None:
    display = _source(DISPLAY)
    timer_block = display[
        display.index("static void anim_timer_cb"):
        display.index("/* --- Settings dropdown", display.index("static void anim_timer_cb"))
    ]
    assert "s_status_dot" in timer_block
    assert "lv_obj_set_style_opa" in timer_block
    assert "lv_timer_create" not in timer_block


def test_butterfly_flyaway_avoids_layout_and_hidden_toggles() -> None:
    display = _source(DISPLAY)
    fly_block = display[
        display.index("static void bf_set_fly_transform"):
        display.index("static void anim_butterfly_tick")
    ]
    assert "lv_obj_set_pos" in fly_block
    assert "if (opa < 30) opa = 30" in fly_block
    assert "lv_obj_align" not in fly_block
    assert "lv_obj_add_flag" not in fly_block


def test_main_tap_handler_does_not_mutate_sprites_in_touch_context() -> None:
    display = _source(DISPLAY)
    button_block = display[
        display.index("static void main_button_cb"):
        display.index("static bool screen_is_online")
    ]
    widgets_block = display[
        display.index("static void build_widgets_common(lv_obj_t *scr)"):
        display.index("/* Top status capsule", display.index("static void build_widgets_common"))
    ]
    assert "voice_conversation_hw_toggle_deferred();" in button_block
    assert "lv_obj_set_style_transform_scale" not in button_block
    assert "main_press_feedback_cb" not in display
    assert "LV_EVENT_PRESSED" not in widgets_block
    assert "LV_EVENT_RELEASED" not in widgets_block
    assert "LV_EVENT_PRESS_LOST" not in widgets_block


def test_dialog_status_is_centered_at_top() -> None:
    display = _source(DISPLAY)
    topbar_block = display[
        display.index("s_lbl_topbar = lv_label_create(scr);"):
        display.index("/* Detail line", display.index("s_lbl_topbar = lv_label_create(scr);"))
    ]
    assert "LV_ALIGN_TOP_MID" in topbar_block
    assert "LV_ALIGN_TOP_LEFT" not in topbar_block


def test_main_screen_uses_hidden_top_right_hotspot_for_settings() -> None:
    display = _source(DISPLAY)
    widgets_block = display[
        display.index("static void build_widgets_common(lv_obj_t *scr)"):
        display.index("static void build_widgets(void)")
    ]
    assert "s_btn_menu" not in display
    assert "menu_button_cb" not in display
    assert 'lv_label_set_text(gear, "设置")' not in display
    assert "s_pull_zone = lv_obj_create(scr);" in widgets_block
    assert "lv_obj_set_size(s_pull_zone, 72, MENU_PULL_ZONE_H)" in widgets_block
    assert "lv_obj_align(s_pull_zone, LV_ALIGN_TOP_RIGHT, 0, 0)" in widgets_block
    assert "lv_obj_set_style_bg_opa(s_pull_zone, LV_OPA_TRANSP, 0)" in widgets_block
    assert "lv_obj_add_event_cb(s_pull_zone, menu_pull_zone_press_cb, LV_EVENT_ALL, NULL)" in widgets_block


def test_pull_zone_press_callback_opens_menu_only_after_downward_drag() -> None:
    display = _source(DISPLAY)
    start = display.index("static void menu_pull_zone_press_cb")
    press_block = display[
        start:
        display.index("static void vol_slider_cb", start)
    ]
    assert "LV_EVENT_PRESSED" in press_block
    assert "LV_EVENT_PRESSING" in press_block
    assert "MENU_PULL_TRIGGER" in press_block
    assert "p.y - s_pull_start_y >= MENU_PULL_TRIGGER" in press_block
    assert "menu_open();" in press_block


def test_pull_zone_plain_tap_does_not_open_menu() -> None:
    display = _source(DISPLAY)
    start = display.index("static void menu_pull_zone_press_cb")
    press_block = display[
        start:
        display.index("static void vol_slider_cb", start)
    ]
    assert "LV_EVENT_CLICKED" not in press_block


def test_hidden_top_right_hotspot_has_no_visible_label_or_icon() -> None:
    display = _source(DISPLAY)
    widgets_block = display[
        display.index("static void build_widgets_common(lv_obj_t *scr)"):
        display.index("static void build_widgets(void)")
    ]
    assert "lv_label_create(s_pull_zone)" not in widgets_block
    assert "LV_SYMBOL_" not in widgets_block


def test_wifi_status_is_not_shown_on_main_screen() -> None:
    display = _source(DISPLAY)
    main_screen = display[display.index("static void build_widgets(void)"):]
    assert "s_lbl_topright" not in main_screen
    assert "LV_SYMBOL_WIFI" not in main_screen
    assert "s_lbl_net = lv_label_create(card);" in display


def test_settings_panel_closes_without_explicit_close_button() -> None:
    display = _source(DISPLAY)
    menu_block = display[
        display.index("static void menu_open(void)"):
        display.index("static int32_t trace_map_x")
    ]
    assert "close_btn" not in menu_block
    assert "close_lbl" not in menu_block
    assert '"关闭"' not in menu_block
    assert "lv_obj_add_event_cb(s_menu_overlay, menu_close_cb, LV_EVENT_CLICKED, NULL)" in menu_block


def test_face_layout_lifts_mouth_above_subtitle() -> None:
    display = _source(DISPLAY)
    mouth_y = re.search(r"#define FACE_MOUTH_Y\s+(\d+)", display)
    assert mouth_y, "expected FACE_MOUTH_Y layout macro"
    assert int(mouth_y.group(1)) < 174
    assert "lv_obj_align(s_img_mouth, LV_ALIGN_TOP_MID, 0, FACE_MOUTH_Y)" in display


def test_eye_sprite_width_grows_to_add_spacing_between_eyes() -> None:
    eyes = _source(FACE_EYES)
    width = re.search(r"#define FACE_EYES_W\s+(\d+)", eyes)
    assert width, "expected FACE_EYES_W macro"
    assert int(width.group(1)) > 198


def test_face_animation_uses_richer_cycles_for_active_states() -> None:
    display = _source(DISPLAY)
    anim_block = display[
        display.index("static void anim_face_tick(void)"):
        display.index("static uint16_t sparkle_rand16(void)")
    ]
    for name in [
        "idle_eyes_cycle",
        "recording_eyes_cycle",
        "recognizing_eyes_cycle",
        "thinking_eyes_cycle",
        "playback_eyes_cycle",
        "recording_mouth_cycle",
        "recognizing_mouth_cycle",
        "thinking_mouth_cycle",
    ]:
        assert name in anim_block
    for sprite in [
        "face_eyes_happy",
        "face_eyes_surprised",
        "face_eyes_question",
        "face_eyes_blink",
        "face_eyes_angry",
        "face_mouth_frown",
        "face_mouth_teardrop",
        "face_mouth_o_sing",
        "face_mouth_open_tongue",
    ]:
        assert sprite in anim_block


def test_recognizing_screen_uses_dedicated_animation_cycles() -> None:
    display = _source(DISPLAY)
    anim_block = display[
        display.index("static void anim_face_tick(void)"):
        display.index("static uint16_t sparkle_rand16(void)")
    ]
    recognizing_block = anim_block[
        anim_block.index("case VOICE_UI_SCREEN_RECOGNIZING:"):
        anim_block.index("case VOICE_UI_SCREEN_THINKING:")
    ]
    assert "recognizing_eyes_cycle" in recognizing_block
    assert "recognizing_mouth_cycle" in recognizing_block


def test_thinking_screen_uses_dedicated_animation_cycles() -> None:
    display = _source(DISPLAY)
    anim_block = display[
        display.index("static void anim_face_tick(void)"):
        display.index("static uint16_t sparkle_rand16(void)")
    ]
    thinking_block = anim_block[
        anim_block.index("case VOICE_UI_SCREEN_THINKING:"):
        anim_block.index("case VOICE_UI_SCREEN_PLAYBACK:")
    ]
    assert "thinking_eyes_cycle" in thinking_block
    assert "thinking_mouth_cycle" in thinking_block


def test_playback_screen_uses_dedicated_animation_cycles() -> None:
    display = _source(DISPLAY)
    anim_block = display[
        display.index("static void anim_face_tick(void)"):
        display.index("static uint16_t sparkle_rand16(void)")
    ]
    playback_block = anim_block[
        anim_block.index("case VOICE_UI_SCREEN_PLAYBACK:"):
        anim_block.index("case VOICE_UI_SCREEN_IDLE:")
    ]
    assert "playback_eyes_cycle" in playback_block
    assert "playback_mouth_cycle" in playback_block


def test_ui_state_screen_text_is_chinese() -> None:
    ui_state = _source(UI_STATE)
    english_literals = [
        "Online",
        "Tap to talk",
        "Starting",
        "Initializing device",
        "Connecting to ",
        "Network ready",
        "Cloud",
        "Connecting",
        "Listening",
        "Speak now",
        "Thinking",
        "Generating reply",
        "Speaking",
        "Tap to interrupt",
    ]
    for literal in english_literals:
        assert f'"{literal}"' not in ui_state


def test_robot_face_generator_has_no_blur_or_halo_shadow() -> None:
    generator = _source(FACE_GENERATOR)
    assert "feGaussianBlur" not in generator
    assert "glowHalo" not in generator


def test_hardware_defaults_to_local_backend_when_no_saved_choice() -> None:
    ws_client = _source(WS_CLIENT)
    assert "static uint8_t s_ws_env = 0;" in ws_client


def test_hardware_boot_restores_saved_backend_choice() -> None:
    ws_client = _source(WS_CLIENT)
    load_block = ws_client[
        ws_client.index("static void ws_env_load(void)"):
        ws_client.index("void voice_ws_set_env", ws_client.index("static void ws_env_load(void)"))
    ]
    assert "s_ws_env = 0;" in load_block
    assert "nvs_get_u8" in load_block
    assert "s_ws_env = val ? 1 : 0;" in load_block


def test_hardware_volume_persists_with_default_63() -> None:
    codec = _source(ES8311_CODEC)
    # Volume is restored from NVS on boot (persisted across reboots), falling
    # back to a 63 default on first boot / when unset.
    assert "ES8311_VOL_DEFAULT 63" in codec
    assert "voice_es8311_set_volume(es8311_volume_load())" in codec
    # set_volume persists the new level so it survives a reboot.
    assert "nvs_set_u8" in codec
    assert "ES8311_VOL_NVS_KEY" in codec


def test_ws_logs_use_effective_backend_url_not_local_kconfig_url() -> None:
    ws_client = _source(WS_CLIENT)
    assert 'ESP_LOGI(TAG, "connected to %s", voice_ws_env_url(s_ws_env));' in ws_client
    assert 'ESP_LOGI(TAG, "starting websocket client to %s as device_id=%s",' in ws_client
    assert "ws_url, CONFIG_VR_DEVICE_ID" in ws_client


def test_firmware_handles_character_trace_message() -> None:
    ws_client = _source(WS_CLIENT)
    assert 'strcmp(type, "show_character_trace") == 0' in ws_client
    assert "voice_display_lcd_show_trace" in ws_client


def test_firmware_handles_idiom_message() -> None:
    ws_client = _source(WS_CLIENT)
    display = _source(DISPLAY)
    display_h = _source(ROOT / "firmware" / "main" / "display_lcd.h")
    # ws_client: parses the glyphs array and streams each char to the display.
    assert 'strcmp(type, "show_idiom") == 0' in ws_client
    assert "json_split_glyphs" in ws_client
    assert "voice_display_lcd_show_idiom_begin" in ws_client
    assert "voice_display_lcd_show_idiom_glyph" in ws_client
    # display: streaming 2×2 big-char API + static fill (no per-stroke reveal).
    assert "void voice_display_lcd_show_idiom_begin(size_t glyph_count);" in display_h
    assert "VOICE_IDIOM_MAX_GLYPHS" in display_h
    assert "idiom_fill_outline_into" in display
    assert "idiom_overlay_close" in display
    assert "idiom_touch_cb" in display
    # 2 columns → 4-char idiom lays out 2×2.
    assert "IDIOM_COLS 2" in display
    # Static fill must NOT animate: no timer / reveal-sweep in the idiom path.
    idiom_block = display[
        display.index("/* ===== 成语大字"):
        display.index("void voice_display_lcd_show_trace(const char *character,")
    ]
    assert "lv_timer_create" not in idiom_block
    assert "reveal" not in idiom_block


def test_firmware_handles_lcd_color_test_message() -> None:
    ws_client = _source(WS_CLIENT)
    display_h = _source(ROOT / "firmware" / "main" / "display_lcd.h")
    display = _source(DISPLAY)
    assert 'strcmp(type, "show_color_test") == 0' in ws_client
    assert "voice_display_lcd_show_color_test" in ws_client
    assert "void voice_display_lcd_show_color_test(const char *color);" in display_h
    assert "static void color_test_overlay_open" in display
    for color in ["red", "green", "blue", "black", "white"]:
        assert f'strcmp(name, "{color}")' in display or f'{{"{color}",' in display


def test_trace_overlay_uses_red_grid_white_strokes_without_prompt_text_or_shadow() -> None:
    display = _source(DISPLAY)
    assert "TRACE_GRID_COLOR" in display
    assert "TRACE_STROKE_COLOR" in display
    assert "0xFF0000" in display
    assert "0xFFFFFF" in display
    assert "TRACE_BG_COLOR 0x000000" in display
    assert "voice_display_lcd_show_trace" in display
    assert "trace_touch_cb" in display
    trace_block = display[
        display.index("static void trace_overlay_open"):
        display.index("static void build_widgets")
    ]
    assert "写完后" not in trace_block
    assert "汉字描红" not in trace_block
    assert "lv_obj_set_style_shadow_width" not in trace_block
    assert "TRACE_STROKE_UNDERLAY_COLOR" not in display
    assert "TRACE_STROKE_HIGHLIGHT_COLOR" not in display
    assert "trace_add_stroke_joint" in display
    assert "trace_add_segment_cap" in display
    assert "trace_create_line(s_trace_lines" in display
    assert "TRACE_STROKE_MAIN_WIDTH" in display
    assert "TRACE_STROKE_CAP_SIZE" in display
    assert "TRACE_STROKE_JOINT_SIZE" in display


def test_trace_animation_uses_server_duration_for_timer_period() -> None:
    display = _source(DISPLAY)
    assert "trace_total_segments" in display
    assert "s_trace_tick_ms = s_trace_duration_ms / total_segments;" in display
    assert "lv_timer_create(trace_timer_cb, s_trace_tick_ms, NULL)" in display


def test_trace_overlay_renders_strokes_in_multiple_layers_for_regular_script_feel() -> None:
    display = _source(DISPLAY)
    trace_add_segment_block = display[
        display.index("static void trace_add_segment"):
        display.index("static void trace_timer_cb(lv_timer_t *timer) {")
    ]
    assert trace_add_segment_block.count("trace_create_line(") == 1
    assert "TRACE_STROKE_COLOR" in trace_add_segment_block


def test_trace_and_websocket_buffers_prefer_psram_not_ws_stack() -> None:
    display = _source(DISPLAY)
    ws_client = _source(WS_CLIENT)
    assert "MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT" in display
    assert "MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT" in ws_client
    assert "voice_trace_stroke_t strokes[VOICE_TRACE_MAX_STROKES]" not in ws_client
    assert "heap_caps_calloc(VOICE_TRACE_MAX_STROKES, sizeof(voice_trace_stroke_t)" in ws_client


def test_board_psram_is_enabled_for_hanzi_trace_runtime() -> None:
    defaults = _source(SDKCONFIG_DEFAULTS)
    assert "CONFIG_SPIRAM=y" in defaults
    assert "CONFIG_SPIRAM_MODE_OCT=y" in defaults
    assert "CONFIG_SPIRAM_USE_MALLOC=y" in defaults


def test_nimble_uses_psram_and_minimal_roles_for_lcd_audio_firmware() -> None:
    defaults = _source(SDKCONFIG_DEFAULTS)
    assert "# CONFIG_BT_NIMBLE_MEM_ALLOC_MODE_INTERNAL is not set" in defaults
    assert "CONFIG_BT_NIMBLE_MEM_ALLOC_MODE_EXTERNAL=y" in defaults
    assert "# CONFIG_BT_NIMBLE_ROLE_CENTRAL is not set" in defaults
    assert "CONFIG_BT_NIMBLE_ROLE_PERIPHERAL=y" in defaults
    assert "CONFIG_BT_NIMBLE_ROLE_BROADCASTER=y" in defaults
    assert "# CONFIG_BT_NIMBLE_ROLE_OBSERVER is not set" in defaults
    assert "CONFIG_BT_NIMBLE_MAX_CONNECTIONS=1" in defaults


def test_ble_background_advertising_disabled_while_wifi_up_to_avoid_controller_oom() -> None:
    # The BT controller's link-layer init needs DMA-capable *internal* RAM (PSRAM
    # cannot back it). Starting BLE in the background while Wi-Fi + LVGL + audio +
    # the trace canvases are up OOMs the controller (`BLE_INIT: Malloc failed` ->
    # `BLE assert emi.c 164` -> interrupt-wdt panic -> boot loop). So app_main must
    # NOT start background BLE once Wi-Fi is connected; BLE stays a Wi-Fi-failure
    # fallback only (where Wi-Fi has freed its RAM).
    app_main = _source(APP_MAIN)
    ble_h = _source(BLE_GATEWAY_H)
    ble = _source(BLE_GATEWAY)
    # The background entrypoint still exists for potential future use behind PSRAM.
    assert "esp_err_t voice_ble_gateway_start_background(void);" in ble_h
    assert "static bool s_publish_ui_on_adv;" in ble
    assert "if (s_publish_ui_on_adv)" in ble
    # ...but it is not invoked from the Wi-Fi-success path.
    assert "voice_ble_gateway_start_background()" not in app_main
    assert "ble_background_skip" in app_main
    # The Wi-Fi-failure fallback still brings BLE up.
    assert "voice_ble_gateway_start()" in app_main


def test_ble_gateway_splits_advertisement_and_scan_response_to_fit_ble_limits() -> None:
    ble = _source(BLE_GATEWAY)
    assert "static char s_short_device_name[11];" in ble
    assert "void voice_ble_gateway_build_short_device_name(" in ble
    assert "voice_ble_gateway_build_short_device_name(CONFIG_VR_DEVICE_ID," in ble
    adv_block = ble[
        ble.index("static void start_advertising(void) {"):
        ble.index("struct ble_gap_adv_params params;")
    ]
    assert "fields.name = (uint8_t *)s_short_device_name;" in adv_block
    assert "fields.uuids128 = &s_service_uuid;" in adv_block
    assert "ble_gap_adv_set_fields(&fields)" in adv_block
    assert "struct ble_hs_adv_fields rsp_fields;" in adv_block
    assert "rsp_fields.name = (uint8_t *)s_device_name;" in adv_block
    assert "ble_gap_adv_rsp_set_fields(&rsp_fields)" in adv_block


def test_ble_gateway_short_adv_name_keeps_local_name_within_8_data_bytes() -> None:
    ble = _source(BLE_GATEWAY)
    short_name_block = ble[
        ble.index("void voice_ble_gateway_build_short_device_name("):
        ble.index("\n#ifdef ESP_PLATFORM", ble.index("void voice_ble_gateway_build_short_device_name("))
    ]
    assert "if (len >= 3)" in short_name_block
    assert 'snprintf(out, out_size, "VR-%s", clean);' in short_name_block


def test_wifi_builtin_path_does_not_preinitialize_ble_provisioning_manager() -> None:
    wifi = _source(WIFI_CLIENT)
    provision_block = wifi[
        wifi.index("static esp_err_t run_ble_provisioning"):
        wifi.index("esp_err_t voice_robot_wifi_start")
    ]
    assert "network_prov_mgr_init(prov_cfg)" in provision_block

    start_block = wifi[wifi.index("esp_err_t voice_robot_wifi_start"):]
    first_branch = start_block.index("if (force_prov || !legacy_creds)")
    assert "network_prov_mgr_init" not in start_block[:first_branch]


def test_wifi_primary_failure_tries_hotspot_before_hard_failure() -> None:
    wifi = _source(WIFI_CLIENT)
    assert "#define WIFI_PRESET_HOTSPOT_SSID CONFIG_VR_WIFI_HOTSPOT_SSID" in wifi
    assert "#define WIFI_PRESET_HOTSPOT_PASSWORD CONFIG_VR_WIFI_HOTSPOT_PASSWORD" in wifi
    assert "#define WIFI_CONNECT_TIMEOUT_MS 65000" in wifi
    assert "wifi_try_next_preset_after_failure()" in wifi
    assert 'primary preset failed; trying preset=%d ssid=\\"%s\\"' in wifi


def test_wifi_stops_before_ble_fallback_after_all_presets_fail() -> None:
    wifi = _source(WIFI_CLIENT)
    fail_block = wifi[
        wifi.index('ESP_LOGE(TAG, "wifi connect failed");'):
        wifi.index("return ESP_FAIL;", wifi.index('ESP_LOGE(TAG, "wifi connect failed");'))
    ]
    assert "esp_wifi_disconnect();" in fail_block
    assert "esp_wifi_stop();" in fail_block
