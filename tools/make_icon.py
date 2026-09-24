"""Draw Langmod Manager's icons, one set per theme.

The mark: two speech bubbles stacked, the way several language mods stack
on the game, with lines of text cut out of the front one. It sits on a
rounded tile in the theme's colours, sprinkled with that theme's scene -
stars, petals, snow, sparks, northern lights, gears, a ringed planet, a
battlefield or, for Your picture, a landscape - at the sizes where they still read. The
"-play" variant adds a play badge, for the shortcut that starts the game.

Each size is drawn at four times scale and reduced, so 16 pixels stays crisp instead of being one big bitmap smeared down.

    python tools/make_icon.py      (needs Pillow)
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "langmod" / "resources"
ICO_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
SS = 4

THEMES = {
    #          tile top    tile bottom  edge        accent      back bubble  cut         deco
    "standard": ("#23272f", "#16181d", "#3d424d", "#e5a52a", "#8a6a2c", "#1a1d23", None),
    "astral": ("#16225a", "#060a18", "#2c3a6e", "#8fbaff", "#4b64a8", "#0a1024", "stars"),
    "sakura": ("#ffd3e3", "#fff3f7", "#f0b7cb", "#d94f86", "#f09bbb", "#fff3f7", "petals"),
    "frost": ("#cfe2f6", "#f4f9ff", "#a9c6e4", "#2f7fd1", "#8fb8e4", "#f4f9ff", "snow"),
    "ember": ("#351510", "#0f0908", "#5a2a1c", "#ff8a3d", "#9a4a24", "#140b09", "sparks"),
    "aurora": ("#062129", "#02070c", "#1f5358", "#4df0b0", "#2b8a78", "#03090e", "aurora"),
    "factory": ("#2b333c", "#12161a", "#4c5763", "#f5c542", "#7c8995", "#15191d", "gears"),
    "space": ("#1b1045", "#04030d", "#4b3f8c", "#b18cff", "#5e45a8", "#070414", "space"),
    "warfare": ("#56606e", "#1a1e24", "#5a6470", "#a8c070", "#6f7a4a", "#15191e", "war"),
    "picture": ("#2c3240", "#0c0e12", "#4a5262", "#7fb2ff", "#4c6590", "#0e1116", "photo"),
}


def rgb(hex_colour: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha


def _tile(size: int, top: str, bottom: str, edge: str) -> Image.Image:
    """The rounded tile, filled top to bottom with the theme's gradient."""
    s = size * SS
    grad = Image.new("RGBA", (s, s))
    t, b = rgb(top), rgb(bottom)
    px = grad.load()
    for y in range(s):
        k = y / max(s - 1, 1)
        row = tuple(round(t[i] + (b[i] - t[i]) * k) for i in range(4))
        for x in range(s):
            px[x, y] = row
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=s * 0.22, fill=255)
    tile = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    tile.paste(grad, (0, 0), mask)
    ImageDraw.Draw(tile).rounded_rectangle([0, 0, s - 1, s - 1], radius=s * 0.22,
                                           outline=rgb(edge), width=max(1, round(s * 0.012)))
    return tile


def _petal(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, angle: float, fill) -> None:
    pts = []
    for i in range(24):
        a = math.tau * i / 24
        # a teardrop with a small notch at its wide end, like a cherry petal
        rad = r * (0.55 + 0.45 * math.cos(a / 2) ** 2)
        if abs(math.sin(a / 2)) < 0.12:
            rad *= 0.78
        x, y = rad * math.cos(a) * 0.62, rad * math.sin(a)
        pts.append((cx + x * math.cos(angle) - y * math.sin(angle), cy + x * math.sin(angle) + y * math.cos(angle)))
    d.polygon(pts, fill=fill)


def _gear_points(cx: float, cy: float, r: float, teeth: int) -> list[tuple[float, float]]:
    root, step = r * 0.80, math.tau / teeth
    pts = []
    for k in range(teeth):
        for frac, rad in ((-0.30, root), (-0.14, r), (0.14, r), (0.30, root), (0.5, root)):
            a = (k + frac) * step
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return pts


