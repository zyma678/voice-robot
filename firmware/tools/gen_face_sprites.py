#!/usr/bin/env python3
"""Generate LVGL sprite frames for the on-device robot face — a pair of cyan
cartoon EYES plus a cyan MOUTH, composited on a pure-black screen.

Aesthetic: OLED-style black background, crisp cyan eyes and mouth with no
drop-shadow/blur/halo, dark pupils with a top-left white highlight; the tongue
is red. Eyes and mouth are SEPARATE transparent sprites so the mouth can animate
(talking open/close) independently of the eyes.

Pipeline (mirrors gen_teacher_sprites.py — read that first):
  1. Embedded SVGs below (one per eye-frame / mouth-frame), inline fill/stroke,
     no blur filters.
  2. Render each frame to a transparent PNG with headless Chrome.
  3. Crop the EYE frames to one shared bbox, and the MOUTH frames to another
     shared bbox (so each group overlays 1:1), then emit
     firmware/main/face_eyes_sprites.c/.h and face_mouth_sprites.c/.h as LVGL
     RGB565A8 images.

RGB565A8 layout (verified against LVGL 9.5 src/draw/sw/lv_draw_sw_img.c):
  [w*h*2 bytes RGB565, little-endian, stride=w*2] then [w*h bytes A8, stride=w].

Regenerate:  .venv/bin/python firmware/tools/gen_face_sprites.py
Requires: Google Chrome (macOS app path below) + Pillow (in .venv).
"""

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN = os.path.join(REPO, "firmware", "main")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Eyes are rendered on a wide canvas (both eyes side by side); the mouth on a
# smaller canvas. Final assets are cropped to each group's union bbox.
EYES_PX_W, EYES_PX_H = 240, 150
MOUTH_PX_W, MOUTH_PX_H = 150, 110
ALPHA_THRESHOLD = 12  # alpha above this counts as "content" for the crop bbox

# Shared SVG <defs>: cyan fill + pupil gradients. Reused by all eye/mouth frames
# so the visual language is consistent.
DEFS = """
  <defs>
    <radialGradient id="cyanFill" cx="50%" cy="40%" r="65%">
      <stop offset="0" stop-color="#f0ffff"/>
      <stop offset="0.5" stop-color="#bdeeff"/>
      <stop offset="1" stop-color="#7fdcff"/>
    </radialGradient>
    <linearGradient id="pupilGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#02060c"/>
      <stop offset="0.6" stop-color="#04101e"/>
      <stop offset="1" stop-color="#0c4a78"/>
    </linearGradient>
  </defs>
"""

# --- Eye building blocks -------------------------------------------------
# Eye centres on the 240x150 canvas. Eyes are large, sitting upper/centre.
# Keep a little more air between the two eyes so the robot reads less "crossed"
# on the 240 px panel without changing the rest of the face proportions.
LX, RX, EY = 62, 178, 70   # left-eye cx, right-eye cx, eye cy
ER = 38                    # eye radius

def _halo(cx, cy, r):
    return ""

def _eyeball(cx, cy, r):
    """Bright cyan eyeball disc with a crisp edge."""
    return (_halo(cx, cy, r * 1.25)
            + f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#cyanFill)"/>'
            + f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#e8feff" '
              f'stroke-width="2" opacity="0.85"/>')

def _pupil(cx, cy, pr=18, off=6):
    """Dark pupil (gradient: near-black with cyan-blue lower edge) + white hi-light."""
    return (f'<circle cx="{cx}" cy="{cy + off}" r="{pr}" fill="url(#pupilGrad)"/>'
            + f'<circle cx="{cx}" cy="{cy + off + pr - 6}" r="{pr - 4}" fill="none" '
              f'stroke="#1f7fb8" stroke-width="2" opacity="0.5"/>'
            + f'<circle cx="{cx - pr*0.42}" cy="{cy + off - pr*0.42}" r="{pr*0.34}" fill="#ffffff"/>')

def eyes_open(pupil_off=6):
    """Both eyes wide open, round pupils."""
    return (_eyeball(LX, EY, ER) + _eyeball(RX, EY, ER)
            + _pupil(LX, EY, 18, pupil_off) + _pupil(RX, EY, 18, pupil_off))

def eyes_surprised():
    """Big wide-open eyes, larger eyeball, small centred pupil (focused)."""
    r = ER + 4
    return (_eyeball(LX, EY, r) + _eyeball(RX, EY, r)
            + _pupil(LX, EY, 15, 2) + _pupil(RX, EY, 15, 2))

