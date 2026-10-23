# 硬件主界面新增「霓虹蝴蝶」UI（与经典脸并存，可切换）

最后更新：2026-06-20

> 计划/实现记录。需求源自对话（参考图：紫蓝霓虹描边发光蝴蝶，星月点缀）。决策见 ADR `0007`；进展见 `../../progress/WORK-IN-PROGRESS.md`。

## 目标

为 ESP32-S3（ST7789 240×284 + LVGL 9.5 + CST816 触摸）新增一套「霓虹发光蝴蝶」主界面，与既有「机器人脸」UI 并存：

- 蝴蝶**不断变换鲜艳色彩**；
- 翅膀**煽动**；
- **表达人类表情、跟随语音对话状态切换**（idle/收听/识别/生成/播放/出错）；
- 老 UI 完整保留，设置菜单可切换；选择 NVS 持久化；**开机默认蝴蝶**。

## 最终方案（已落地、已真机验证启动）

| 维度 | 选择 |
|---|---|
| 变色 | 单精灵**白色描边底图 + LVGL `image_recolor`（`LV_OPA_COVER`）+ HSV 色相循环** |
| 煽动 | 翅膀 **3 帧位图切换**（open/mid/close，横向收窄模拟扇动） |
| 表情 | **新画蝴蝶专属**「蝶身+头部」6 帧（idle/focus/curious/think/talk/sad），叠在翅膀中央 |
| 切换 | 设置菜单「界面风格」两按钮（经典脸/蝴蝶），NVS `vrcfg/ui_style`，默认蝴蝶；切换即重建控件树，无需重启 |

### 资源

- 生成脚本 `firmware/tools/gen_butterfly_sprites.py`（复用 `gen_face_sprites.py` 的 render_png/to_c_array/shared_bbox/emit_group 基础设施；离线 Chrome 渲染白色 SVG → 裁剪 → RGB565A8 C 数组）。
- 产物 `firmware/main/butterfly_sprites.c/.h`：翅膀 190×149×3 帧 ≈255KB + 蝶身 54×138×6 帧 ≈134KB，**合计 ≈373KB flash**（app 分区烧录后仍剩 15%）。

### 固件改造（`firmware/main/display_lcd.c`）

- 枚举 `ui_style_t{FACE,BUTTERFLY}` + NVS 读写 `ui_style_load/save`（默认蝴蝶）。
- `build_widgets` 拆为 `build_widgets_common`（黑底+topbar+点击层+下拉热区+detail+字幕，两 UI 共用）/`build_widgets_face`/`build_widgets_butterfly`。
- `anim_timer_cb`（90ms/~11Hz）拆为 `anim_face_tick`/`anim_butterfly_tick`，按 `s_ui_style` 分流。
- 蝴蝶 tick：翅膀按状态切扇动帧 + **每 2 tick 走一次 HSV recolor 换色**（playback 步进更快）+ 按 `s_cur_screen` 选蝶身表情。
- `ui_style_apply`：保存→删主体精灵→重建对应分支→`common_widgets_to_front` 把共用控件移到顶层。
- last_* 帧缓存提升为文件级 + `anim_reset_caches` 重置（防重建后悬空）。
- 设置菜单加「界面风格」行 + `ui_style_btn_cb`。
- `CMakeLists.txt` SRCS 加 `butterfly_sprites.c`。

## 技术选型尝试与结论（供后续迭代参考）

> 这些结论是本次踩过/核实过的关键点，后续做任何「端侧动态图形 / 换色 / 表情」功能前先看这里，避免重复试错。

1. **变色用 `image_recolor` + `LV_OPA_COVER`，底图必须画成纯白描边**。
   - recolor + COVER 会把精灵 RGB 通道整体替换为目标色、**保留 A8 alpha**。项目早有先例：汉字描红 canvas（`display_lcd.c` 把 A8 蒙版 recolor 成白/红）。
   - 结论：**彩色底图做 recolor 得不到干净换色**（只能往某色调染、渐变被压平）。蝴蝶发光的浓淡层次全部用 **alpha**（多层递减 opacity 白描边模拟霓虹辉光）表达，颜色交给运行时 recolor。后续任何"可换色发光元素"都应沿用"白底+alpha+recolor"。

2. **HSV 换色用 `lv_color_hsv_to_rgb(h,s,v)`，纯整数实现**，`CONFIG_LV_USE_FLOAT` 关闭也能用。可逐帧 HSV 旋转，也可改预置鲜艳色板循环（更可控）。本次用 HSV，饱和度 92 / 亮度 100。

3. **换色刷新是 SPI 负载热点，必须降频**。每次 `set_style_image_recolor` 会 invalidate 整块精灵 → 每 90ms 全量重刷会拖垮 24MHz SPI。对策（已采用）：① 蝴蝶主体面积控制在 ~190×149；② **每 2~3 tick 才换一次 hue**（降到 ~4~5Hz，肉眼仍连续）；③ 沿用"仅值变化才 set/invalidate"的 last_* 防抖。

4. **扇动用位图切帧，不要用缩放**。`CONFIG_LV_USE_MATRIX` 未开 → `lv_image_set_scale` 非 256 倍会走软件变换、较慢。3 帧 open/mid/close 切换即可，零变换开销。

5. **切换 UI 重建控件树的最大坑：函数内 `static` 帧缓存悬空**。老 `anim_timer_cb` 的 `last_eyes/last_mouth/...` 是函数级 static；重建后会指向已 `lv_obj_del` 的旧对象。必须提升为文件级并在切换时 `anim_reset_caches()` 清零。

6. **共用控件层级**：重建主体精灵后，点击层/topbar/字幕/下拉热区等共用控件要 `lv_obj_move_to_index(obj,-1)` 提到顶层（LVGL9 无 `lv_obj_move_foreground`，那是 v8 兼容层），否则可能被新精灵盖住、吞触摸。

7. **表情辨识度**：最初把表情眼睛画在细长躯干上（被段纹干扰、看不清）。改为给蝴蝶加一个明显的**圆形头部**承载大眼睛+嘴，6 种表情（睁眼微笑/眯眼专注/睁大好奇/挑眉疑惑/弯眼咧嘴/垂眼含泪）一眼可辨。后续做表情优先给"头部"留足面积。

8. **资源生成沿用现有离线管线**：Chrome headless 渲染内联 SVG（不用 SVG blur，headless 支持差），Pillow 裁剪共享 bbox + 转 RGB565A8 C 数组。白色透明预览要合成到深色底才看得见。

## 验证

- `idf.py build` 通过（app 剩 15%）；真机 `/dev/cu.usbmodemXXXX` 烧录、启动日志 `main UI style: butterfly`、LCD/LVGL/触摸 OK、Wi-Fi 拿到 IP、WS `authenticated`、`[idle] 在线`，无崩溃/报错。
- 待用户真机目检：颜色循环与扇动流畅度、各状态表情切换、设置切「经典脸」即时重建并重启保持（NVS）、trace/字幕/点击/下拉在两 UI 下均正常。
