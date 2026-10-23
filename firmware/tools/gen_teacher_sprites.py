#!/usr/bin/env python3
"""Generate LVGL sprite frames for the on-device teacher character (小睿老师).

Pipeline (mirrors gen_cat_sprites.py — read that first):
  1. Use the embedded cartoon-female-teacher SVG below (viewBox 0 0 120 120,
     inline fill/stroke, same drawing style as web_console <svg class="cat"> /
     <svg class="princess">). Self-contained: the device art lives here.
  2. Render each animation frame to a transparent PNG with headless Chrome,
     re-creating expression keyframes as static CSS transforms
     (blink = eyes scaleY 0.1, talk = mouth scaleY larger, head_tilt = rotate).
  3. Crop every frame to one shared bounding box (so they overlay perfectly),
     then emit firmware/main/teacher_sprites.c/.h as LVGL RGB565A8 images.

RGB565A8 layout (verified against LVGL 9.5 src/draw/sw/lv_draw_sw_img.c):
  [w*h*2 bytes RGB565, little-endian, stride=w*2] followed by
  [w*h   bytes A8,                  stride=w].

Regenerate:  .venv/bin/python firmware/tools/gen_teacher_sprites.py
Requires: Google Chrome (macOS app path below) + Pillow (in .venv).
"""

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_C = os.path.join(REPO, "firmware", "main", "teacher_sprites.c")
OUT_H = os.path.join(REPO, "firmware", "main", "teacher_sprites.h")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Canvas size the SVG (viewBox 0 0 120 120) is rendered at. The head fills most
# of the canvas; the final asset is cropped to the figure's bounding box.
RENDER_PX = 150
ALPHA_THRESHOLD = 16  # alpha above this counts as "content" for the crop bbox

