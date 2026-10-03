"""Split and inset scenes, rendered from a plan (D-197).

A red picture and a blue recording of the speaker, so every pixel says which
it came from.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from voxframe.config.settings import AspectRatio, QualityPreset
from voxframe.config.style import get_template
from voxframe.plan.scene_layout import LayoutKind, SceneLayout
from voxframe.plan.scene_plan import Footage, PlanAsset, PlannedScene, ScenePlan, Shot
from voxframe.render.compose import render_from_plan
from voxframe.render.compose.layout import inset_pane, split_panes
from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import run_ffmpeg

pytestmark = pytest.mark.needs_ffmpeg

WIDTH, HEIGHT = 360, 640


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        found = probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")
    if not found.has_libass:
        pytest.skip("FFmpeg has no libass")
    return found


@pytest.fixture
def sources(caps, tmp_path: Path) -> tuple[Path, Path]:  # type: ignore[no-untyped-def]
    recording = tmp_path / "talk.mp4"
    run_ffmpeg(caps.ffmpeg_path, [
        "-loglevel", "error", "-f", "lavfi", "-i", "color=blue:s=640x360:r=30:d=4",
        "-f", "lavfi", "-i", "sine=f=220:d=4", "-shortest", "-pix_fmt", "yuv420p",
        "-y", str(recording),
    ])
    picture = tmp_path / "flood.png"
    Image.new("RGB", (900, 900), (220, 20, 20)).save(picture)
    return recording, picture


def _plan(recording: Path, picture: Path, first: SceneLayout, second: SceneLayout) -> ScenePlan:
    asset = PlanAsset(
        id="flood", path=str(picture), width=900, height=900,
        license_name="CC0", license_author="Test", license_source="Test",
    )
    scenes = (
        PlannedScene(index=0, start_frame=0, end_frame=60, text="", asset=asset,
                     shot=Shot.PICTURE, footage_start=0.0, layout=first),
        PlannedScene(index=1, start_frame=60, end_frame=120, text="", asset=asset,
                     shot=Shot.PICTURE, footage_start=2.0, layout=second),
    )
    return ScenePlan(
        audio_path=str(recording), audio_sha256="0" * 64, audio_duration=4.0, fps=30.0,
        total_frames=120, aspect=AspectRatio.VERTICAL, scenes=scenes,
        footage=Footage(path=str(recording), width=640, height=360, fps=30.0, duration=4.0),
    )


def _frame(caps, video: Path, number: int, out: Path) -> np.ndarray:  # type: ignore[no-untyped-def]
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-i", str(video), "-vf",
                                  f"select=eq(n\\,{number})", "-frames:v", "1", "-y", str(out)])
    return np.asarray(Image.open(out).convert("RGB")).astype(int)


def _red(pixel: np.ndarray) -> bool:
    return bool(pixel[0] > 150 and pixel[2] < 90)


def _blue(pixel: np.ndarray) -> bool:
    return bool(pixel[2] > 150 and pixel[0] < 90)


def test_a_split_and_an_inset_in_one_video(caps, sources, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    recording, picture = sources
    split = SceneLayout(kind=LayoutKind.SPLIT, split=0.5)
    inset = SceneLayout(kind=LayoutKind.INSET, inset_size=0.4, inset_x=0.7, inset_y=0.3)
    result = render_from_plan(
        _plan(recording, picture, split, inset), recording, get_template(), caps,
        tmp_path / "out" / "talk.mp4", quality=QualityPreset.DRAFT, height=HEIGHT,
        cache_dir=tmp_path / "cache" / "segments",
    )
    # render_from_plan checks the frame count itself; it must be exact.
    assert (result.width, result.height) == (WIDTH, HEIGHT)

    top, bottom = split_panes(WIDTH, HEIGHT, split)
    frame = _frame(caps, result.video_path, 30, tmp_path / "split.png")
    assert _red(frame[top.y + top.height // 3, WIDTH // 2])
    assert _blue(frame[bottom.y + bottom.height // 2, WIDTH // 6])

    pane = inset_pane(WIDTH, HEIGHT, inset)
    frame = _frame(caps, result.video_path, 90, tmp_path / "inset.png")
    assert _blue(frame[pane.y + pane.height // 2, pane.x + pane.width // 2])
    assert _red(frame[HEIGHT - 120, 30])


def test_the_picture_can_be_the_inset(caps, sources, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    recording, picture = sources
    inset = SceneLayout(kind=LayoutKind.INSET, inset_speaker=False)
    full = SceneLayout()
    result = render_from_plan(
        _plan(recording, picture, inset, full), recording, get_template(), caps,
        tmp_path / "out" / "talk.mp4", quality=QualityPreset.DRAFT, height=HEIGHT,
    )
    pane = inset_pane(WIDTH, HEIGHT, inset)
    frame = _frame(caps, result.video_path, 30, tmp_path / "f.png")
    assert _red(frame[pane.y + pane.height // 2, pane.x + pane.width // 2])
    assert _blue(frame[HEIGHT - 120, 30])


def test_a_shared_frame_needs_the_footage(sources) -> None:  # type: ignore[no-untyped-def]
    from voxframe.plan.scene_plan import PlanError

    recording, picture = sources
    plan = _plan(recording, picture, SceneLayout(kind=LayoutKind.SPLIT), SceneLayout())
    with pytest.raises((PlanError, ValueError)):
        ScenePlan.model_validate({**plan.model_dump(), "footage": None})
