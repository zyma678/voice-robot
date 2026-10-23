#pragma once

#include "esp_err.h"
#include "ui_state.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// MakeMeAHanzi dataset max stroke count is 33 ("鱻"). Keep a small buffer so the
// device can render any supported character without truncation.
#define VOICE_TRACE_MAX_STROKES 33
#define VOICE_TRACE_MAX_POINTS_PER_STROKE 32
// Filled 楷体 stroke outline (真正正楷): one polygon per stroke. Server caps each
// contour at 96 points; we keep the largest contour per stroke.
#define VOICE_TRACE_OUTLINE_MAX_POINTS 96

typedef struct {
    int16_t x;
    int16_t y;
} voice_trace_point_t;

typedef struct {
    uint8_t point_count;
    voice_trace_point_t points[VOICE_TRACE_MAX_POINTS_PER_STROKE];
} voice_trace_stroke_t;

typedef struct {
    uint8_t point_count;
    voice_trace_point_t points[VOICE_TRACE_OUTLINE_MAX_POINTS];
} voice_trace_outline_t;

esp_err_t voice_display_lcd_start(void);
void voice_display_lcd_render(const voice_ui_state_t *state);
// strokes = medians (stroke-order/skeleton). outlines = per-stroke filled 楷体
// polygons (NULL when unavailable → falls back to the median skeleton render).
void voice_display_lcd_show_trace(const char *character,
                                  const voice_trace_stroke_t *strokes,
                                  size_t stroke_count,
                                  const voice_trace_outline_t *outlines,
                                  uint32_t duration_ms);
void voice_display_lcd_show_color_test(const char *color);

// 成语大字展示：把一条成语的若干汉字以 2×2（行优先）大字陈列，楷体轮廓静态填充
// （无描红动画）。流式 API：先 begin 打开覆盖层并按字数算 2 列布局，再对每个字调用
// _glyph 渲染到对应格——这样调用方（ws_client）可复用一份 strokes/outlines 解析缓冲，
// 不必同时持有整条成语所有字的字形数据。glyph_count 上限见 VOICE_IDIOM_MAX_GLYPHS。
#define VOICE_IDIOM_MAX_GLYPHS 8
void voice_display_lcd_show_idiom_begin(size_t glyph_count);
void voice_display_lcd_show_idiom_glyph(size_t index,
                                        const voice_trace_stroke_t *strokes,
                                        size_t stroke_count,
                                        const voice_trace_outline_t *outlines,
                                        const char *utf8_char);

// 意图场景（如英语学习）进出时临时切换主界面到「经典脸」并恢复。临时切换不写
// NVS（不污染用户在设置里选的默认界面风格）；enter 记住进入前的风格，exit 还原。
// 幂等：重复 enter 不重复记忆，未进入时 exit 无副作用。
void voice_display_lcd_scene_enter_face(void);
void voice_display_lcd_scene_exit_face(void);

#ifdef __cplusplus
}
#endif
