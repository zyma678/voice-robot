#!/usr/bin/env python3
"""Generate LVGL sprite frames for the on-device "butterfly" main screen — a
neon glowing butterfly whose WINGS flap (3 frames) and continuously shift hue at
runtime, with a small BODY sprite that carries human-like EXPRESSIONS tied to the
voice-conversation state.

Design language (mirrors the reference art): a symmetric neon butterfly drawn as
crisp WHITE outlines + faint white interior fills, with little stars / a crescent
moon sprinkled inside the wings. Everything is drawn in WHITE on transparent so
that display_lcd.c can recolor the whole butterfly to a vivid, cycling HSV color
at runtime (LVGL image_recolor + LV_OPA_COVER keeps the A8 alpha, replaces RGB).
DO NOT bake colors in here — color is applied on-device.

Sprites are split so each part animates / recolors independently:
  * WINGS  (3 frames: open / mid / close)  — flap + hue cycle
  * BODY   (6 frames of expression)        — idle / focus / curious / think /
                                             talk / sad, switched by voice state

Pipeline (same as gen_face_sprites.py — read that first):
  1. Embedded WHITE-only SVGs below (no blur filters; glow faked via layered
     decreasing-opacity strokes, like gen_face_sprites' _crescent).
  2. Render each frame to a transparent PNG with headless Chrome.
  3. Crop each GROUP to its shared bbox so frames overlay 1:1, then emit
     firmware/main/butterfly_sprites.c/.h as LVGL RGB565A8 images.

RGB565A8 layout (verified against LVGL 9.5 src/draw/sw/lv_draw_sw_img.c):
  [w*h*2 bytes RGB565, little-endian, stride=w*2] then [w*h bytes A8, stride=w].

Regenerate:  .venv/bin/python firmware/tools/gen_butterfly_sprites.py
Requires: Google Chrome (macOS app path below) + Pillow (in .venv).
"""

import math
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN = os.path.join(REPO, "firmware", "main")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Wings rendered on a wide canvas; the body on a narrow tall canvas. Final assets
# are cropped to each group's union bbox.
WING_PX_W, WING_PX_H = 200, 170
BODY_PX_W, BODY_PX_H = 110, 190
ALPHA_THRESHOLD = 12  # alpha above this counts as "content" for the crop bbox


def _glow_stroke(d, widths_opas):
    """A path stroked several times with decreasing width handled by the caller;
    here we just emit layered white strokes (wide+faint .. thin+bright) to fake a
    neon glow without SVG blur (Chrome headless renders filters poorly)."""
    out = ""
    for w, o in widths_opas:
        out += (f'<path d="{d}" fill="none" stroke="#ffffff" '
                f'stroke-width="{w}" stroke-linecap="round" '
                f'stroke-linejoin="round" opacity="{o}"/>')
    return out


def _star(cx, cy, r, op=0.95):
    """A small 4-point sparkle (two crossed diamonds) in white."""
    return (f'<path d="M{cx} {cy-r} L{cx+r*0.32} {cy-r*0.32} L{cx+r} {cy} '
            f'L{cx+r*0.32} {cy+r*0.32} L{cx} {cy+r} L{cx-r*0.32} {cy+r*0.32} '
            f'L{cx-r} {cy} L{cx-r*0.32} {cy-r*0.32} Z" '
            f'fill="#ffffff" opacity="{op}"/>')


def _dot(cx, cy, r, op=0.85):
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#ffffff" opacity="{op}"/>'


def _crescent(cx, cy, r, op=0.9):
    """Small crescent moon (two offset arcs) in white."""
    return (f'<path d="M{cx-r*0.2} {cy-r} A{r} {r} 0 1 0 {cx-r*0.2} {cy+r} '
            f'A{r*0.78} {r*0.78} 0 1 1 {cx-r*0.2} {cy-r} Z" '
            f'fill="#ffffff" opacity="{op}"/>')


