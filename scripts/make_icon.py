"""Draw the integration icon (brand/icon.png 256 px, icon@2x.png 512 px)."""
import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 2048  # draw large, scale down for smooth edges
OUT = Path(__file__).resolve().parent.parent / "custom_components" / "solxpect_sites" / "brand"

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# sun with rays, upper right
cx, cy, r = 1430, 600, 330
for k in range(12):
    a = math.radians(k * 30)
    d.line([(cx + math.cos(a) * (r + 90), cy + math.sin(a) * (r + 90)),
            (cx + math.cos(a) * (r + 260), cy + math.sin(a) * (r + 260))],
           fill=(255, 179, 0, 255), width=110)
d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 193, 7, 255))

# tilted panel (parallelogram) with cell grid, lower left
tl, tr, br, bl = (420, 1020), (1340, 1020), (1660, 1800), (100, 1800)
d.polygon([tl, tr, br, bl], fill=(21, 101, 192, 255))


def lerp(p, q, t):
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


for i in range(1, 4):  # columns
    t = i / 4
    d.line([lerp(tl, tr, t), lerp(bl, br, t)], fill=(187, 222, 251, 255), width=34)
for j in range(1, 3):  # rows
    t = j / 3
    d.line([lerp(tl, bl, t), lerp(tr, br, t)], fill=(187, 222, 251, 255), width=34)
d.line([tl, tr, br, bl, tl], fill=(13, 71, 161, 255), width=60, joint="curve")

# stand
d.rectangle([840, 1800, 920, 1960], fill=(96, 125, 139, 255))
d.rectangle([600, 1940, 1160, 2010], fill=(96, 125, 139, 255))

img = img.crop(img.getbbox())
side = max(img.size)
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
OUT.mkdir(parents=True, exist_ok=True)
square.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png", optimize=True)
square.resize((512, 512), Image.LANCZOS).save(OUT / "icon@2x.png", optimize=True)
print("written", OUT)