def _crescent(cx, cy, r):
    """Happy squint: an upward-smiling crescent (ends tilt up, belly dips down)."""
    y0 = cy - r * 0.35
    return (_halo(cx, cy, r * 1.1)
            + f'<path d="M{cx-r} {y0} Q{cx} {y0+r*1.25} {cx+r} {y0}" '
              f'fill="none" stroke="url(#cyanFill)" stroke-width="12" '
              f'stroke-linecap="round"/>'
            + f'<path d="M{cx-r} {y0} Q{cx} {y0+r*1.25} {cx+r} {y0}" '
              f'fill="none" stroke="#e8feff" stroke-width="4.5" stroke-linecap="round" opacity="0.85"/>')

def eyes_happy():
    return _crescent(LX, EY, ER) + _crescent(RX, EY, ER)

def _line_eye(cx, cy, r):
    """Closed flat line eye (blink / sing) — a solid bright cyan bar, slightly
    bowed downward at the centre so it reads as a relaxed closed lid."""
    return (_halo(cx, cy, r * 0.85)
            + f'<path d="M{cx-r} {cy-2} Q{cx} {cy+6} {cx+r} {cy-2}" '
              f'fill="none" stroke="#bdeeff" stroke-width="13" stroke-linecap="round"/>'
            + f'<path d="M{cx-r} {cy-2} Q{cx} {cy+6} {cx+r} {cy-2}" '
              f'fill="none" stroke="#eaffff" stroke-width="5" stroke-linecap="round"/>')

def eyes_blink():
    return _line_eye(LX, EY, ER) + _line_eye(RX, EY, ER)

def _brow(cx, inner_dx, outer_dx, y_in, y_out):
    """Thick black angry eyebrow sloping down toward the centre."""
    x1 = cx + outer_dx
    x2 = cx + inner_dx
    return (f'<line x1="{x1}" y1="{y_out}" x2="{x2}" y2="{y_in}" '
            f'stroke="#000000" stroke-width="13" stroke-linecap="round"/>')

def eyes_angry():
    # eyes look down; thick brows slope toward centre (inner edge lower).
    base = (_eyeball(LX, EY, ER) + _eyeball(RX, EY, ER)
            + _pupil(LX, EY, 16, 14) + _pupil(RX, EY, 16, 14))
    brows = (_brow(LX, 26, -30, EY - 4, EY - 26)
             + _brow(RX, -26, 30, EY - 4, EY - 26))
    return base + brows

def eyes_sad():
    # eyes with a cyan teardrop below each.
    base = eyes_open(8)
    tear = ""
    for cx in (LX, RX):
        ty = EY + ER + 1
        tx = cx + 4
        tear += (_halo(tx, ty + 12, 18)
                 + f'<path d="M{tx} {ty} C{tx-8} {ty+16} {tx-8} {ty+30} {tx} {ty+32} '
                   f'C{tx+8} {ty+30} {tx+8} {ty+16} {tx} {ty} Z" '
                   f'fill="url(#cyanFill)"/>'
                 + f'<path d="M{tx} {ty} C{tx-8} {ty+16} {tx-8} {ty+30} {tx} {ty+32} '
                   f'C{tx+8} {ty+30} {tx+8} {ty+16} {tx} {ty} Z" '
                   f'fill="none" stroke="#eaffff" stroke-width="1.5" opacity="0.8"/>')
    return base + tear

def eyes_question():
    # open eyes, pupil holds a cyan "?".
    base = (_eyeball(LX, EY, ER) + _eyeball(RX, EY, ER)
            + _pupil(LX, EY, 18, 4) + _pupil(RX, EY, 18, 4))
    q = ""
    for cx in (LX, RX):
        # Bright, bold "?" centred over the dark pupil — stroke for crisp edges
        # plus a brighter fill so it stays legible against the dark pupil.
        q += (f'<text x="{cx}" y="{EY+15}" font-family="Arial, sans-serif" '
              f'font-size="34" font-weight="bold" fill="#eaffff" '
              f'stroke="#7fdcff" stroke-width="1" '
              f'text-anchor="middle">?</text>')
    return base + q

EYE_FRAMES = {
    "neutral": eyes_open(6),
    "blink": eyes_blink(),
    "happy": eyes_happy(),
    "surprised": eyes_surprised(),
    "angry": eyes_angry(),
    "sad": eyes_sad(),
    "question": eyes_question(),
}

