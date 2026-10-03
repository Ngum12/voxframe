"""Pop-ups in the finished video, measured on its frames (D-198)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from voxframe.config.settings import AspectRatio, QualityPreset
from voxframe.config.style import get_template
from voxframe.plan.overlays import Entrance, Overlay, OverlayKind
from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan
from voxframe.render.compose import render_from_plan
from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import run_ffmpeg

pytestmark = pytest.mark.needs_ffmpeg

WIDTH, HEIGHT = 360, 640
TOKENS = "Floods hit Douala again this week".split()


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        found = probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")
    if not found.has_libass:
        pytest.skip("FFmpeg has no libass")
    return found


@pytest.fixture(scope="module")
def audio(caps, tmp_path_factory: pytest.TempPathFactory) -> Path:  # type: ignore[no-untyped-def]
    path = tmp_path_factory.mktemp("popups") / "talk.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-f", "lavfi", "-i",
                                  "sine=frequency=220:duration=3", "-y", str(path)])
    return path


def _plan(audio: Path, *overlays: Overlay, progress: bool = False) -> ScenePlan:
    words = tuple(
        PlanWord(text=t, start=0.3 + 0.4 * i, end=0.6 + 0.4 * i) for i, t in enumerate(TOKENS)
    )
    return ScenePlan(
        audio_path=str(audio), audio_sha256="0" * 64, audio_duration=3.0, fps=30.0,
        total_frames=90, aspect=AspectRatio.VERTICAL,
        scenes=(PlannedScene(index=0, start_frame=0, end_frame=90, text=" ".join(TOKENS),
                             words=words),),
        overlays=overlays, progress_bar=progress,
    )


def _render(caps, plan: ScenePlan, out: Path, cache: Path | None = None) -> Path:  # type: ignore[no-untyped-def]
    return render_from_plan(
        plan, Path(plan.audio_path), get_template(), caps, out, quality=QualityPreset.DRAFT,
        height=HEIGHT, cache_dir=cache,
    ).video_path


def _frame(caps, video: Path, seconds: float, tmp: Path) -> np.ndarray:  # type: ignore[no-untyped-def]
    out = tmp / f"f{seconds:.2f}.png"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-i", str(video), "-vf",
                                  f"select=eq(n\\,{int(seconds * 30)})", "-frames:v", "1",
                                  "-y", str(out)])
    return np.asarray(Image.open(out).convert("RGB")).astype(int)


def _region(frame: np.ndarray, x: float, y: float, half: int = 40) -> np.ndarray:
    cx, cy = int(x * WIDTH), int(y * HEIGHT)
    return frame[cy - half : cy + half, cx - half : cx + half]


def _changed(a: np.ndarray, b: np.ndarray) -> float:
    return float((np.abs(a - b).sum(axis=2) > 60).mean())


def test_a_sticker_appears_on_its_word_and_pops_in(caps, audio: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    # "Douala", word 2, is said at 1.1 s.
    sticker = Overlay(id="w", kind=OverlayKind.STICKER, sticker="water-wave", scene=0, word=2,
                      x=0.5, y=0.3, size=0.4, seconds=1.0)
    video = _render(caps, _plan(audio, sticker), tmp_path / "s.mp4")
    before = _frame(caps, video, 1.0, tmp_path)
    popping = _frame(caps, video, 1.14, tmp_path)
    shown = _frame(caps, video, 1.6, tmp_path)
    after = _frame(caps, video, 2.4, tmp_path)

    blank = _region(before, 0.5, 0.3, 70)
    assert _changed(blank, _region(after, 0.5, 0.3, 70)) < 0.01
    full = _changed(blank, _region(shown, 0.5, 0.3, 70))
    growing = _changed(blank, _region(popping, 0.5, 0.3, 70))
    assert full > 0.3
    assert 0 < growing < full


def test_slide_and_bounce_come_from_below_and_above(caps, audio: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    def top_of(entrance: Entrance, at: float) -> int:
        overlay = Overlay(id="s", kind=OverlayKind.STICKER, sticker="fire", scene=0, word=2,
                          x=0.5, y=0.3, size=0.3, seconds=1.2, entrance=entrance)
        video = _render(caps, _plan(audio, overlay), tmp_path / f"{entrance.value}.mp4")
        frame = _frame(caps, video, at, tmp_path)
        # Orange on the dark background, even while it fades in.
        fire = (frame[..., 0] > 90) & (frame[..., 0] - frame[..., 2] > 60)
        fire[int(HEIGHT * 0.6) :] = False  # not the captions
        return int(np.nonzero(fire)[0].min())

    settled = top_of(Entrance.NONE, 1.6)
    assert top_of(Entrance.SLIDE, 1.2) > settled + 3
    assert top_of(Entrance.BOUNCE, 1.17) < settled - 3


def test_the_progress_bar_fills_with_the_video(caps, audio: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    video = _render(caps, _plan(audio, progress=True), tmp_path / "p.mp4")
    for seconds in (0.75, 1.5, 2.25):
        frame = _frame(caps, video, seconds, tmp_path)
        row = frame[HEIGHT - 2]
        gold = (row[:, 0] > 200) & (row[:, 1] > 150) & (row[:, 2] < 100)
        filled = np.nonzero(gold)[0].max() / WIDTH
        assert filled == pytest.approx(seconds / 3.0, abs=0.06)


def test_a_text_pop_up_is_drawn_from_its_word(caps, audio: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    pill = Overlay(id="t", kind=OverlayKind.TEXT, text="3 TIPS", scene=0, word=1,
                   x=0.5, y=0.15, size=0.4, seconds=1.0, entrance=Entrance.NONE)
    video = _render(caps, _plan(audio, pill), tmp_path / "t.mp4")
    before = _frame(caps, video, 0.6, tmp_path)
    during = _frame(caps, video, 1.0, tmp_path)
    after = _frame(caps, video, 1.9, tmp_path)
    gold = lambda f: ((f[..., 0] > 200) & (f[..., 1] > 170) & (f[..., 2] < 90))[: HEIGHT // 3]  # noqa: E731
    assert not gold(before).any() and not gold(after).any()
    assert gold(during).sum() > 500


def test_changing_a_sticker_burns_again_and_keeps_the_pictures(caps, audio: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cache = tmp_path / "cache" / "segments"
    first = Overlay(id="w", kind=OverlayKind.STICKER, sticker="fire", scene=0, word=0)
    _render(caps, _plan(audio, first), tmp_path / "a.mp4", cache)
    pictures = tmp_path / "cache" / "pictures"
    _render(caps, _plan(audio, first.model_copy(update={"x": 0.2})), tmp_path / "b.mp4", cache)
    # Two burned versions, one set of uncaptioned pictures.
    assert len(list(pictures.glob("*.mp4"))) == 2
    assert len(list((pictures / "clean").glob("*.mp4"))) == 1
