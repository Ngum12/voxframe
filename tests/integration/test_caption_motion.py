"""Caption animations and transitions, measured on frames FFmpeg draws (D-196).

Each claim is checked where a viewer would see it: on the picture libass
burns in, frame by frame, against the words' own timestamps.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voxframe.config.style import (
    CaptionAnimation,
    CaptionBacking,
    CaptionStyle,
    CaptionTransition,
)
from voxframe.models.scene import Scene
from voxframe.models.transcript import Word
from voxframe.render.captions.ass import write_ass

WIDTH, HEIGHT = 540, 960
WORDS = (
    ("Floods", 0.30, 0.70),
    ("hit", 0.75, 0.95),
    ("Douala", 1.00, 1.50),
    ("again", 1.55, 1.90),
    ("this", 2.00, 2.20),
    ("week", 2.25, 2.60),
)
#: The highlight colour, as RGB: ASS ``&H0000D7FF`` is blue 00, green D7, red FF.
GOLD = (255, 215, 0)


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities

    try:
        found = probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")
    if not found.has_libass:
        pytest.skip("FFmpeg has no libass")
    return found


def _scene(emphasis: tuple[int, ...] = ()) -> Scene:
    return Scene(
        index=0, start_frame=0, end_frame=90,
        words=tuple(Word(text=t, start=s, end=e) for t, s, e in WORDS),
        emphasis=emphasis,
    )


def _frames(  # type: ignore[no-untyped-def]
    caps, tmp_path: Path, style: CaptionStyle, times: list[float], scene: Scene | None = None,
    *, size: tuple[int, int] = (WIDTH, HEIGHT),
) -> list[np.ndarray]:
    """The frames shown at ``times``, captions over black, as RGB arrays."""
    from PIL import Image

    from voxframe.assets import fonts_dir
    from voxframe.render.ffpath import ass_filter, run_ffmpeg

    width, height = size
    ass = write_ass(tmp_path / "c.ass", (scene or _scene(),), style, width, height, 30.0)
    caption_filter, cwd = ass_filter(ass, fontsdir=fonts_dir())
    frames = []
    for number, t in enumerate(times):
        # Frame n is shown from n/30 s: ask for the frame that is on screen.
        frame = round(t * 30)
        out = tmp_path / f"f{number}.png"
        run_ffmpeg(
            caps.ffmpeg_path,
            ["-loglevel", "error", "-f", "lavfi", "-i", f"color=black:s={width}x{height}:r=30:d=3",
             "-vf", f"{caption_filter},select=eq(n\\,{frame})", "-frames:v", "1",
             "-y", str(out.resolve())],
            cwd=cwd,
        )
        frames.append(np.asarray(Image.open(out).convert("RGB")).astype(int))
    return frames


def _lit(frame: np.ndarray, threshold: int = 120) -> np.ndarray:
    return frame.max(axis=2) > threshold


def _gold(frame: np.ndarray) -> np.ndarray:
    r, g, b = frame[..., 0], frame[..., 1], frame[..., 2]
    return (r > 200) & (g > 150) & (g < 240) & (b < 90)


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    assert len(xs), "nothing drawn"
    return int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())


def _outline(**changes: object) -> CaptionStyle:
    """Outline captions: no box, so only the words themselves are lit."""
    return CaptionStyle(backing=CaptionBacking.OUTLINE, **changes)  # type: ignore[arg-type]


class TestWordsPlacedOneByOne:
    @pytest.mark.parametrize("shape", [(1080, 1920), (1920, 1080)])
    def test_land_where_libass_puts_the_line(self, caps, tmp_path: Path, shape) -> None:  # type: ignore[no-untyped-def]
        # After the last word, both show every word white but the last.
        line = _frames(caps, tmp_path, _outline(), [2.9], size=shape)[0]
        bounce = _outline(animation=CaptionAnimation.BOUNCE)
        words = _frames(caps, tmp_path, bounce, [2.9], size=shape)[0]
        a, b = _lit(line), _lit(words)
        for edge_a, edge_b in zip(_bbox(a), _bbox(b), strict=True):
            assert abs(edge_a - edge_b) <= 2
        assert (a & b).sum() / (a | b).sum() > 0.8


class TestPop:
    def test_a_word_appears_only_once_it_is_said(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        style = _outline(animation=CaptionAnimation.POP)
        before, first, second = _frames(caps, tmp_path, style, [0.25, 0.5, 0.85], _scene())
        assert not _lit(before).any()
        assert _lit(first).sum() < _lit(second).sum()
        # Only "Floods" so far: everything lit is left of where "hit" goes.
        _, right_first, _, _ = _bbox(_lit(first))
        _, right_second, _, _ = _bbox(_lit(second))
        assert right_second > right_first + 20

    def test_it_grows_from_its_own_centre(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        style = _outline(animation=CaptionAnimation.POP)
        growing, settled = _frames(caps, tmp_path, style, [0.34, 0.6])
        g_left, g_right, _, _ = _bbox(_lit(growing))
        s_left, s_right, _, _ = _bbox(_lit(settled))
        assert g_right - g_left < s_right - s_left
        assert abs((g_left + g_right) / 2 - (s_left + s_right) / 2) <= 3


class TestBounce:
    def test_the_spoken_word_jumps_and_the_others_stay(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        style = _outline(animation=CaptionAnimation.BOUNCE)
        # "Douala" is said at 1.00 s and is at the top of its jump 90 ms on.
        lit_early, peak = _frames(caps, tmp_path, style, [1.0, 1.1])
        size = style.font_size_px(HEIGHT)
        assert _bbox(_gold(lit_early))[2] - _bbox(_gold(peak))[2] > 0.15 * size
        # "Floods", left of it, has not moved.
        floods = slice(0, _bbox(_gold(peak))[0] - size)
        assert _bbox(_lit(lit_early)[:, floods])[2] == _bbox(_lit(peak)[:, floods])[2]

    def test_it_does_not_jump_before_the_word_is_said(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        style = _outline(animation=CaptionAnimation.BOUNCE)
        # "Floods" is lit from the caption's start but said at 0.30 s.
        waiting, said = _frames(caps, tmp_path, style, [0.2, 0.4])
        assert _bbox(_gold(waiting))[2] - _bbox(_gold(said))[2] > 0.15 * style.font_size_px(HEIGHT)
        (settled,) = _frames(caps, tmp_path, style, [0.65])
        assert _bbox(_gold(settled))[2] == _bbox(_gold(waiting))[2]


class TestSpotlight:
    def test_a_box_sits_behind_the_spoken_word(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        style = _outline(animation=CaptionAnimation.SPOTLIGHT)
        (frame,) = _frames(caps, tmp_path, style, [1.3])
        box = _gold(frame)
        left, right, top, bottom = _bbox(box)
        size = style.font_size_px(HEIGHT)
        assert bottom - top >= size
        # Dark text inside the box: the word, readable on it.
        inside = frame[top:bottom, left:right].max(axis=2) < 60
        assert inside.sum() > 0.1 * (right - left) * (bottom - top)


class TestKaraoke:
    def test_a_word_fills_from_the_left_as_it_is_said(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        style = _outline(animation=CaptionAnimation.KARAOKE)
        (frame,) = _frames(caps, tmp_path, style, [1.25])
        gold = _gold(frame)
        white = _lit(frame) & ~gold
        # Mid-"Douala": the words before it and its first half are gold; its
        # second half and the rest of the line are white.
        _, gold_right, top, bottom = _bbox(gold)
        row = slice(top, bottom + 1)
        assert white[row][:, gold_right + 1 :].sum() > 0
        (later,) = _frames(caps, tmp_path, style, [1.45])
        assert _bbox(_gold(later)[row])[1] > gold_right


class TestTypewriter:
    def test_words_not_yet_said_are_not_shown(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        style = _outline(animation=CaptionAnimation.TYPEWRITER)
        early, later = _frames(caps, tmp_path, style, [0.5, 1.7])
        assert _lit(early).sum() < _lit(later).sum() / 3
        # The second line ("this week") has not started at 1.7 s.
        _, _, _, bottom_later = _bbox(_lit(later))
        (whole,) = _frames(caps, tmp_path, style, [2.9])
        assert _bbox(_lit(whole))[3] >= bottom_later


class TestEmphasis:
    def test_an_emphasised_word_is_larger(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        # "Douala" as the spoken word, and "Douala" emphasised.
        (spoken,) = _frames(caps, tmp_path, _outline(), [1.2], _scene())
        style = _outline(highlight_enabled=False)
        (plain,) = _frames(caps, tmp_path, style, [2.9], _scene())
        (stressed,) = _frames(caps, tmp_path, style, [2.9], _scene(emphasis=(2,)))
        assert not _gold(plain).any()
        left, right, top, bottom = _bbox(_gold(spoken))
        s_left, s_right, s_top, s_bottom = _bbox(_gold(stressed))
        scale = style.emphasis_scale
        assert (s_right - s_left) / (right - left) == pytest.approx(scale, rel=0.08)
        assert (s_bottom - s_top) / (bottom - top) == pytest.approx(scale, rel=0.08)


class TestTransitions:
    @pytest.mark.parametrize("animation", [CaptionAnimation.HIGHLIGHT, CaptionAnimation.BOUNCE])
    def test_fade_comes_in_and_goes_out(self, caps, tmp_path: Path, animation) -> None:  # type: ignore[no-untyped-def]
        style = _outline(animation=animation, transition=CaptionTransition.FADE)
        start, middle, end = _frames(caps, tmp_path, style, [0.0, 0.1, 1.5])
        (late,) = _frames(caps, tmp_path, style, [2.97])
        assert start.max() < 60
        assert middle.max() > start.max()
        assert end.max() > 200
        assert late.max() < end.max()

    @pytest.mark.parametrize("animation", [CaptionAnimation.HIGHLIGHT, CaptionAnimation.SPOTLIGHT])
    def test_slide_rises_into_place(self, caps, tmp_path: Path, animation) -> None:  # type: ignore[no-untyped-def]
        style = _outline(animation=animation, transition=CaptionTransition.SLIDE)
        rising, settled = _frames(caps, tmp_path, style, [0.1, 0.5])
        assert _bbox(_lit(rising))[3] > _bbox(_lit(settled))[3] + 3

    @pytest.mark.parametrize(
        ("transition", "smaller"), [(CaptionTransition.POP, True), (CaptionTransition.ZOOM, False)]
    )
    @pytest.mark.parametrize("animation", [CaptionAnimation.HIGHLIGHT, CaptionAnimation.BOUNCE])
    def test_pop_and_zoom_scale_the_caption(  # type: ignore[no-untyped-def]
        self, caps, tmp_path: Path, transition, smaller, animation
    ) -> None:
        style = _outline(animation=animation, transition=transition)
        entering, settled = _frames(caps, tmp_path, style, [0.07, 0.5])
        # Still fading in: faint, so counted from a low level.
        widths = [_bbox(_lit(f, 20))[1] - _bbox(_lit(f, 20))[0] for f in (entering, settled)]
        assert (widths[0] < widths[1]) is smaller
        assert abs(widths[0] - widths[1]) > 5
