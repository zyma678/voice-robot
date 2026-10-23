# 0007. 端侧「蝴蝶」UI 用单精灵 recolor 色相循环实现动态变色，而非多帧位图

- 状态：已接受
- 日期：2026-06-20
- 相关：计划 `../../design/plans/2026-06-20-butterfly-main-ui.md`；代码 `firmware/main/display_lcd.c`、`firmware/main/butterfly_sprites.c/.h`、`firmware/tools/gen_butterfly_sprites.py`；进展 `../../progress/WORK-IN-PROGRESS.md`

## 背景

新增「霓虹发光蝴蝶」主界面，要求蝴蝶**不断变换鲜艳色彩** + 翅膀煽动 + 跟随语音状态切表情，与经典脸 UI 并存可切换。约束：ESP32-S3 + ST7789(240×284) + LVGL 9.5，SPI 24MHz，UI 在 ~11Hz 的 `lv_timer` 上刷新，app 分区与内部 RAM 都不宽裕（PSRAM 已启用但 SPI flush 仍是瓶颈，且历史上 RAM 紧张已多次致问题）。「不断变色」是核心诉求，实现方式直接决定 flash/带宽成本。

## 备选方案

- **方案 A：单精灵白色描边底图 + LVGL `image_recolor`(`LV_OPA_COVER`) + HSV 色相循环**
  - 优点：每个色相一份图也不用存，flash 只放一套白图；变色是改 style，无新位图；与项目既有 A8+recolor 先例（汉字描红 canvas）一致。
  - 缺点：彩色源图无法干净换色（必须白底），渐变靠 alpha 表达；recolor 每帧 invalidate 是 SPI 热点，需降频。
- **方案 B：离线渲染多张不同颜色的彩色蝴蝶位图，循环播放**
  - 优点：还原度高、颜色完全可控。
  - 缺点：每个颜色一份全图，flash 成本 = 帧数×尺寸×颜色数，轻易上 MB；SPI 带宽与解码压力大；变色"档位"离散不平滑。
- **方案 C：`lv_canvas`/矢量实时绘制蝴蝶并实时改色**
  - 优点：最灵活。
  - 缺点：实现复杂、CPU 占用高、霓虹发光质感难还原；与现有"内嵌精灵"管线不一致。

## 决策

选择**方案 A**。蝴蝶底图一律画成纯白描边（发光浓淡用 alpha 叠层表达），运行时用 `lv_color_hsv_to_rgb` 每 2~3 个 tick 旋转一次色相并 `lv_obj_set_style_image_recolor(..., LV_OPA_COVER)` 给整只蝴蝶上色；翅膀煽动用 3 帧位图切换（不用缩放，因 `LV_USE_MATRIX` 未开）；表情用新画的 6 帧蝶身精灵。理由：在"鲜艳连续变色"诉求下，A 的 flash/带宽成本最低（一套白图 ≈373KB vs B 的成倍增长）、变色最平滑，且复用了已验证的 recolor 管线。

## 后果

- 正面：flash 省（仅一套白图）、变色平滑可调速、与描红 canvas 复用同一 recolor 认知；新旧 UI 并存、NVS 持久化、切换无需重启。
- 负面 / 代价：底图被限制为"白色 + alpha 辉光"，无法直接表达多色渐变；recolor 触发整块 invalidate，是 SPI 负载热点，必须靠"限面积 + 降频换色（每 2~3 tick）+ 仅值变化才刷"压住。
- 后续需要关注 / 偿还的点：
  - 真机长时间运行的 SPI 负载与音频路径是否互扰（已分属不同 task，启动验证无异常，待长稳观察）。
  - 切换 UI 重建控件树时，帧缓存须提升文件级并重置（已做 `anim_reset_caches`），共用控件须 `lv_obj_move_to_index(obj,-1)` 提顶（LVGL9 无 `move_foreground`）。
  - 若未来要真正的多色渐变/更复杂动态，再评估方案 C 或预置色板。
