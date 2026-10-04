"""Measure clap sync through direction, and measure zoom in actual encoded frames."""
from pathlib import Path

import numpy as np
import pytest

from tests.integration import test_footage_sync as sync
from tests.integration.test_render_from_plan import _frame_count
from tests.integration.test_shorts_render import recording
from voxframe.config.settings import AspectRatio
from voxframe.config.style import get_template
from voxframe.config.visuals import VisualBeat
from voxframe.plan.scene_plan import Footage, PlanWord
from voxframe.plan.visual_director import direct, set_visual
from voxframe.render.compose import render_from_plan
from voxframe.render.compose.footage import render_footage_segment
from voxframe.render.ffpath import run_ffmpeg

caps = sync.caps
pytestmark = pytest.mark.needs_ffmpeg


def directed_recording(caps, tmp_path: Path, delay: float = 0):  # type: ignore[no-untyped-def]
    plan = recording(caps, tmp_path, delay)
    text = "Start with 3 ideas make each word count now."
    times = (.2, .7, 1.2, 2, 3, 3.6, 4.2, 5.2, 6)
    scene = plan.scenes[0].model_copy(update={"text": text, "words": tuple(
        PlanWord(text=w, start=t, end=t + .4) for w, t in zip(text.split(), times, strict=True))})
    return plan.model_copy(update={"aspect": AspectRatio.VERTICAL, "scenes": (scene,)})


@pytest.mark.parametrize("delay", [0, .6])
def test_punch_ins_and_graphics_keep_claps_voice_words_and_exact_frames(caps, tmp_path: Path, delay: float) -> None:  # type: ignore[no-untyped-def]
    original = directed_recording(caps, tmp_path, delay)
    plan = direct(original, "energy", match_captions=True)
    assert any(s.visual_beat.zoom > 1 for s in plan.scenes)
    result = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             tmp_path / "directed.mp4", height=480)
    sync._assert_in_sync(caps, result.video_path, list(sync.CLAPS))
    assert _frame_count(caps, result.video_path) == plan.total_frames
    assert "word" in result.srt_path.read_text() and "KEY POINT" not in result.srt_path.read_text()
    assert "Dialogue: 2," in result.ass_path.read_text()


def test_real_zoom_enlarges_subject_without_black_edges(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "square.mp4"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-f", "lavfi", "-i",
        "color=0x303030:s=640x360:r=25:d=1,drawbox=x=290:y=150:w=60:h=60:color=white:t=fill",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(source)])
    footage = Footage(path=str(source), width=640, height=360, fps=25, duration=1)
    areas = []
    for zoom in (1, 1.2):
        output = tmp_path / f"zoom-{zoom}.mp4"
        render_footage_segment(caps, footage, 0, output, width=270, height=480,
            fps=30, frames=30, intermediate_args=["-c:v", "libx264", "-pix_fmt", "yuv420p"], zoom=zoom)
        raw = output.with_suffix(".gray")
        run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-i", str(output), "-frames:v", "1",
            "-pix_fmt", "gray", "-f", "rawvideo", "-y", str(raw)])
        frame = np.frombuffer(raw.read_bytes(), dtype=np.uint8).reshape(480, 270)
        assert np.min(frame[[0, -1]]) > 20 and np.min(frame[:, [0, -1]]) > 20
        areas.append(np.count_nonzero(frame > 200))
    assert 1.35 < areas[1] / areas[0] < 1.55


def test_edited_text_and_zoom_appear_in_real_output(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    plan = directed_recording(caps, tmp_path)
    beat = VisualBeat(text="MAKE EVERY WORD COUNT", zoom=1.2, position="top")
    changed = set_visual(plan, 0, beat)
    result = render_from_plan(changed, Path(changed.audio_path), get_template(), caps,
                             tmp_path / "edited.mp4", height=480)
    assert "MAKE EVERY WORD COUNT" in result.ass_path.read_text().replace(r"\N", " ")
    frame = tmp_path / "text.png"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-ss", "2.5", "-i", str(result.video_path),
                                "-frames:v", "1", "-y", str(frame)])
    from PIL import Image
    pixels = np.array(Image.open(frame).convert("RGB"))
    assert (pixels[35:200].max(axis=2) > 180).sum() > 100


def test_direction_after_pause_cuts_and_short_selection_preserves_sync(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from voxframe.plan.pacing import apply_cuts, suggest_cuts
    from voxframe.plan.shorts import bounds, build_short, tokens

    original = recording(caps, tmp_path, .6)
    cut = suggest_cuts(original)[0]
    paced = apply_cuts(original, (cut["id"],))
    start, _ = bounds(paced, tokens(paced), 0, 1)
    short = build_short(paced, 0, 1)
    plan = direct(short, "energy")
    plan = set_visual(plan, 0, VisualBeat(text="First", zoom=1.2))
    result = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             tmp_path / "all-stages.mp4", height=480)
    sync._assert_in_sync(caps, result.video_path,
                         [1.5 - start / plan.fps, 4.5 - cut["seconds"] - start / plan.fps])
    assert _frame_count(caps, result.video_path) == plan.total_frames
