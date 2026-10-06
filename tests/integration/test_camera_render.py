"""Measure the chosen movement in encoded preview and export frames."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from tests.integration import test_render_from_plan as fixtures
from voxframe.config.camera import CameraMove
from voxframe.config.style import get_template
from voxframe.plan.camera_studio import configure
from voxframe.render.compose.scenes import render_scene_segments
from voxframe.render.ffpath import run_ffmpeg
from voxframe.render.motion.preview import camera_preview

caps = fixtures.caps
pytestmark = pytest.mark.needs_ffmpeg


def camera_photo(path: Path) -> Path:
    photo = Image.new("RGB", (900, 900), (55, 55, 55))
    draw = ImageDraw.Draw(photo)
    draw.rectangle((410, 410, 490, 490), fill="white")
    draw.rectangle((100, 100, 150, 150), fill=(100, 100, 100))
    photo.save(path)
    return path


def _frames(caps, video, tmp_path):
    target = tmp_path / f"{video.stem}-{video.parent.name}.gray"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-i", str(video),
        "-vf", "scale=480:480", "-pix_fmt", "gray", "-f", "rawvideo", "-y", str(target)])
    return np.frombuffer(target.read_bytes(), dtype=np.uint8).reshape(-1, 480, 480)


@pytest.mark.parametrize("direction", ["in", "out", "left", "right", "up", "down"])
def test_direction_in_real_preview_and_export(caps, tmp_path, direction):
    from voxframe.config.settings import AspectRatio

    photo = camera_photo(tmp_path / "photo.png")
    plan = fixtures._plan(photo, scene_count=1, duration=2, aspect=AspectRatio.SQUARE)
    plan = configure(plan, 0, True, CameraMove(direction=direction, strength=1))
    preview, seconds = camera_preview(plan, 0, tmp_path / "previews")
    exported = render_scene_segments(plan, caps, tmp_path / "segments", width=480, height=480,
        motion=get_template().motion, background="0x18212b")[0].path
    assert seconds == 2
    for video in (preview, exported):
        frames = _frames(caps, video, tmp_path)
        assert len(frames) == 60
        first, last = frames[0], frames[-1]
        assert min(first[0].min(), first[-1].min(), last[0].min(), last[-1].min()) > 25
        fy, fx = np.where(first > 200)
        ly, lx = np.where(last > 200)
        if direction == "in":
            assert len(lx) > len(fx) * 1.2
        elif direction == "out":
            assert len(fx) > len(lx) * 1.2
        elif direction == "left":
            assert lx.mean() - fx.mean() > 30
        elif direction == "right":
            assert fx.mean() - lx.mean() > 30
        elif direction == "up":
            assert ly.mean() - fy.mean() > 30
        else:
            assert fy.mean() - ly.mean() > 30


def test_preview_duration_uses_original_scene_speed_and_hold_is_static(caps, tmp_path):
    from voxframe.config.settings import AspectRatio

    photo = camera_photo(tmp_path / "photo.png")
    plan = fixtures._plan(photo, scene_count=1, duration=24, aspect=AspectRatio.SQUARE)
    plan = configure(plan, 0, True, CameraMove(direction="in", strength=1))
    preview, seconds = camera_preview(plan, 0, tmp_path / "preview")
    assert seconds == 12
    assert camera_preview(plan, 0, tmp_path / "preview")[0] == preview
    frames = _frames(caps, preview, tmp_path)
    ratio = np.count_nonzero(frames[-1] > 200) / np.count_nonzero(frames[0] > 200)
    assert 1.08 < ratio < 1.22  # Half the full 15% zoom, rather than rushed to the ending.
    plan = configure(plan, 0, False, None)
    still, seconds = camera_preview(plan, 0, tmp_path / "preview")
    assert still != preview
    frames = _frames(caps, still, tmp_path)
    assert np.abs(frames[0].astype(float) - frames[-1].astype(float)).mean() < .1


@pytest.mark.parametrize("missing_speaker", [False, True])
def test_hold_keeps_photo_in_export_and_missing_speaker_fallback(caps, tmp_path, missing_speaker):
    from voxframe.config.settings import AspectRatio
    from voxframe.plan.scene_plan import Footage, Shot

    photo = camera_photo(tmp_path / "photo.png")
    plan = fixtures._plan(photo, scene_count=1, duration=2, aspect=AspectRatio.SQUARE)
    plan = configure(plan, 0, False, None)
    if missing_speaker:
        scene = plan.scenes[0].model_copy(update={"shot": Shot.SPEAKER, "footage_start": 0})
        plan = plan.model_copy(update={"scenes": (scene,), "footage": Footage(
            path=str(tmp_path / "gone.mp4"), width=900, height=900, fps=30, duration=2)})
    segment = render_scene_segments(plan, caps, tmp_path / "segments", width=480, height=480,
        motion=get_template().motion, background="0x18212b")[0]
    frames = _frames(caps, segment.path, tmp_path)
    assert len(frames) == 60
    assert np.count_nonzero(frames[0] > 200) > 1000  # The white subject, not a plain background.
    assert np.abs(frames[0].astype(float) - frames[-1].astype(float)).mean() < .1