# Cartoon female teacher head — friendly primary-school teacher "小睿老师".
# Same inline-colour style as the web console characters. Animation hooks:
#   .tc-eye (both eyes)  -> blink, .tc-mouth -> talk, .tc-head-g -> head_tilt.
TEACHER_SVG = """
<svg class="teacher" viewBox="0 0 120 120" aria-hidden="true">
  <defs>
    <linearGradient id="tcHair" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#6d4630"/><stop offset="1" stop-color="#4a2e1c"/>
    </linearGradient>
    <linearGradient id="tcSkin" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffe2c6"/><stop offset="1" stop-color="#ffd2ad"/>
    </linearGradient>
    <linearGradient id="tcShirt" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#7fb8ff"/><stop offset="1" stop-color="#4a8fe0"/>
    </linearGradient>
  </defs>
  <g class="tc-head-g">
    <!-- shoulders / blouse + collar -->
    <path d="M30 116 Q60 96 90 116 Z" fill="url(#tcShirt)"/>
    <path d="M48 100 L60 110 L72 100 L66 96 L60 102 L54 96 Z" fill="#fdfdff"/>
    <!-- hair back -->
    <path d="M24 60 Q18 96 34 110 L44 100 Q30 86 32 64 Z" fill="url(#tcHair)"/>
    <path d="M96 60 Q102 96 86 110 L76 100 Q90 86 88 64 Z" fill="url(#tcHair)"/>
    <ellipse cx="60" cy="50" rx="36" ry="36" fill="url(#tcHair)"/>
    <!-- face -->
    <ellipse cx="60" cy="56" rx="28" ry="30" fill="url(#tcSkin)" stroke="#e9b58c" stroke-width="1"/>
    <!-- ears + tiny earrings -->
    <circle cx="32" cy="58" r="5.2" fill="url(#tcSkin)" stroke="#e9b58c" stroke-width="0.8"/>
    <circle cx="88" cy="58" r="5.2" fill="url(#tcSkin)" stroke="#e9b58c" stroke-width="0.8"/>
    <circle cx="32" cy="64" r="1.8" fill="#ffd23e" stroke="#e0a000" stroke-width="0.6"/>
    <circle cx="88" cy="64" r="1.8" fill="#ffd23e" stroke="#e0a000" stroke-width="0.6"/>
    <!-- bangs (swept fringe) -->
    <path d="M32 44 Q34 22 60 22 Q86 22 88 44 Q74 30 60 33 Q46 30 32 44 Z" fill="url(#tcHair)"/>
    <path d="M60 33 Q44 33 33 46 Q40 36 56 35 Z" fill="#5c3a26"/>
    <!-- side hair tuck -->
    <path d="M84 40 Q92 56 84 80 Q90 56 80 42 Z" fill="#5c3a26"/>
    <!-- eyebrows -->
    <path d="M40 44 Q48 40 56 44" fill="none" stroke="#5c3a26" stroke-width="2" stroke-linecap="round"/>
    <path d="M64 44 Q72 40 80 44" fill="none" stroke="#5c3a26" stroke-width="2" stroke-linecap="round"/>
    <!-- blush -->
    <ellipse cx="42" cy="64" rx="5" ry="3" fill="#ff9bbb" opacity="0.55"/>
    <ellipse cx="78" cy="64" rx="5" ry="3" fill="#ff9bbb" opacity="0.55"/>
    <!-- glasses bridge + rims (teacher detail) -->
    <line x1="57" y1="55" x2="63" y2="55" stroke="#3a3f4a" stroke-width="1.6"/>
    <rect x="38" y="48" width="19" height="15" rx="7.5" fill="none" stroke="#3a3f4a" stroke-width="1.8"/>
    <rect x="63" y="48" width="19" height="15" rx="7.5" fill="none" stroke="#3a3f4a" stroke-width="1.8"/>
    <!-- eyes (big, warm brown) -->
    <g class="tc-eye tc-eye-l">
      <ellipse class="tc-eye-white" cx="47.5" cy="55" rx="7" ry="8" fill="#fdfdff"/>
      <circle class="tc-iris" cx="47.5" cy="56" r="5" fill="#6b4a2e"/>
      <circle class="tc-pupil" cx="47.5" cy="56" r="2.6" fill="#1b2330"/>
      <circle class="tc-shine" cx="45.5" cy="53.5" r="1.8" fill="#ffffff"/>
    </g>
    <g class="tc-eye tc-eye-r">
      <ellipse class="tc-eye-white" cx="72.5" cy="55" rx="7" ry="8" fill="#fdfdff"/>
      <circle class="tc-iris" cx="72.5" cy="56" r="5" fill="#6b4a2e"/>
      <circle class="tc-pupil" cx="72.5" cy="56" r="2.6" fill="#1b2330"/>
      <circle class="tc-shine" cx="70.5" cy="53.5" r="1.8" fill="#ffffff"/>
    </g>
    <!-- nose -->
    <path d="M59 62 Q60 66 62 67" fill="none" stroke="#e9a87f" stroke-width="1.4" stroke-linecap="round"/>
    <!-- friendly smile (mouth) -->
    <ellipse class="tc-mouth" cx="60" cy="74" rx="7" ry="3.4" fill="#d4566f"/>
    <path class="tc-mouth" d="M53 73 Q60 80 67 73" fill="none" stroke="#b03b54" stroke-width="1.2" stroke-linecap="round"/>
  </g>
</svg>
"""

# Frame name -> extra CSS overriding the resting pose.
FRAMES = {
    "neutral": "",
    "blink": ".tc-eye{transform-box:fill-box;transform-origin:center;transform:scaleY(0.1)}",
    "talk": ".tc-mouth{transform-box:fill-box;transform-origin:center;transform:scaleY(1.9)}",
    "talk2": ".tc-mouth{transform-box:fill-box;transform-origin:center;transform:scaleY(1.35) scaleX(0.8)}",
    "tilt": ".tc-head-g{transform-box:view-box;transform-origin:60px 70px;transform:rotate(-7deg)}"
            ".tc-iris,.tc-pupil,.tc-shine{transform-box:fill-box;transform-origin:center;transform:translateY(-2px)}",
}


