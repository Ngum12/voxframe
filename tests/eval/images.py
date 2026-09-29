"""Generate the evaluation image pool.

Images are drawn rather than downloaded so the eval runs offline, is
deterministic, and raises no licensing questions. They are simple by
construction, which means absolute retrieval scores here will exceed what a
real photographic library achieves. That is acceptable: the eval exists to
*compare* encoders and catch regressions, not to predict field accuracy.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from tests.eval.dataset import IMAGE_POOL, ImageSpec

__all__ = ["IMAGE_SIZE", "generate_image", "generate_pool"]

#: CLIP resizes to 224x224, so anything larger is discarded. 512 keeps the
#: shapes clean through that downscale without wasting time.
IMAGE_SIZE = 512


def _draw_shape(
    draw: ImageDraw.ImageDraw,
    kind: str,
    colour: str,
    geometry: tuple[float, ...],
    size: int,
) -> None:
    """Draw one shape using coordinates relative to the image size."""
    if kind == "circle":
        cx, cy, radius = geometry
        x, y, r = cx * size, cy * size, radius * size
        draw.ellipse([x - r, y - r, x + r, y + r], fill=colour)

    elif kind == "rect":
        x, y, width, height = geometry
        draw.rectangle(
            [x * size, y * size, (x + width) * size, (y + height) * size], fill=colour
        )

    elif kind == "triangle":
        # Base centre, base y, half-width, height. Points upward, which suits
        # both mountains and trees.
        cx, base_y, half_width, height = geometry
        draw.polygon(
            [
                ((cx - half_width) * size, base_y * size),
                ((cx + half_width) * size, base_y * size),
                (cx * size, (base_y - height) * size),
            ],
            fill=colour,
        )

    elif kind == "wave":
        # A band with a sinusoidal top edge, for water and dunes.
        x0, y, width, amplitude = geometry
        points = []
        steps = 64
        for step in range(steps + 1):
            fraction = step / steps
            px = (x0 + fraction * width) * size
            # Two cycles across the band reads as waves rather than a curve.
            import math

            py = (y + amplitude * math.sin(fraction * math.pi * 4)) * size
            points.append((px, py))
        points.append((size, size))
        points.append((0, size))
        draw.polygon(points, fill=colour)


def generate_image(spec: ImageSpec, destination: Path, size: int = IMAGE_SIZE) -> Path:
    """Render one spec to a PNG.

    A slight blur is applied at the end: hard vector edges are unlike anything
    in CLIP's training data, and softening them makes the images behave a
    little more like photographs.
    """
    image = Image.new("RGB", (size, size), spec.background)
    draw = ImageDraw.Draw(image)

    for kind, colour, geometry in spec.shapes:
        _draw_shape(draw, kind, colour, geometry, size)

    image = image.filter(ImageFilter.GaussianBlur(radius=1.2))

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "PNG")
    return destination


def generate_pool(directory: Path, size: int = IMAGE_SIZE) -> dict[str, Path]:
    """Generate every pool image into ``directory``.

    Returns:
        Mapping of image key to path.
    """
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    for spec in IMAGE_POOL:
        path = directory / f"{spec.key}.png"
        if not path.exists():
            generate_image(spec, path, size)
        paths[spec.key] = path

    return paths