def _spiral(cx, cy, turns=1.3, r_end=12, op=0.6):
    """Decorative spiral curve in white."""
    steps = int(turns * 18)
    pts = []
    for i in range(steps + 1):
        t = i / steps * turns * 2 * math.pi
        r = 2 + (r_end - 2) * (i / steps)
        pts.append(f"{cx + r * math.cos(t):.1f},{cy + r * math.sin(t):.1f}")
    return _glow_stroke("M" + " L".join(pts), [(3, 0.18), (1.4, op)])


# --- Wing geometry -------------------------------------------------------
# Symmetric butterfly centred on the WING canvas. The body axis sits at x=WCX.
WCX, WCY = WING_PX_W / 2, WING_PX_H / 2

def _wing_pair(spread):
    """Draw a symmetric pair of wings. `spread` in [0..1] scales the horizontal
    extent so the butterfly appears to flap (1=fully open, ~0.55=closed).
    Upper wings are big rounded lobes, lower wings smaller. White neon outline +
    faint interior fill + sparkle/moon decorations."""
    # Horizontal reach of each wing from the body axis.
    up_dx = 88 * spread     # upper wing outer reach
    lo_dx = 70 * spread     # lower wing outer reach
    out = ""
    for side in (-1, 1):
        s = side
        ux = WCX + s * up_dx
        lx = WCX + s * lo_dx
        # Upper wing: body-top -> sweeps out and up -> back to body-mid.
        upper = (f"M{WCX} {WCY-30} "
                 f"C{WCX + s*30} {WCY-78} {ux} {WCY-70} {ux} {WCY-26} "
                 f"C{ux} {WCY+4} {WCX + s*28} {WCY+8} {WCX} {WCY-2} Z")
        # Lower wing: body-mid -> sweeps out and down -> back to body-bottom.
        lower = (f"M{WCX} {WCY+2} "
                 f"C{WCX + s*26} {WCY+12} {lx} {WCY+24} {lx} {WCY+56} "
                 f"C{lx} {WCY+84} {WCX + s*22} {WCY+82} {WCX} {WCY+34} Z")
        # Faint interior fill (alpha carries the glow body).
        out += f'<path d="{upper}" fill="#ffffff" fill-opacity="0.16"/>'
        out += f'<path d="{lower}" fill="#ffffff" fill-opacity="0.13"/>'
        # Layered neon outline: 4-layer glow for refined edges.
        out += _glow_stroke(upper, [(10, 0.12), (6, 0.35), (3, 0.65), (1.8, 0.98)])
        out += _glow_stroke(lower, [(9, 0.10), (5.5, 0.32), (2.8, 0.6), (1.6, 0.95)])
        # Inner accent curves — two concentric swooping lines per wing.
        inner_u = (f"M{WCX + s*10} {WCY-22} "
                   f"Q{WCX + s*(up_dx*0.62)} {WCY-44} "
                   f"{WCX + s*(up_dx*0.74)} {WCY-18}")
        inner_u2 = (f"M{WCX + s*6} {WCY-16} "
                    f"Q{WCX + s*(up_dx*0.38)} {WCY-36} "
                    f"{WCX + s*(up_dx*0.52)} {WCY-12}")
        inner_l = (f"M{WCX + s*8} {WCY+14} "
                   f"Q{WCX + s*(lo_dx*0.66)} {WCY+34} "
                   f"{WCX + s*(lo_dx*0.7)} {WCY+58}")
        inner_l2 = (f"M{WCX + s*6} {WCY+18} "
                    f"Q{WCX + s*(lo_dx*0.42)} {WCY+38} "
                    f"{WCX + s*(lo_dx*0.48)} {WCY+54}")
        out += _glow_stroke(inner_u, [(3, 0.2), (1.5, 0.65)])
        out += _glow_stroke(inner_u2, [(1.2, 0.4)])
        out += _glow_stroke(inner_l, [(3, 0.18), (1.4, 0.6)])
        out += _glow_stroke(inner_l2, [(1.2, 0.38)])
        # Decorations scale with the flap.
        if spread > 0.6:
            out += _star(WCX + s*up_dx*0.55, WCY - 44, 5.5)
            out += _star(WCX + s*up_dx*0.32, WCY - 34, 4.0, 0.8)
            out += _dot(WCX + s*up_dx*0.72, WCY - 30, 2.2)
            out += _dot(WCX + s*up_dx*0.44, WCY - 20, 1.6, 0.6)
            out += _dot(WCX + s*up_dx*0.82, WCY - 14, 1.4, 0.5)
            out += _star(WCX + s*lo_dx*0.4, WCY + 42, 5.0, 0.85)
            out += _dot(WCX + s*lo_dx*0.6, WCY + 62, 1.8, 0.6)
            out += _dot(WCX + s*lo_dx*0.3, WCY + 54, 1.5, 0.55)
            out += _spiral(WCX + s*lo_dx*0.52, WCY + 48,
                           turns=1.2, r_end=10, op=0.5)
    # Crescents and featured stars inside the wings.
    if spread > 0.6:
        out += _crescent(WCX - up_dx*0.35, WCY - 30, 9)
        out += _crescent(WCX + lo_dx*0.35, WCY + 50, 7, 0.7)
        out += _star(WCX + up_dx*0.3, WCY - 26, 6.5)
        out += _star(WCX - lo_dx*0.28, WCY + 44, 5.0, 0.75)
    return out