def _scene_layer(img: Image.Image, kind: str, s: int) -> None:
    """A decoration that reaches the tile's edges, kept inside its rounded corners."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if kind == "aurora":
        edge = [(x, s * (0.34 + 0.05 * math.sin(x / s * 7.0 + 0.6) + 0.02 * math.sin(x / s * 17)))
                for x in range(0, s + 1, max(1, s // 64))]
        d.polygon([(x, y - s * 0.26) for x, y in edge] + edge[::-1], fill=rgb("#2bd8a0", 90))
        d.polygon([(x, y - s * 0.08) for x, y in edge] + edge[::-1], fill=rgb("#8affcf", 175))
        for i in range(10):
            x = s * (0.05 + i * 0.1)
            d.line([(x, s * 0.04), (x, s * 0.36)], fill=rgb("#a98cff", 45), width=max(1, s // 50))
        layer = layer.filter(ImageFilter.GaussianBlur(s * 0.016))
        d = ImageDraw.Draw(layer)
        for u, v in ((0.12, 0.10), (0.86, 0.08), (0.62, 0.05), (0.90, 0.56)):
            r = s * 0.013
            d.ellipse([u * s - r, v * s - r, u * s + r, v * s + r], fill=(235, 250, 255, 235))
    elif kind == "space":
        for cx, cy, r, colour in ((0.2, 0.3, 0.3, "#7b3fe0"), (0.85, 0.75, 0.28, "#d8457f")):
            d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], fill=rgb(colour, 70))
        layer = layer.filter(ImageFilter.GaussianBlur(s * 0.06))
        d = ImageDraw.Draw(layer)
        for u, v in ((0.08, 0.30), (0.88, 0.10), (0.58, 0.07), (0.93, 0.52), (0.07, 0.62), (0.30, 0.93)):
            r = s * 0.011
            d.ellipse([u * s - r, v * s - r, u * s + r, v * s + r], fill=(240, 236, 255, 235))
        px, py, pr = 0.17 * s, 0.15 * s, 0.075 * s                       # a ringed planet
        d.ellipse([px - pr, py - pr, px + pr, py + pr], fill=rgb("#d69a6f", 255))
        d.ellipse([px - pr * 0.55, py - pr * 0.75, px + pr * 0.2, py - pr * 0.1], fill=rgb("#f0c99a", 170))
        d.ellipse([px - pr * 2.0, py - pr * 0.42, px + pr * 2.0, py + pr * 0.42], outline=rgb("#e6d3b3", 230),
                  width=max(1, round(s * 0.012)))
    elif kind == "photo":
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))                 # a low sun, warm in a cool sky
        g = ImageDraw.Draw(glow)
        g.ellipse([0.52 * s, -0.10 * s, 1.08 * s, 0.46 * s], fill=rgb("#ff9f5a", 130))
        layer.alpha_composite(glow.filter(ImageFilter.GaussianBlur(s * 0.08)))
        d = ImageDraw.Draw(layer)
        r = 0.07 * s
        d.ellipse([0.80 * s - r, 0.18 * s - r, 0.80 * s + r, 0.18 * s + r], fill=rgb("#ffd49a", 255))
        far = [(0, 0.80 * s), (0.18 * s, 0.70 * s), (0.34 * s, 0.77 * s), (0.55 * s, 0.64 * s), (0.74 * s, 0.74 * s),
               (s, 0.68 * s), (s, s), (0, s)]
        d.polygon(far, fill=rgb("#3d4a66", 255))
        near = [(0, 0.86 * s), (0.22 * s, 0.80 * s), (0.46 * s, 0.88 * s), (0.70 * s, 0.81 * s), (s, 0.87 * s),
                (s, s), (0, s)]
        d.polygon(near, fill=rgb("#1b2130", 255))
    elif kind == "war":
        smoke = Image.new("RGBA", img.size, (0, 0, 0, 0))                # a pall of smoke over a city at dusk
        g = ImageDraw.Draw(smoke)
        for cx, cy, r in ((0.30, 0.62, 0.07), (0.33, 0.48, 0.10), (0.38, 0.33, 0.14), (0.50, 0.16, 0.18),
                          (0.75, 0.08, 0.2), (0.18, 0.10, 0.16), (0.98, 0.14, 0.14)):
            g.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], fill=rgb("#121417", 235))
        layer.alpha_composite(smoke.filter(ImageFilter.GaussianBlur(s * 0.035)))
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([0.2 * s, 0.66 * s, 0.4 * s, 0.8 * s], fill=rgb("#ff8a3c", 150))
        layer.alpha_composite(glow.filter(ImageFilter.GaussianBlur(s * 0.03)))
        d = ImageDraw.Draw(layer)
        rng = random.Random(3)
        x = -0.02
        while x < 1.0:                                                   # blocks of flats, windows lit
            bw, bh = rng.uniform(0.08, 0.14), rng.uniform(0.10, 0.22)
            top = 0.86 - bh
            d.rectangle([x * s, top * s, (x + bw) * s, s], fill=rgb("#5d646e", 255))
            for fy in range(int(bh / 0.035)):
                for fx in range(int(bw / 0.035)):
                    if rng.random() < 0.3:
                        wx, wy = x + 0.012 + fx * 0.035, top + 0.014 + fy * 0.035
                        d.rectangle([wx * s, wy * s, (wx + 0.015) * s, (wy + 0.012) * s], fill=rgb("#ffcf8a", 255))
            x += bw + rng.uniform(0.0, 0.02)
        d.rectangle([0, 0.9 * s, s, s], fill=rgb("#3d3b2a", 255))
    else:
        for cx, cy, r, teeth in ((0.10, 0.11, 0.18, 10), (0.94, 0.92, 0.22, 12)):
            d.polygon(_gear_points(cx * s, cy * s, r * s, teeth), fill=rgb("#5b6773", 230))
            hub = r * s * 0.34
            d.ellipse([cx * s - hub, cy * s - hub, cx * s + hub, cy * s + hub], fill=rgb("#2a323a", 255))
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), img.getchannel("A")))
    img.alpha_composite(layer)


def _decorate(img: Image.Image, kind: str | None, accent: str, s: int) -> None:
    if not kind:
        return
    if kind in ("aurora", "gears", "space", "war", "photo"):
        _scene_layer(img, kind, s)
        return
    d = ImageDraw.Draw(img)
    rng = random.Random(7)
    spots = [(0.12, 0.14), (0.86, 0.12), (0.90, 0.40), (0.10, 0.88), (0.54, 0.10), (0.92, 0.88), (0.40, 0.92)]
    for i, (u, v) in enumerate(spots):
        x, y = u * s, v * s
        if kind == "stars":
            r = s * (0.012 + 0.012 * rng.random())
            d.ellipse([x - r, y - r, x + r, y + r], fill=(235, 242, 255, 230))
            if i % 3 == 0:
                arm = r * 3.2
                d.line([x - arm, y, x + arm, y], fill=(200, 220, 255, 150), width=max(1, round(r * 0.6)))
                d.line([x, y - arm, x, y + arm], fill=(200, 220, 255, 150), width=max(1, round(r * 0.6)))
        elif kind == "petals":
            _petal(d, x, y, s * 0.045, rng.uniform(0, math.tau), rgb("#f39ab9", 220))
        elif kind == "snow":
            r = s * (0.014 + 0.012 * rng.random())
            d.ellipse([x - r - 1, y - r - 1, x + r + 1, y + r + 1], fill=(143, 184, 228, 160))
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 255))
        elif kind == "sparks":
            r = s * (0.006 + 0.007 * rng.random())
            d.ellipse([x - r * 2.6, y - r * 3.4, x + r * 2.6, y + r * 3.4], fill=rgb(accent, 55))
            d.ellipse([x - r, y - r * 1.9, x + r, y + r * 1.9], fill=(255, 196, 96, 255))


def draw(size: int, theme: str, play: bool = False) -> Image.Image:
    top, bottom, edge, accent, back, cut, deco = THEMES[theme]
    s = size * SS
    img = _tile(size, top, bottom, edge)
    detailed = size >= 32
    if detailed:
        _decorate(img, deco, accent, s)
    d = ImageDraw.Draw(img)

    def box(x0, y0, x1, y1):
        return [x0 * s, y0 * s, x1 * s, y1 * s]

    # The bubble behind: another mod, further down the stack.
    d.rounded_rectangle(box(0.34, 0.20, 0.84, 0.54), radius=s * 0.075, fill=rgb(back))
    d.polygon([(0.70 * s, 0.53 * s), (0.80 * s, 0.53 * s), (0.80 * s, 0.63 * s)], fill=rgb(back))
    # The bubble in front, with a tail down to the left, and a ring of the tile around it
    # so the two read as separate sheets.
    ring = s * 0.028
    front = (0.14, 0.34, 0.72, 0.72)
    d.rounded_rectangle([front[0] * s - ring, front[1] * s - ring, front[2] * s + ring, front[3] * s + ring],
                        radius=s * 0.09 + ring, fill=rgb(cut))
    tail = [(0.22 * s, 0.70 * s), (0.20 * s, 0.86 * s), (0.40 * s, 0.70 * s)]
    d.polygon([(x, y + ring * (1 if i == 1 else 0)) for i, (x, y) in enumerate(tail)], fill=rgb(cut))
    d.rounded_rectangle(box(*front), radius=s * 0.09, fill=rgb(accent))
    d.polygon(tail, fill=rgb(accent))
    # Lines of text, cut out of it.
    line_h = 0.062 if detailed else 0.085
    if detailed:
        d.rounded_rectangle(box(0.24, 0.445, 0.62, 0.445 + line_h), radius=s * line_h / 2, fill=rgb(cut))
        d.rounded_rectangle(box(0.24, 0.575, 0.50, 0.575 + line_h), radius=s * line_h / 2, fill=rgb(cut))
    else:
        d.rounded_rectangle(box(0.25, 0.49, 0.61, 0.49 + line_h), radius=s * line_h / 2, fill=rgb(cut))

    if play:
        cx, cy, r = 0.74 * s, 0.74 * s, 0.20 * s
        d.ellipse([cx - r - ring, cy - r - ring, cx + r + ring, cy + r + ring], fill=rgb(cut))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rgb(accent))
        t = r * 0.52
        d.polygon([(cx - t * 0.7, cy - t), (cx - t * 0.7, cy + t), (cx + t * 1.05, cy)], fill=rgb(cut))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for theme in THEMES:
        for play in (False, True):
            images = [draw(n, theme, play) for n in ICO_SIZES]
            name = f"icon-{theme}{'-play' if play else ''}"
            images[-1].save(OUT / f"{name}.ico", format="ICO", sizes=[(n, n) for n in ICO_SIZES],
                            append_images=images[:-1])
            images[-1].save(OUT / f"{name}.png", format="PNG")
            written.append(name)
    # A sheet to look at the small sizes properly.
    sizes = (16, 24, 32, 48, 64, 128)
    pad = 14
    row_w = sum(n + pad for n in sizes) + pad + 128 + pad
    sheet = Image.new("RGBA", (row_w, (128 + pad) * len(THEMES) + pad), (238, 240, 244, 255))
    y = pad
    for theme in THEMES:
        x = pad
        for n in sizes:
            im = draw(n, theme)
            sheet.paste(im, (x, y + (128 - n) // 2), im)
            x += n + pad
        im = draw(128, theme, play=True)
        sheet.paste(im, (x, y), im)
        y += 128 + pad
    sheet.save(ROOT / "tools" / "icon_preview.png")
    print("wrote " + ", ".join(written))


if __name__ == "__main__":
    main()
