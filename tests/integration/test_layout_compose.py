"""Split and inset frames, put together by FFmpeg and measured (D-197)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from voxframe.plan.scene_layout import InsetShape, LayoutKind, SceneLayout
from voxframe.render.compose.layout import compose_inset, compose_split, inset_pane, split_panes
from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import run_ffmpeg

pytestmark = pytest.mark.needs_ffmpeg

ARGS = ["-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-pix_fmt", "yuv444p"]
FRAMES = 30


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


def _solid(caps, colour: str, width: int, height: int, path: Path) -> Path:  # type: ignore[no-untyped-def]
    run_ffmpeg(caps.ffmpeg_path, [
        "-loglevel", "error", "-f", "lavfi", "-i", f"color={colour}:s={width}x{height}:r=30",
        "-frames:v", str(FRAMES), *ARGS, "-y", str(path),
    ])
    return path


def _frame(caps, video: Path, out: Path, number: int = 15) -> np.ndarray:  # type: ignore[no-untyped-def]
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-i", str(video), "-vf",
                                  f"select=eq(n\\,{number})", "-frames:v", "1", "-y", str(out)])
    return np.asarray(Image.open(out).convert("RGB")).astype(int)


def _frames(caps, video: Path) -> int:  # type: ignore[no-untyped-def]
    return int(subprocess.run(
        [caps.ffprobe_path, "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout.strip())


def _close(pixel: np.ndarray, rgb: tuple[int, int, int]) -> bool:
    return bool(np.abs(pixel - np.array(rgb)).max() < 30)


@pytest.mark.parametrize(("width", "height"), [(360, 640), (640, 360)])
def test_a_split_puts_each_part_in_its_place(caps, tmp_path: Path, width: int, height: int) -> None:  # type: ignore[no-untyped-def]
    layout = SceneLayout(kind=LayoutKind.SPLIT, split=0.4)
    picture_pane, speaker_pane = split_panes(width, height, layout)
    picture = _solid(caps, "red", picture_pane.width, picture_pane.height, tmp_path / "p.mp4")
    speaker = _solid(caps, "blue", speaker_pane.width, speaker_pane.height, tmp_path / "s.mp4")
    out = tmp_path / "split.mp4"
    compose_split(caps, picture, speaker, out, width=width, height=height, layout=layout,
                  frames=FRAMES, intermediate_args=ARGS)

    assert _frames(caps, out) == FRAMES
    frame = _frame(caps, out, tmp_path / "f.png")
    assert frame.shape[:2] == (height, width)
    assert _close(frame[picture_pane.y + 5, picture_pane.x + 5], (255, 0, 0))
    assert _close(frame[speaker_pane.y + speaker_pane.height - 5,
                        speaker_pane.x + speaker_pane.width - 5], (0, 0, 255))
    # The divider: dark, where the two meet.
    if height >= width:
        assert frame[speaker_pane.y, width // 2].max() < 60
    else:
        assert frame[height // 2, speaker_pane.x].max() < 60


def test_the_speaker_can_go_on_top(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    layout = SceneLayout(kind=LayoutKind.SPLIT, speaker_first=True)
    picture_pane, speaker_pane = split_panes(360, 640, layout)
    picture = _solid(caps, "red", picture_pane.width, picture_pane.height, tmp_path / "p.mp4")
    speaker = _solid(caps, "blue", speaker_pane.width, speaker_pane.height, tmp_path / "s.mp4")
    out = tmp_path / "split.mp4"
    compose_split(caps, picture, speaker, out, width=360, height=640, layout=layout,
                  frames=FRAMES, intermediate_args=ARGS)
    frame = _frame(caps, out, tmp_path / "f.png")
    assert _close(frame[10, 180], (0, 0, 255))
    assert _close(frame[630, 180], (255, 0, 0))


@pytest.mark.parametrize("shape", list(InsetShape))
def test_an_inset_is_cut_to_its_shape_with_a_border(caps, tmp_path: Path, shape: InsetShape) -> None:  # type: ignore[no-untyped-def]
    layout = SceneLayout(kind=LayoutKind.INSET, inset_shape=shape, inset_size=0.4)
    pane = inset_pane(360, 640, layout)
    base = _solid(caps, "green", 360, 640, tmp_path / "b.mp4")
    inset = _solid(caps, "magenta", pane.width, pane.height, tmp_path / "i.mp4")
    out = tmp_path / "inset.mp4"
    compose_inset(caps, base, inset, out, pane=pane, shape=shape, frames=FRAMES,
                  work_dir=tmp_path / "masks", intermediate_args=ARGS)

    assert _frames(caps, out) == FRAMES
    frame = _frame(caps, out, tmp_path / "f.png")
    cx, cy = pane.x + pane.width // 2, pane.y + pane.height // 2
    assert _close(frame[cy, cx], (255, 0, 255))
    # The corner of its box is outside a circle and a rounded corner: the base.
    assert _close(frame[pane.y + 1, pane.x + 1], (0, 128, 0)) or frame[pane.y + 1, pane.x + 1][1] > 90
    # Just outside its edge, at the middle of a side: the white border.
    assert frame[cy, pane.x - 2].min() > 180
    # Far from it, the base untouched.
    assert _close(frame[600, 20], (0, 128, 0))