WING_FRAMES = {
    "open":     _wing_pair(1.0),
    "open_mid": _wing_pair(0.88),
    "mid":      _wing_pair(0.72),
    "mid_close":_wing_pair(0.50),
    "close":    _wing_pair(0.32),
}

# --- Body + expression geometry -----------------------------------------
# A glowing butterfly body centred on the BODY canvas: two antennae, a clear
# rounded HEAD that carries the human-like expression (large eyes + mouth), and a
# slim trunk below. White only (recolored on device).
BX, BY = BODY_PX_W / 2, BODY_PX_H / 2
HEAD_CY = BY - 26          # head centre
HEAD_R = 18               # head radius
EYE_Y = HEAD_CY - 2       # eye row inside the head
EYE_DX = 7                # eye offset from axis

def _body_base():
    """Antennae + head disc + segmented trunk (shared by every expression)."""
    out = ""
    # Antennae: two curved feelers with club tips, rising from the head top.
    for s in (-1, 1):
        a = (f"M{BX + s*5} {HEAD_CY-HEAD_R+3} "
             f"Q{BX + s*20} {HEAD_CY-HEAD_R-22} {BX + s*26} {HEAD_CY-HEAD_R-46}")
        out += _glow_stroke(a, [(3.0, 0.3), (1.6, 0.95)])
        out += _dot(BX + s*26, HEAD_CY-HEAD_R-48, 3.6)
    # Trunk below the head: slim rounded capsule with faint segment ticks.
    trunk = (f"M{BX} {HEAD_CY+HEAD_R-4} C{BX-7} {HEAD_CY+HEAD_R} "
             f"{BX-7} {BY+52} {BX} {BY+58} "
             f"C{BX+7} {BY+52} {BX+7} {HEAD_CY+HEAD_R} {BX} {HEAD_CY+HEAD_R-4} Z")
    out += f'<path d="{trunk}" fill="#ffffff" fill-opacity="0.2"/>'
    out += _glow_stroke(trunk, [(5, 0.16), (3, 0.5), (1.6, 0.95)])
    for i in range(3):
        ty = BY + 6 + i * 14
        out += (f'<path d="M{BX-4} {ty} Q{BX} {ty+3} {BX+4} {ty}" '
                f'fill="none" stroke="#ffffff" stroke-width="1.3" opacity="0.5"/>')
    # Head disc: faint fill + crisp neon ring.
    out += (f'<circle cx="{BX}" cy="{HEAD_CY}" r="{HEAD_R}" '
            f'fill="#ffffff" fill-opacity="0.14"/>')
    out += _glow_stroke(
        f"M{BX-HEAD_R} {HEAD_CY} a{HEAD_R} {HEAD_R} 0 1 0 {2*HEAD_R} 0 "
        f"a{HEAD_R} {HEAD_R} 0 1 0 {-2*HEAD_R} 0 Z",
        [(5, 0.14), (2.8, 0.5), (1.6, 0.95)])
    return out