# --- Mouth building blocks ----------------------------------------------
# Mouth centred on the 150x110 canvas.
MX, MY = 75, 52

def _m_halo(cx, cy, r):
    return ""

def mouth_smile_small():
    """Small upward cyan smile arc."""
    return (_m_halo(MX, MY, 30)
            + f'<path d="M{MX-26} {MY-4} Q{MX} {MY+18} {MX+26} {MY-4}" '
              f'fill="none" stroke="url(#cyanFill)" stroke-width="7" '
              f'stroke-linecap="round"/>'
            + f'<path d="M{MX-26} {MY-4} Q{MX} {MY+18} {MX+26} {MY-4}" '
              f'fill="none" stroke="#e8feff" stroke-width="3" stroke-linecap="round" opacity="0.8"/>')

def mouth_frown():
    """Small downward sad/frown arc."""
    return (_m_halo(MX, MY, 26)
            + f'<path d="M{MX-22} {MY+8} Q{MX} {MY-12} {MX+22} {MY+8}" '
              f'fill="none" stroke="url(#cyanFill)" stroke-width="7" '
              f'stroke-linecap="round"/>'
            + f'<path d="M{MX-22} {MY+8} Q{MX} {MY-12} {MX+22} {MY+8}" '
              f'fill="none" stroke="#e8feff" stroke-width="3" stroke-linecap="round" opacity="0.8"/>')

def _open_mouth(rx, ry):
    """Open mouth outline (cyan ring, dark interior) — talking shapes."""
    return (_m_halo(MX, MY, max(rx, ry) + 10)
            + f'<ellipse cx="{MX}" cy="{MY}" rx="{rx}" ry="{ry}" fill="#03121f"/>'
            + f'<ellipse cx="{MX}" cy="{MY}" rx="{rx}" ry="{ry}" fill="none" '
              f'stroke="url(#cyanFill)" stroke-width="6"/>'
            + f'<ellipse cx="{MX}" cy="{MY}" rx="{rx}" ry="{ry}" fill="none" '
              f'stroke="#e8feff" stroke-width="2.5" opacity="0.8"/>')

def mouth_open1():
    return _open_mouth(18, 11)

def mouth_open2():
    return _open_mouth(22, 18)

def mouth_open_tongue():
    """Open mouth with a red tongue at the bottom."""
    base = _open_mouth(22, 17)
    tongue = (f'<path d="M{MX-11} {MY+5} Q{MX-13} {MY+22} {MX} {MY+23} '
              f'Q{MX+13} {MY+22} {MX+11} {MY+5} Z" fill="#ff4f5f"/>'
              + f'<path d="M{MX} {MY+9} L{MX} {MY+20}" stroke="#d22b3a" '
                f'stroke-width="2" stroke-linecap="round"/>')
    return base + tongue

def mouth_o_small():
    """Small vertical O for surprise."""
    return (_m_halo(MX, MY, 22)
            + f'<ellipse cx="{MX}" cy="{MY}" rx="11" ry="15" fill="#03121f"/>'
            + f'<ellipse cx="{MX}" cy="{MY}" rx="11" ry="15" fill="none" '
              f'stroke="url(#cyanFill)" stroke-width="6"/>'
            + f'<ellipse cx="{MX}" cy="{MY}" rx="11" ry="15" fill="none" '
              f'stroke="#e8feff" stroke-width="2.5" opacity="0.8"/>')

def mouth_o_sing():
    """Tall vertical O for singing (open mouth)."""
    return (_m_halo(MX, MY, 28)
            + f'<ellipse cx="{MX}" cy="{MY}" rx="13" ry="20" fill="#03121f"/>'
            + f'<ellipse cx="{MX}" cy="{MY}" rx="13" ry="20" fill="none" '
              f'stroke="url(#cyanFill)" stroke-width="6"/>'
            + f'<ellipse cx="{MX}" cy="{MY}" rx="13" ry="20" fill="none" '
              f'stroke="#e8feff" stroke-width="2.5" opacity="0.8"/>')

def mouth_teardrop():
    """Teardrop/ellipse open mouth for the confused (question) face."""
    return (_m_halo(MX, MY, 26)
            + f'<path d="M{MX} {MY-22} C{MX+18} {MY-6} {MX+16} {MY+20} {MX} {MY+22} '
              f'C{MX-16} {MY+20} {MX-18} {MY-6} {MX} {MY-22} Z" fill="#03121f"/>'
            + f'<path d="M{MX} {MY-22} C{MX+18} {MY-6} {MX+16} {MY+20} {MX} {MY+22} '
              f'C{MX-16} {MY+20} {MX-18} {MY-6} {MX} {MY-22} Z" fill="none" '
              f'stroke="url(#cyanFill)" stroke-width="6"/>')

