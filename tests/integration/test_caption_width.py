"""Captions fit the frame they are burned into, at every shape (D-194).

Lines used to end at 32 characters whatever the frame. The font is sized by
the frame's height, so on a vertical frame -- under a third as wide as a
landscape one at the same height -- a line ran across the whole width and a
long one off it. These burn real captions with libass and measure them.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voxframe.assets import FontsMissing, fonts_dir
from voxframe.config.style import BUILTIN_TEMPLATES, CaptionStyle
from voxframe.models.scene import Scene
from voxframe.models.transcript import Word
from voxframe.render.captions.ass import _wrap_words, write_ass
from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import ass_filter, run_ffmpeg

pytestmark = pytest.mark.needs_ffmpeg

#: Long words as well as short, so a line is tested near its limit.
TEXT = (
    "The extraordinary winter morning was bright and remarkably cold, and we "
    "walked along the frozen river towards the abandoned watermill together"
)

SHAPES = {"vertical": (1080, 1920), "square": (1080, 1080), "landscape": (1920, 1080)}


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


def _scene() -> Scene:
    words = tuple(
        Word(text=token, start=i * 0.25, end=i * 0.25 + 0.2) for i, token in enumerate(TEXT.split())
    )
    return Scene(index=0, start_frame=0, end_frame=round(len(words) * 0.25 * 30) + 30, words=words)


def _widest_caption(caps, style: CaptionStyle, width: int, height: int, tmp_path: Path) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    """The left- and right-most lit columns of every caption page, burned by libass."""
    try:
        fonts = fonts_dir()
    except FontsMissing:
        pytest.skip("the bundled fonts are missing")
    # White text with no box or highlight colour, on black, so lit pixels
    # are exactly the caption.
    plain = style.model_copy(
        update={
            "primary_color": "&H00FFFFFF", "highlight_color": "&H00FFFFFF",
            "box_color": "&HFF000000", "outline_color": "&HFF000000",
            "highlight_enabled": False, "shadow_depth": 0.0,
        }
    )
    ass = write_ass(tmp_path / "c.ass", (_scene(),), plain, width, height, 30.0)
    caption_filter, cwd = ass_filter(ass, fontsdir=fonts)
    raw = tmp_path / "frames.gray"
    seconds = len(TEXT.split()) * 0.25
    run_ffmpeg(
        caps.ffmpeg_path,
        ["-loglevel", "error", "-f", "lavfi", "-i", f"color=black:s={width}x{height}:r=4:d={seconds:.2f}",
         "-vf", f"{caption_filter},format=gray", "-f", "rawvideo", "-y", str(raw.resolve())],
        cwd=cwd,
    )
    frames = np.fromfile(raw, dtype=np.uint8).reshape(-1, height, width)
    lit = np.where(frames.max(axis=(0, 1)) > 100)[0]
    assert lit.size, "no caption was drawn"
    return int(lit.min()), int(lit.max())


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("template", sorted(BUILTIN_TEMPLATES))
def test_every_caption_stays_inside_the_side_margins(
    caps, template: str, shape: str, tmp_path: Path  # type: ignore[no-untyped-def]
) -> None:
    from voxframe.config.style import get_template

    style = get_template(template).captions
    width, height = SHAPES[shape]
    left, right = _widest_caption(caps, style, width, height, tmp_path)
    # The margin, less a few pixels for the outline and antialiasing.
    margin = style.margin_horizontal_px(width) - 0.01 * width
    assert left >= margin and right <= width - margin, (
        f"{template} {shape}: captions span {left}..{right} of {width}, "
        f"past the {style.margin_horizontal_px(width)} px margins"
    )


@pytest.mark.parametrize("template", sorted(BUILTIN_TEMPLATES))
def test_landscape_lines_are_as_they_were(template: str) -> None:
    """A landscape frame fits 32 characters easily: nothing changes there."""
    from voxframe.config.style import get_template

    style = get_template(template).captions
    words = _scene().words
    assert _wrap_words(words, style, 1920, 1080) == _wrap_words(words, style)


def test_a_vertical_frame_gets_shorter_lines() -> None:
    from voxframe.config.style import get_template

    style = get_template("clean-educational").captions
    words = _scene().words
    vertical = _wrap_words(words, style, 1080, 1920)
    assert len(vertical) > len(_wrap_words(words, style))
