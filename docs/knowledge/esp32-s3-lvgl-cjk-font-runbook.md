# ESP32-S3 LVGL 中文字体显示排障记录

最后更新：2026-06-14

> 适用：Waveshare ESP32-S3-Touch-LCD-1.83 + ESP-IDF + LVGL 9.5  
> 对应代码：`firmware/main/display_lcd.c`、`firmware/main/lv_font_cjk_sc_16.c`、`firmware/sdkconfig.defaults`、`tests/test_firmware_display_static.py`

## 一、现象

真机 LCD 上文字不显示：

- 初始问题：中文状态文案不显示。
- 回归后问题：改用项目内 CJK 字体后，连原本能显示的英文/ASCII 状态也不显示。
- 串口日志仍正常输出 UI 状态，例如 `ui: [idle] 在线 | 轻触说话 |`，但屏幕实际没有文字。

关键判断：串口 UI 日志只证明状态机输出正常，不证明 LCD/LVGL 实际渲染成功。硬件 UI 验收必须以真机屏幕肉眼显示为准。

## 二、根因

项目内中文字体 `firmware/main/lv_font_cjk_sc_16.c` 由 `lv_font_conv` 生成，字体描述符里是：

```c
.bitmap_format = 1,
```

在 LVGL 中：

```c
LV_FONT_FMT_TXT_PLAIN = 0
LV_FONT_FMT_TXT_COMPRESSED = 1
```

也就是说，该字体是压缩位图字体。但当时固件配置只有：

```text
CONFIG_LV_FONT_FMT_TXT_LARGE=y
# CONFIG_LV_USE_FONT_COMPRESSED is not set
```

LVGL `lv_font_get_bitmap_fmt_txt()` 遇到压缩字体且未启用 `LV_USE_FONT_COMPRESSED` 时会返回 `NULL`，并输出警告：

```text
Compressed fonts is used but LV_USE_FONT_COMPRESSED is not enabled in lv_conf.h
```

因此，所有设置为 `&lv_font_cjk_sc_16` 的 label 都无法取到 glyph bitmap。ASCII/英文也会受影响，因为这些 label 已经绑定到该 CJK 字体，而不是 Montserrat。

## 三、修复

在固件默认配置和当前 `sdkconfig` 中启用 LVGL 压缩字体支持：

```text
CONFIG_LV_USE_FONT_COMPRESSED=y
```

当前落点：

- `firmware/sdkconfig.defaults`
- `firmware/sdkconfig`

同时新增静态回归测试：如果 `lv_font_cjk_sc_16.c` 的 `.bitmap_format` 是 `1` 或 `2`，则 `firmware/sdkconfig.defaults` 必须包含 `CONFIG_LV_USE_FONT_COMPRESSED=y`。

测试位置：

```text
tests/test_firmware_display_static.py::test_cjk_font_bitmap_format_matches_lvgl_config
```

## 四、验证方法

推荐按以下顺序验证，避免只看串口日志就误判屏幕已正常：

```bash
.venv/bin/pytest -q tests/test_firmware_display_static.py tests/test_firmware_host_tests.py tests/test_flash_firmware_script.py

cd firmware
source /esp/esp-idf/export.sh
idf.py -B build_flash_real build
idf.py -B build_flash_real -p /dev/cu.usbmodemXXXX flash
idf.py -B build_flash_real -p /dev/cu.usbmodemXXXX monitor
```

monitor 中至少确认：

```text
display: LCD + LVGL ready
ui: [idle] 在线 | 轻触说话 |
```

并确认没有出现：

```text
Compressed fonts is used but LV_USE_FONT_COMPRESSED is not enabled
```

最后必须肉眼确认真机 LCD 上能显示中文，例如“在线”“轻触说话”。

## 五、排障清单

遇到“串口 UI 正常但屏幕文字不显示”时，按顺序查：

1. `VR_BOARD_UI_ENABLE=y`，且启动日志有 `LCD + LVGL ready`。
2. 字体文件里的 `.bitmap_format`：
   - `0`：plain 字体，不要求 `CONFIG_LV_USE_FONT_COMPRESSED`。
   - `1` / `2`：压缩字体，必须启用 `CONFIG_LV_USE_FONT_COMPRESSED=y`。
3. 构建产物里确认配置生效：
   ```bash
   rg -n "CONFIG_LV_USE_FONT_COMPRESSED" firmware/build_flash_real/config
   ```
   预期能看到 `#define CONFIG_LV_USE_FONT_COMPRESSED 1`。
4. monitor 里查 LVGL 字体 warning。
5. 真机屏幕肉眼确认文字，而不是只看串口 `ui:` 日志。

## 六、相关注意事项

- `CONFIG_LV_FONT_FMT_TXT_LARGE=y` 只解决大字符集字体索引容量问题，不能解压压缩字体。
- `.fallback = &lv_font_montserrat_14` 只能在 glyph 查找不到时兜底；如果 glyph 在 CJK 字体中但 bitmap 因压缩配置不匹配而解码失败，fallback 不会解决。
- UI 阴影是另一个独立问题：`display_lcd.c` 中按钮/card 已显式设置 `lv_obj_set_style_shadow_width(..., 0, 0)`；精灵生成器也不应引入 blur/halo。