MOUTH_FRAMES = {
    "smile_small": mouth_smile_small(),
    "frown": mouth_frown(),
    "open1": mouth_open1(),
    "open2": mouth_open2(),
    "open_tongue": mouth_open_tongue(),
    "o_small": mouth_o_small(),
    "o_sing": mouth_o_sing(),
    "teardrop": mouth_teardrop(),
}


def render_png(inner_svg, vb_w, vb_h, px_w, px_h, out_png):
    svg = (f'<svg viewBox="0 0 {vb_w} {vb_h}" '
           f'xmlns="http://www.w3.org/2000/svg">{DEFS}{inner_svg}</svg>')
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;background:transparent}}
svg{{width:{px_w}px;height:{px_h}px;display:block}}
</style></head><body>{svg}</body></html>"""
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        html_path = f.name
    try:
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--force-device-scale-factor=1",
             "--default-background-color=00000000", f"--window-size={px_w},{px_h}",
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
            color_bytes.append(rgb565 & 0xFF)
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


def shared_bbox(images, px_w, px_h):
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
    x0, y0 = max(0, x0 - 2), max(0, y0 - 2)
    x1, y1 = min(px_w, x1 + 2), min(px_h, y1 + 2)
    return (x0, y0, x1, y1)


def emit_group(frames, vb_w, vb_h, px_w, px_h, prefix, out_c, out_h,
               w_macro, h_macro, tmpdir):
    from PIL import Image
    images = {}
    for name, svg in frames.items():
        png = os.path.join(tmpdir, f"{prefix}_{name}.png")
        render_png(svg, vb_w, vb_h, px_w, px_h, png)
        images[name] = Image.open(png).convert("RGBA")

    bbox = shared_bbox(images, px_w, px_h)
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0

    for name in frames:
        preview = os.path.join(tmpdir, f"{prefix}_{name}_cropped.png")
        images[name].crop(bbox).save(preview)
        print(f"preview PNG: {preview}")

    parts = [
        f"/* AUTO-GENERATED by firmware/tools/gen_face_sprites.py — do not edit by hand.",
        f" * Robot-face {prefix} sprite frames (LVGL RGB565A8), crisp cyan on black. */",
        f'#include "{os.path.basename(out_h)}"',
        "",
    ]
    for name in frames:
        parts.append(to_c_array(f"{prefix}_{name}", images[name].crop(bbox)))
        parts.append("")
    open(out_c, "w").write("\n".join(parts))

    header = [
        "/* AUTO-GENERATED by firmware/tools/gen_face_sprites.py — do not edit by hand. */",
        "#pragma once",
        "",
        '#include "lvgl.h"',
        "",
        f"#define {w_macro} {w}",
        f"#define {h_macro} {h}",
        "",
    ]
    for name in frames:
        header.append(f"extern const lv_image_dsc_t {prefix}_{name};")
    header.append("")
    open(out_h, "w").write("\n".join(header))

    print(f"{prefix}: {len(frames)} frames, {w}x{h}px, "
          f"flash ≈ {len(frames) * w * h * 3} bytes")
    return w, h, len(frames)


def main():
    tmpdir = tempfile.mkdtemp(prefix="face_")
    print(f"tmp: {tmpdir}")

    ew, eh, en = emit_group(
        EYE_FRAMES, EYES_PX_W, EYES_PX_H, EYES_PX_W, EYES_PX_H, "face_eyes",
        os.path.join(MAIN, "face_eyes_sprites.c"),
        os.path.join(MAIN, "face_eyes_sprites.h"),
        "FACE_EYES_W", "FACE_EYES_H", tmpdir)

    mw, mh, mn = emit_group(
        MOUTH_FRAMES, MOUTH_PX_W, MOUTH_PX_H, MOUTH_PX_W, MOUTH_PX_H, "face_mouth",
        os.path.join(MAIN, "face_mouth_sprites.c"),
        os.path.join(MAIN, "face_mouth_sprites.h"),
        "FACE_MOUTH_W", "FACE_MOUTH_H", tmpdir)

    total = en * ew * eh * 3 + mn * mw * mh * 3
    print(f"\nTOTAL flash ≈ {total} bytes ({total/1024:.1f} KiB)")


if __name__ == "__main__":
    main()
