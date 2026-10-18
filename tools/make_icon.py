"""Generate the 糊涂账 app icon (PNG + .icns + favicon)."""
from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
S = 2048
M = 170
R = 390
CREAM = (245, 237, 224, 255)
INK_TOP = (37, 32, 27)
INK_BOTTOM = (18, 15, 13)
GREEN = (127, 169, 141)
AMBER = (208, 138, 68)
SONGTI = "/System/Library/Fonts/Supplemental/Songti.ttc"


def lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def mix(c1, c2, t: float):
    return tuple(lerp(c1[i], c2[i], t) for i in range(3)) + (255,)


def build() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # vertical ink gradient
    grad = Image.new("RGBA", (1, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.point((0, y), mix(INK_TOP, INK_BOTTOM, y / S))
    grad = grad.resize((S, S))

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([M, M, S - M, S - M], R, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)

    # soft warm glow behind the character
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for r in range(620, 0, 14):
        a = int(10 * (1 - r / 620) ** 1.6)
        gd.ellipse([S / 2 - r, 900 - r * 0.9, S / 2 + r, 900 + r * 0.9],
                   fill=(236, 210, 170, a))
    img.alpha_composite(glow)

    # the character
    font = ImageFont.truetype(SONGTI, 980, index=1)
    text = "账"
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((S - tw) / 2 - box[0], 800 - th / 2 - box[1]), text, font=font, fill=CREAM)

    # hand-drawn wavy underline: a messy ledger line that still ends neatly
    pts = []
    x0, x1, base = 545, 1490, 1550
    steps = 260
    for i in range(steps + 1):
        t = i / steps
        x = x0 + (x1 - x0) * t
        # amplitude starts small, gets messy, then settles
        amp = 62 * math.sin(math.pi * min(1.0, t * 1.1)) + 10 * (1 - t)
        y = base + math.sin(t * math.pi * 2 * 2.8 + 0.4) * amp - 14 * t
        pts.append((x, y))

    width = 38
    for i in range(len(pts) - 1):
        t = i / (len(pts) - 1)
        color = mix(GREEN, AMBER, t)
        d.line([pts[i], pts[i + 1]], fill=color, width=width)
        r = width / 2
        d.ellipse([pts[i][0] - r, pts[i][1] - r, pts[i][0] + r, pts[i][1] + r], fill=color)
    # end dot
    ex, ey = pts[-1]
    d.ellipse([ex - 26, ey - 26, ex + 26, ey + 26], fill=AMBER + (255,))

    return img.resize((1024, 1024), Image.LANCZOS)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    icon = build()
    icon.save(ASSETS / "icon-1024.png")

    # favicon
    icon.resize((128, 128), Image.LANCZOS).save(ROOT / "app" / "static" / "icon-128.png")

    iconset = ASSETS / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    for base in (16, 32, 128, 256, 512):
        icon.resize((base, base), Image.LANCZOS).save(iconset / f"icon_{base}x{base}.png")
        icon.resize((base * 2, base * 2), Image.LANCZOS).save(iconset / f"icon_{base}x{base}@2x.png")

    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "icon.icns")], check=True)
    print("wrote", ASSETS / "icon-1024.png", "and icon.icns")


if __name__ == "__main__":
    main()
