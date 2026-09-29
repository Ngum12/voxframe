"""Draw Voxframe's icon (D-158).

A rounded blue square; a white play triangle; below it, a caption bar with
one word lit in the captions' own highlight gold. Speech becoming a captioned
video, in one glyph, legible at 16 pixels.

Writes the SVG (for the web app and docs), a 512-pixel PNG, a Windows .ico
and a macOS .icns, with every size each system asks for. Drawn with Pillow at 1024 pixels and scaled
down, so no SVG renderer is needed.

    python scripts/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "src" / "voxframe" / "assets" / "icon"
FAVICON = REPO_ROOT / "web" / "public" / "favicon.svg"

BLUE_TOP = (43, 108, 222)
BLUE_BOTTOM = (22, 72, 176)
WHITE = (255, 255, 255)
GOLD = (255, 215, 0)
SOFT = (255, 255, 255, 110)

#: The design on a 256 unit square, shared by the SVG and the drawing.
CORNER = 56
TRIANGLE = [(98, 58), (98, 154), (182, 106)]
CORNER_ROUNDING = 12
BAR = (52, 176, 204, 208)  # x0, y0, x1, y1
BAR_RADIUS = 16
WORD = (116, 184, 170, 200)
WORD_RADIUS = 8


def _svg_path(points: list[tuple[float, float]]) -> str:
    head, *rest = points
    return f"M{head[0]:.1f},{head[1]:.1f} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in rest) + " Z"


def svg() -> str:
    x0, y0, x1, y1 = BAR
    w0, v0, w1, v1 = WORD
    triangle = _svg_path(_rounded([(float(x), float(y)) for x, y in TRIANGLE], CORNER_ROUNDING))
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"'
        ' role="img" aria-label="Voxframe">',
        "  <defs>",
        '    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">',
        f'      <stop offset="0" stop-color="rgb{BLUE_TOP}"/>',
        f'      <stop offset="1" stop-color="rgb{BLUE_BOTTOM}"/>',
        "    </linearGradient>",
        "  </defs>",
        f'  <rect width="256" height="256" rx="{CORNER}" fill="url(#bg)"/>',
        f'  <path d="{triangle}" fill="#fff"/>',
        f'  <rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}"'
        f' rx="{BAR_RADIUS}" fill="#fff" fill-opacity="0.43"/>',
        f'  <rect x="{w0}" y="{v0}" width="{w1 - w0}" height="{v1 - v0}"'
        f' rx="{WORD_RADIUS}" fill="#FFD700"/>',
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


def _rounded(points: list[tuple[float, float]], radius: float) -> list[tuple[float, float]]:
    """A polygon with a true circular arc of ``radius`` at every corner."""
    import math

    out: list[tuple[float, float]] = []
    count = len(points)
    for i, (px, py) in enumerate(points):
        ax, ay = points[i - 1]
        bx, by = points[(i + 1) % count]
        # Unit vectors from the corner along both edges.
        ux, uy = ax - px, ay - py
        vx, vy = bx - px, by - py
        lu, lv = math.hypot(ux, uy), math.hypot(vx, vy)
        ux, uy, vx, vy = ux / lu, uy / lu, vx / lv, vy / lv
        angle = math.acos(max(-1.0, min(1.0, ux * vx + uy * vy)))
        tangent = radius / math.tan(angle / 2)
        start = (px + ux * tangent, py + uy * tangent)
        end = (px + vx * tangent, py + vy * tangent)
        # The arc's centre lies on the bisector.
        bisector_x, bisector_y = ux + vx, uy + vy
        length = math.hypot(bisector_x, bisector_y)
        centre_distance = radius / math.sin(angle / 2)
        cx = px + bisector_x / length * centre_distance
        cy = py + bisector_y / length * centre_distance
        first = math.atan2(start[1] - cy, start[0] - cx)
        last = math.atan2(end[1] - cy, end[0] - cx)
        sweep = (last - first + math.pi) % (2 * math.pi) - math.pi
        for step in range(17):
            theta = first + sweep * step / 16
            out.append((cx + radius * math.cos(theta), cy + radius * math.sin(theta)))
    return out


def draw(size: int = 1024) -> Image.Image:
    scale = size / 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    gradient = Image.new("RGBA", (size, size))
    pixels = gradient.load()
    for y in range(size):
        t = y / (size - 1)
        colour = tuple(round(a + (b - a) * t) for a, b in zip(BLUE_TOP, BLUE_BOTTOM, strict=True))
        for x in range(size):
            pixels[x, y] = (*colour, 255)
    mask = Image.new("L", (size, size), 0)
    corner = round(CORNER * scale)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), corner, fill=255)
    image.paste(gradient, (0, 0), mask)

    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pen = ImageDraw.Draw(overlay)
    triangle = [(x * scale, y * scale) for x, y in TRIANGLE]
    pen.polygon(_rounded(triangle, CORNER_ROUNDING * scale), fill=WHITE)
    pen.rounded_rectangle([v * scale for v in BAR], round(BAR_RADIUS * scale), fill=SOFT)
    pen.rounded_rectangle([v * scale for v in WORD], round(WORD_RADIUS * scale), fill=GOLD)
    return Image.alpha_composite(image, overlay)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    FAVICON.parent.mkdir(parents=True, exist_ok=True)
    (OUT / "voxframe.svg").write_text(svg(), encoding="utf-8", newline="\n")
    FAVICON.write_text(svg(), encoding="utf-8", newline="\n")

    big = draw(1024)
    big.resize((512, 512), Image.LANCZOS).save(OUT / "voxframe-512.png", optimize=True)
    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    big.resize((256, 256), Image.LANCZOS).save(OUT / "voxframe.ico", sizes=sizes)
    # The Mac app's icon: Pillow writes every size macOS asks for from one image.
    big.save(OUT / "voxframe.icns")
    for path in sorted(OUT.iterdir()):
        print(f"  {path.relative_to(REPO_ROOT)}  {path.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