def render_png(svg, extra_css, out_png):
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;background:transparent}}
svg.teacher{{width:{RENDER_PX}px;height:{RENDER_PX}px;display:block}}
{extra_css}
</style></head><body>{svg}</body></html>"""
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        html_path = f.name
    try:
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--force-device-scale-factor=1",
             "--default-background-color=00000000", f"--window-size={RENDER_PX},{RENDER_PX}",
             "--hide-scrollbars", f"--screenshot={out_png}", html_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        os.unlink(html_path)


def to_c_array(name, img):
    w, h = img.size
    px = img.load()
    color_bytes = bytearray()
    alpha_bytes = bytearray()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            color_bytes.append(rgb565 & 0xFF)        # little-endian
            color_bytes.append((rgb565 >> 8) & 0xFF)
            alpha_bytes.append(a)
    data = bytes(color_bytes) + bytes(alpha_bytes)

    lines = [f"static const uint8_t {name}_map[] = {{"]
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        lines.append("    " + "".join(f"0x{b:02x}," for b in chunk))
    lines.append("};")
    lines.append("")
    lines.append(f"const lv_image_dsc_t {name} = {{")
    lines.append("    .header = {")
    lines.append("        .cf = LV_COLOR_FORMAT_RGB565A8,")
    lines.append("        .magic = LV_IMAGE_HEADER_MAGIC,")
    lines.append(f"        .w = {w},")
    lines.append(f"        .h = {h},")
    lines.append(f"        .stride = {w * 2},")
    lines.append("    },")
    lines.append(f"    .data_size = sizeof({name}_map),")
    lines.append(f"    .data = {name}_map,")
    lines.append("};")
    return "\n".join(lines)


def main():
    from PIL import Image

    tmpdir = tempfile.mkdtemp(prefix="teacher_")
    images = {}
    for name, css in FRAMES.items():
        png = os.path.join(tmpdir, f"{name}.png")
        render_png(TEACHER_SVG, css, png)
        images[name] = Image.open(png).convert("RGBA")

    # Shared crop bbox = union of every frame's content, so frames overlay 1:1.
    bbox = None
    for img in images.values():
        b = img.getchannel("A").point(lambda a: 255 if a > ALPHA_THRESHOLD else 0).getbbox()
        if b is None:
            continue
        bbox = b if bbox is None else (
            min(bbox[0], b[0]), min(bbox[1], b[1]),
            max(bbox[2], b[2]), max(bbox[3], b[3]))
    if bbox is None:
        sys.exit("all frames rendered empty")
    x0, y0, x1, y1 = bbox
    x0, y0 = max(0, x0 - 1), max(0, y0 - 1)
    x1, y1 = min(RENDER_PX, x1 + 1), min(RENDER_PX, y1 + 1)
    bbox = (x0, y0, x1, y1)
    w, h = x1 - x0, y1 - y0

    for name in FRAMES:
        preview = os.path.join(tmpdir, f"{name}_cropped.png")
        images[name].crop(bbox).save(preview)
        print(f"preview PNG: {preview}")

    parts = [
        "/* AUTO-GENERATED by firmware/tools/gen_teacher_sprites.py — do not edit by hand.",
        " * On-device teacher sprite frames (LVGL RGB565A8), rendered from the",
        " * embedded cartoon-female-teacher SVG (小睿老师). */",
        "#include \"teacher_sprites.h\"",
        "",
    ]
    for name in FRAMES:
        cropped = images[name].crop(bbox)
        parts.append(to_c_array(f"teacher_{name}", cropped))
        parts.append("")
    open(OUT_C, "w").write("\n".join(parts))

    header = [
        "/* AUTO-GENERATED by firmware/tools/gen_teacher_sprites.py — do not edit by hand. */",
        "#pragma once",
        "",
        "#include \"lvgl.h\"",
        "",
        f"#define TEACHER_SPRITE_W {w}",
        f"#define TEACHER_SPRITE_H {h}",
        "",
    ]
    for name in FRAMES:
        header.append(f"extern const lv_image_dsc_t teacher_{name};")
    header.append("")
    open(OUT_H, "w").write("\n".join(header))

    print(f"teacher sprites: {len(FRAMES)} frames, {w}x{h}px")
    print(f"  -> {OUT_C}")
    print(f"  -> {OUT_H}")
    print(f"  flash ≈ {len(FRAMES) * w * h * 3} bytes (RGB565A8)")


if __name__ == "__main__":
    main()
