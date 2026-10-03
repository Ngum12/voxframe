"""Split screen and picture-in-picture, composed frame-exact (D-197).

Each part of the frame is rendered on its own, at its own size, by the same
code that renders a whole-frame scene: the picture with its camera movement
or its clip, the speaker cut to the frames that were spoken and framed on
their face for the part's shape. They are then put together in one FFmpeg
pass that keeps every frame: stacked for a split, overlaid through an
antialiased mask for an inset. A split is stacked top and bottom in a
vertical or square frame and side by side in a landscape one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from voxframe.plan.scene_layout import InsetShape, LayoutKind, SceneLayout
from voxframe.render.encode.probe import FFmpegCapabilities
from voxframe.render.ffpath import run_ffmpeg

__all__ = ["Pane", "compose_inset", "compose_split", "inset_pane", "split_panes"]

#: The divider's thickness, as a fraction of the frame's shorter side.
DIVIDER = 0.006
#: The border round an inset, as a fraction of its width.
BORDER = 0.035
#: The inset's soft shadow: how far it spreads, as a fraction of its width.
SHADOW = 0.06
#: Masks are drawn this many times larger and reduced, for smooth edges.
SUPERSAMPLE = 4


def _even(value: float) -> int:
    """A size or place on the 4:2:0 grid: even, and at least 2."""
    return max(2, round(value / 2) * 2)


@dataclass(frozen=True)
class Pane:
    """A part of the frame, in pixels."""

    x: int
    y: int
    width: int
    height: int


def stacked(width: int, height: int) -> bool:
    """Whether a split is top and bottom (vertical or square) rather than side by side."""
    return height >= width


def split_panes(width: int, height: int, layout: SceneLayout) -> tuple[Pane, Pane]:
    """The picture's part and the speaker's part of a split frame."""
    if stacked(width, height):
        first = _even(height * (layout.split if not layout.speaker_first else 1 - layout.split))
        top, bottom = Pane(0, 0, width, first), Pane(0, first, width, height - first)
        return (bottom, top) if layout.speaker_first else (top, bottom)
    first = _even(width * (layout.split if not layout.speaker_first else 1 - layout.split))
    left, right = Pane(0, 0, first, height), Pane(first, 0, width - first, height)
    return (right, left) if layout.speaker_first else (left, right)


def divider_position(width: int, height: int, layout: SceneLayout) -> float:
    """Where the split falls, as a fraction of the height (vertical frames)
    or the width (landscape): where captions sit by default (D-197)."""
    picture, speaker = split_panes(width, height, layout)
    first = min((picture, speaker), key=lambda pane: (pane.y, pane.x))
    return (first.height / height) if stacked(width, height) else (first.width / width)


def inset_pane(width: int, height: int, layout: SceneLayout) -> Pane:
    """Where the inset sits: its size from the layout, kept inside the frame."""
    inset_width = _even(width * layout.inset_size)
    if layout.inset_shape is InsetShape.CIRCLE:
        inset_height = inset_width
    elif stacked(width, height):
        inset_height = _even(inset_width * 4 / 3)
    else:
        inset_height = _even(inset_width * 9 / 16)
    inset_height = min(inset_height, _even(height * 0.9))
    margin = _even(min(width, height) * 0.03)
    x = _even(width * layout.inset_x - inset_width / 2)
    y = _even(height * layout.inset_y - inset_height / 2)
    x = min(max(x, margin), width - inset_width - margin)
    y = min(max(y, margin), height - inset_height - margin)
    return Pane(x, y, inset_width, inset_height)


def _shape(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], shape: InsetShape, fill: int
) -> None:
    if shape is InsetShape.CIRCLE:
        draw.ellipse(box, fill=fill)
    else:
        width = box[2] - box[0]
        draw.rounded_rectangle(box, radius=int(width * 0.12), fill=fill)


def inset_images(pane: Pane, shape: InsetShape, folder: Path) -> tuple[Path, Path, Path, int]:
    """The inset's mask, its shadow and its border, as images, and how far
    the shadow and border reach beyond the inset on each side."""
    folder.mkdir(parents=True, exist_ok=True)
    s = SUPERSAMPLE
    w, h = pane.width, pane.height

    mask = Image.new("L", (w * s, h * s), 0)
    _shape(ImageDraw.Draw(mask), (0, 0, w * s - 1, h * s - 1), shape, 255)
    mask_path = folder / f"mask-{shape.value}-{w}x{h}.png"
    mask.resize((w, h), Image.Resampling.LANCZOS).save(mask_path)

    reach = _even(w * SHADOW)
    border = max(2, round(w * BORDER))
    full = (w + 2 * reach, h + 2 * reach)

    shadow = Image.new("L", (full[0] * s, full[1] * s), 0)
    offset = reach * s
    _shape(
        ImageDraw.Draw(shadow),
        (offset, offset + border * s, offset + w * s, offset + h * s + border * s),
        shape, 150,
    )
    shadow = shadow.resize(full, Image.Resampling.LANCZOS).filter(
        ImageFilter.GaussianBlur(reach / 2)
    )
    shadow_rgba = Image.new("RGBA", full, (0, 0, 0, 0))
    shadow_rgba.putalpha(shadow)
    shadow_path = folder / f"shadow-{shape.value}-{w}x{h}.png"
    shadow_rgba.save(shadow_path)

    ring = Image.new("L", (full[0] * s, full[1] * s), 0)
    draw = ImageDraw.Draw(ring)
    _shape(draw, (offset - border * s, offset - border * s, offset + w * s + border * s - 1,
                  offset + h * s + border * s - 1), shape, 255)
    _shape(draw, (offset, offset, offset + w * s - 1, offset + h * s - 1), shape, 0)
    ring_rgba = Image.new("RGBA", full, (255, 255, 255, 0))
    ring_rgba.putalpha(ring.resize(full, Image.Resampling.LANCZOS))
    ring_path = folder / f"ring-{shape.value}-{w}x{h}.png"
    ring_rgba.save(ring_path)
    return mask_path, shadow_path, ring_path, reach


def compose_split(
    caps: FFmpegCapabilities,
    picture: Path,
    speaker: Path,
    output: Path,
    *,
    width: int,
    height: int,
    layout: SceneLayout,
    frames: int,
    intermediate_args: list[str],
) -> None:
    """Put the two parts of a split frame together, every frame kept."""
    assert layout.kind is LayoutKind.SPLIT
    first, second = (speaker, picture) if layout.speaker_first else (picture, speaker)
    stack = "vstack" if stacked(width, height) else "hstack"
    graph = f"[0:v][1:v]{stack}=inputs=2"
    if layout.divider:
        thickness = _even(min(width, height) * DIVIDER)
        along = height if stacked(width, height) else width
        at = round(divider_position(width, height, layout) * along) - thickness // 2
        if stacked(width, height):
            box = f"x=0:y={at}:w={width}:h={thickness}"
        else:
            box = f"x={at}:y=0:w={thickness}:h={height}"
        graph += f",drawbox={box}:color=black@0.85:t=fill"
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-i", str(first.resolve()),
            "-i", str(second.resolve()),
            "-filter_complex", f"{graph}[v]",
            "-map", "[v]",
            "-frames:v", str(frames),
            *intermediate_args,
            "-y", str(output.resolve()),
        ],
    )


def compose_inset(
    caps: FFmpegCapabilities,
    base: Path,
    inset: Path,
    output: Path,
    *,
    pane: Pane,
    shape: InsetShape,
    frames: int,
    work_dir: Path,
    intermediate_args: list[str],
) -> None:
    """Lay the inset over the base through its mask, with a soft shadow
    under it and a border round it, every frame kept."""
    mask, shadow, ring, reach = inset_images(pane, shape, work_dir)
    sx, sy = pane.x - reach, pane.y - reach
    graph = (
        "[2:v]format=gray[mask];"
        "[1:v]format=yuva444p[clip];"
        "[clip][mask]alphamerge[cut];"
        f"[0:v][3:v]overlay={sx}:{sy}[shadowed];"
        f"[shadowed][cut]overlay={pane.x}:{pane.y}[inset];"
        f"[inset][4:v]overlay={sx}:{sy},format=yuv444p[v]"
    )
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-i", str(base.resolve()),
            "-i", str(inset.resolve()),
            "-loop", "1", "-i", str(mask.resolve()),
            "-loop", "1", "-i", str(shadow.resolve()),
            "-loop", "1", "-i", str(ring.resolve()),
            "-filter_complex", graph,
            "-map", "[v]",
            "-frames:v", str(frames),
            *intermediate_args,
            "-y", str(output.resolve()),
        ],
    )


def _cover(image: Image.Image, width: int, height: int, focus_x: float = 0.5) -> Image.Image:
    """``image`` scaled to cover ``width`` by ``height`` and cropped round
    ``focus_x``, as the renderer frames it."""
    scale = max(width / image.width, height / image.height)
    scaled = image.resize(
        (max(width, round(image.width * scale)), max(height, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = min(max(0, round(scaled.width * focus_x - width / 2)), scaled.width - width)
    top = (scaled.height - height) // 2
    return scaled.crop((left, top, left + width, top + height))


def compose_still(
    picture: Image.Image | None,
    speaker: Image.Image,
    width: int,
    height: int,
    layout: SceneLayout,
    *,
    speaker_x: float = 0.5,
    background: tuple[int, int, int] = (0x14, 0x18, 0x24),
) -> Image.Image:
    """One still of a split or inset frame, for the studio's preview: the
    same parts in the same places as the render (D-197)."""

    def part(which: str, w: int, h: int) -> Image.Image:
        if which == "speaker":
            return _cover(speaker.convert("RGB"), w, h, speaker_x)
        if picture is None:
            return Image.new("RGB", (w, h), background)
        return _cover(picture.convert("RGB"), w, h)

    frame = Image.new("RGB", (width, height), background)
    if layout.kind is LayoutKind.SPLIT:
        picture_pane, speaker_pane = split_panes(width, height, layout)
        for which, pane in (("picture", picture_pane), ("speaker", speaker_pane)):
            frame.paste(part(which, pane.width, pane.height), (pane.x, pane.y))
        if layout.divider:
            draw = ImageDraw.Draw(frame)
            thickness = _even(min(width, height) * DIVIDER)
            along = height if stacked(width, height) else width
            at = round(divider_position(width, height, layout) * along) - thickness // 2
            if stacked(width, height):
                box = (0, at, width, at + thickness - 1)
            else:
                box = (at, 0, at + thickness - 1, height)
            draw.rectangle(box, fill=(0, 0, 0))
        return frame

    pane = inset_pane(width, height, layout)
    base, small = ("picture", "speaker") if layout.inset_speaker else ("speaker", "picture")
    frame.paste(part(base, width, height), (0, 0))
    s = SUPERSAMPLE
    shape = layout.inset_shape
    mask = Image.new("L", (pane.width * s, pane.height * s), 0)
    _shape(ImageDraw.Draw(mask), (0, 0, mask.width - 1, mask.height - 1), shape, 255)
    mask = mask.resize((pane.width, pane.height), Image.Resampling.LANCZOS)
    border = max(2, round(pane.width * BORDER))
    outer = (pane.width + 2 * border, pane.height + 2 * border)
    ring = Image.new("L", (outer[0] * s, outer[1] * s), 0)
    _shape(ImageDraw.Draw(ring), (0, 0, ring.width - 1, ring.height - 1), shape, 255)
    ring = ring.resize(outer, Image.Resampling.LANCZOS)
    frame.paste((255, 255, 255), (pane.x - border, pane.y - border), ring)
    frame.paste(part(small, pane.width, pane.height), (pane.x, pane.y), mask)
    return frame