def _eyes(kind):
    """Large expressive eyes (+ mouth) inside the head. `kind` selects emotion."""
    ey = EYE_Y
    lx, rx = BX - EYE_DX, BX + EYE_DX
    out = ""
    def disc(cx, r):
        return (f'<circle cx="{cx}" cy="{ey}" r="{r}" fill="#ffffff" '
                f'fill-opacity="0.96"/>')
    def pupil(cx, dy=0, pr=2.2):
        return f'<circle cx="{cx}" cy="{ey+dy}" r="{pr}" fill="#0a0a14"/>'
    def arc(d, w=2.8):
        return (f'<path d="{d}" fill="none" stroke="#ffffff" '
                f'stroke-width="{w}" stroke-linecap="round"/>')
    def mouth(d, w=2.2):
        return (f'<path d="{d}" fill="none" stroke="#ffffff" '
                f'stroke-width="{w}" stroke-linecap="round"/>')
    my = HEAD_CY + 9   # mouth row
    if kind == "idle":          # calm, open round eyes + soft smile
        out += disc(lx, 4.5) + disc(rx, 4.5) + pupil(lx, 0.5) + pupil(rx, 0.5)
        out += mouth(f"M{BX-5} {my} Q{BX} {my+3} {BX+5} {my}")
    elif kind == "focus":       # narrowed attentive eyes (listening)
        for cx in (lx, rx):
            out += (f'<ellipse cx="{cx}" cy="{ey}" rx="5" ry="3.2" '
                    f'fill="#ffffff" fill-opacity="0.96"/>')
            out += pupil(cx, 0, 2.0)
        out += mouth(f"M{BX-4} {my} L{BX+4} {my}")
    elif kind == "curious":     # wide eyes, pupils up + small o (recognizing)
        out += disc(lx, 5.5) + disc(rx, 5.5) + pupil(lx, -1.6) + pupil(rx, -1.6)
        out += (f'<circle cx="{BX}" cy="{my}" r="2.4" fill="none" '
                f'stroke="#ffffff" stroke-width="1.8"/>')
    elif kind == "think":       # raised brows + side-glancing pupils
        out += disc(lx, 4.5) + disc(rx, 4.5) + pupil(lx, -1.5, 1.8) + pupil(rx, 0)
        out += arc(f"M{lx-6} {ey-8} Q{lx} {ey-12} {lx+6} {ey-9}", 2.4)
        out += arc(f"M{rx-6} {ey-10} Q{rx} {ey-13} {rx+6} {ey-7}", 2.4)
        out += mouth(f"M{BX-4} {my+1} Q{BX+1} {my-2} {BX+5} {my+1}")
    elif kind == "talk":        # happy crescents + open smile (speaking)
        out += arc(f"M{lx-6} {ey+1} Q{lx} {ey-7} {lx+6} {ey+1}", 3.0)
        out += arc(f"M{rx-6} {ey+1} Q{rx} {ey-7} {rx+6} {ey+1}", 3.0)
        out += (f'<path d="M{BX-6} {my-1} Q{BX} {my+7} {BX+6} {my-1} Z" '
                f'fill="#ffffff" fill-opacity="0.9"/>')
    elif kind == "sad":         # downcast eyes + teardrop + frown (error)
        out += disc(lx, 4.5) + disc(rx, 4.5) + pupil(lx, 1.6) + pupil(rx, 1.6)
        out += arc(f"M{lx-6} {ey-8} Q{lx} {ey-5} {lx+6} {ey-9}", 2.4)
        out += arc(f"M{rx-6} {ey-9} Q{rx} {ey-5} {rx+6} {ey-8}", 2.4)
        out += mouth(f"M{BX-5} {my+2} Q{BX} {my-2} {BX+5} {my+2}")
        out += (f'<path d="M{rx+2} {ey+6} q-3 6 0 8 q3 -2 0 -8 Z" '
                f'fill="#ffffff" fill-opacity="0.9"/>')
    return out


