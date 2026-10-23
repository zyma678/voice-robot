# 汉字描红「真正正楷」改造计划

最后更新：2026-06-16

> 背景：当前描红只把 MakeMeAHanzi 的**笔画中线（medians）**画成等宽圆头折线 —— 是「骨架」而非正楷。
> 目标：用数据集里的**楷体填充轮廓（strokes，SVG path）**渲染真正的正楷字形，并保留逐笔书写动画。
> 关联：WS 文本帧重组修复见 `../../progress/WORK-IN-PROGRESS.md`（2026-06-16）。

## 方案（已与用户确认：方案 1）

**服务端展开轮廓 + 端侧填充**，符合「服务端权威 + 瘦客户端」架构（ADR 0005）：

- **服务端**：解析每个笔画的楷体 SVG 轮廓路径（命令仅 `M/L/Q/C/Z`），把贝塞尔展开为多边形顶点，按整字 ink 包围盒统一归一化到 `0..1000`（居中、留白、翻转 Y，与 medians 同一变换以对齐），随 `show_character_trace` 下发新字段 `outlines`。
- **Web**：用楷体轮廓**填充**笔画，并沿中线做**扫描显影**（HanziWriter 思路：以轮廓为裁剪区，沿 median 扫一条粗线显影），呈现「毛笔按笔顺写出正楷笔画」。
- **固件（ESP32-S3，无 PSRAM，LVGL 9.5 无 ThorVG/FreeType）**：扫描线填充多边形到 A8 蒙版画布（~40KB 瞬时），按笔顺逐笔显影，再以白色 ink blit。不引入 ThorVG（boot loop 风险）。

排除项：ThorVG 矢量填充（内存风险）、FreeType 楷体字库（丢笔顺动画 + 占 flash）。

## 协议 / payload 变更

`character_trace_payload` / `show_character_trace.payload` 在保留现有字段基础上新增 `outlines`：

```jsonc
{
  "character": "象",
  "duration_ms": 1800,
  "strokes":  [[[x,y],...], ...],     // 既有：中线（显影方向/进度 + 旧固件兜底），不变语义
  "outlines": [ [ [[x,y],...contour], ...], ... ] // 新增：每笔的楷体填充轮廓（多边形，可多 contour），0..1000
}
```

- `outlines[i]` 与 `strokes[i]` 同序、同变换，逐笔对应。
- 数据集缺该字（落到 `_STROKE_DATA` 手写兜底）时 `outlines` 省略 → Web/固件回退到中线骨架渲染。
- 顶点做距离阈值简化 + 每 contour 上限，控制体积（复杂字 outlines 比中线大，靠已修复的 WS 重组承载）。

## 分期与状态

- **A 期（Web 先行，本期）**：服务端 `outlines` + Web 正楷填充/显影 + 测试。完成后**暂停让用户在 web 端确认效果**。
  - [x] 服务端解析/展开/归一化 + payload `outlines`（`character_trace.py`：`_parse_svg_outline`/`_cubic_samples`/RDP 简化；归一化改用 outline ink 包围盒，medians 与 outlines 共用变换）
  - [x] 更新 `tests/test_character_trace.py`（新增 outlines 断言）；`test_trace_api.py`/`test_protocol.py` 保持通过
  - [x] Web `renderTraceCanvas` 正楷填充 + 沿中线显影（`renderTraceKaiti`/`fillStrokeOutline`/`revealStrokeOutline`；淡红整字模板 + 白色逐笔书写）
  - [x] `node --check` 通过；`pytest -q` 待确认
- **B 期（固件，已实现，待烧录）**：
  - [x] 固件解析 payload `outlines`（`ws_client.c` `json_extract_trace_outlines`：嵌套 [笔][轮廓][点]，每笔取最大轮廓为单多边形；堆缓冲避免撑爆 WS 任务栈）。
  - [x] 渲染（`display_lcd.h`/`display_lcd.c`）：A8 蒙版画布（200×200，native 无缩放走 LVGL A8 快路径，recolor 白）+ 偶奇规则扫描线填充 `trace_canvas_fill_stroke`；`trace_timer_cb` 逐笔显影；`heap_caps_malloc` 失败优雅回退中线骨架。
  - [x] 顺带修复潜伏 bug：`trace_overlay_open()` 原首行 `trace_overlay_close()` 会把调用方刚设好的 strokes 清零（真机此前因 WS bug 没走到、无渲染测试，故没暴露）——改为调用方 `show_trace` 先 close 再设状态再 open。
  - [x] `idf.py build` 通过（app 24% free）；firmware host C 单测全过；`test_firmware_display_static.py` 无新增失败。
  - [x] 优化：**沿中线扫描显影**（`trace_canvas_reveal_stroke` 按 median 方向投影裁剪，每笔 `TRACE_KAITI_SUBSTEPS=6` 子步，末步无裁剪保证笔锋/收笔完整）。
  - [x] 优化：**淡红整字模板**（第二块 A8 画布在 ink 下层，开场一次性填全字、red recolor、alpha `TRACE_TEMPLATE_ALPHA=70`；其 ~40KB 缓冲独立 guard，分配失败仅跳过模板、不影响书写）。
  - [x] 多 contour 带孔笔画：**确认无需**——数据集每笔均为单 contour（孔在字级、非笔级，象/警等实测 contour 数全为 1），「取最大轮廓」即正确。
  - [x] `idf.py build` 通过（B 期+优化）；host C 单测全过；display static 无新增失败。
  - [ ] **烧录真机联调**（本机未接板）：验证正楷字形、逐笔扫描显影、淡红模板、写完点击退出；同时验证 WS 重组 + 红色（R/B）修复。Web 端已具备模板/扫描显影/多 contour 同等效果。

## 风险

- 固件内存：A8 蒙版用完即释放；必要时缩小蒙版分辨率或限制并发对象。
- payload 体积：靠简化 + 上限 + WS 重组；固件侧解析需限幅防溢出。