BODY_FRAMES = {"base": _body_base()}

FACE_FRAMES = {
    "idle":    _eyes("idle"),
    "focus":   _eyes("focus"),
    "curious": _eyes("curious"),
    "think":   _eyes("think"),
    "talk":    _eyes("talk"),
    "sad":     _eyes("sad"),
}


def render_png(inner_svg, vb_w, vb_h, px_w, px_h, out_png):
    svg = (f'<svg viewBox="0 0 {vb_w} {vb_h}" '
           f'xmlns="http://www.w3.org/2000/svg">{inner_svg}</svg>')
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


def emit_group(frames, vb_w, vb_h, px_w, px_h, prefix, parts_accum, header_accum,
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

    for name in frames:
        parts_accum.append(to_c_array(f"{prefix}_{name}", images[name].crop(bbox)))
        parts_accum.append("")

    header_accum.append(f"#define {w_macro} {w}")
    header_accum.append(f"#define {h_macro} {h}")
    header_accum.append("")
    for name in frames:
        header_accum.append(f"extern const lv_image_dsc_t {prefix}_{name};")
    header_accum.append("")

    print(f"{prefix}: {len(frames)} frames, {w}x{h}px, "
          f"flash ≈ {len(frames) * w * h * 3} bytes")
    return w, h, len(frames), bbox


def main():
    tmpdir = tempfile.mkdtemp(prefix="butterfly_")
    print(f"tmp: {tmpdir}")

    out_c = os.path.join(MAIN, "butterfly_sprites.c")
    out_h = os.path.join(MAIN, "butterfly_sprites.h")

    parts = [
        "/* AUTO-GENERATED by firmware/tools/gen_butterfly_sprites.py — do not edit by hand.",
        " * Neon butterfly sprite frames (LVGL RGB565A8). Drawn in WHITE so the device",
        " * recolors the whole butterfly to a cycling vivid hue at runtime. */",
        '#include "butterfly_sprites.h"',
        "",
    ]
    header = [
        "/* AUTO-GENERATED by firmware/tools/gen_butterfly_sprites.py — do not edit by hand. */",
        "#pragma once",
        "",
        '#include "lvgl.h"',
        "",
    ]

    ww, wh, wn, _ = emit_group(
        WING_FRAMES, WING_PX_W, WING_PX_H, WING_PX_W, WING_PX_H, "butterfly_wing",
        parts, header, "BUTTERFLY_WING_W", "BUTTERFLY_WING_H", tmpdir)

    bw, bh, bn, body_bbox = emit_group(
        BODY_FRAMES, BODY_PX_W, BODY_PX_H, BODY_PX_W, BODY_PX_H, "butterfly_body",
        parts, header, "BUTTERFLY_BODY_W", "BUTTERFLY_BODY_H", tmpdir)

    fw, fh, fn, face_bbox = emit_group(
        FACE_FRAMES, BODY_PX_W, BODY_PX_H, BODY_PX_W, BODY_PX_H, "butterfly_face",
        parts, header, "BUTTERFLY_FACE_W", "BUTTERFLY_FACE_H", tmpdir)

    face_dy = face_bbox[1] - body_bbox[1]
    header.append(f"#define BUTTERFLY_FACE_BODY_DY {face_dy}")
    header.append("")

    open(out_c, "w").write("\n".join(parts))
    open(out_h, "w").write("\n".join(header))

    total = wn * ww * wh * 3 + bn * bw * bh * 3 + fn * fw * fh * 3
    print(f"\nTOTAL flash ≈ {total} bytes ({total/1024:.1f} KiB)")
    print(f"wrote {out_c}")
    print(f"wrote {out_h}")


if __name__ == "__main__":
    main()
