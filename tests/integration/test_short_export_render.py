"""Check safe text pixels, real-clock progress, sync and saved delivery resolution."""
from pathlib import Path

import numpy as np
import pytest

from tests.integration import test_footage_sync as sync
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_render_from_plan import _frame_count
from voxframe.config.captions import CAPTION_PRESETS
from voxframe.config.settings import QualityPreset
from voxframe.config.short_export import PRESETS
from voxframe.config.style import get_template
from voxframe.plan.short_export import configure
from voxframe.plan.visual_director import direct
from voxframe.render.compose import render_from_plan
from voxframe.render.ffpath import run_ffmpeg

caps = sync.caps
pytestmark = pytest.mark.needs_ffmpeg


def pixels(caps, video: Path, folder: Path, seconds: float) -> np.ndarray:  # type: ignore[no-untyped-def]
    raw = folder / f"frame-{seconds}.rgb"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-ss", str(seconds), "-i", str(video),
        "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-y", str(raw)])
    return np.frombuffer(raw.read_bytes(), dtype=np.uint8).reshape(480, 270, 3)


@pytest.mark.parametrize("platform", list(PRESETS))
def test_platform_text_and_progress_stay_inside_guides_and_source_sync(caps, tmp_path: Path, platform: str) -> None:  # type: ignore[no-untyped-def]
    original = directed_recording(caps, tmp_path, .6)
    plan = configure(direct(original, "energy", match_captions=True), PRESETS[platform]["export"])
    result = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
        tmp_path / "export.mp4", height=480, quality=QualityPreset.DRAFT)
    sync._assert_in_sync(caps, result.video_path, list(sync.CLAPS))
    assert _frame_count(caps, result.video_path) == plan.total_frames
    area = plan.short_export.safe_area
    assert "ShortProgress" in result.ass_path.read_text()
    assert "ShortProgress" not in result.srt_path.read_text()
    amounts = []
    for t in (.25, 3.5, 6.9):
        frame = pixels(caps, result.video_path, tmp_path, t)
        bright = frame.max(axis=2) > 185
        outside = bright.copy()
        outside[max(0, int(area.top * 480) - 2):min(480, round((1 - area.bottom) * 480) + 3),
                max(0, int(area.left * 270) - 2):min(270, round((1 - area.right) * 270) + 3)] = False
        assert not outside.any(), f"{platform} text escaped its safe area at {t}"
        y = round((area.top + .01) * 480)
        rail = frame[y:y + 2]
        teal = (rail[:, :, 1] > 180) & (rail[:, :, 1].astype(int) - rail[:, :, 0] > 25) & (rail[:, :, 2] > 130)
        amounts.append(teal.sum() / 2)
    length = round((1 - area.left - area.right) * 270)
    assert amounts[0] < length * .1
    assert length * .4 < amounts[1] < length * .6
    assert amounts[2] > length * .9


@pytest.mark.parametrize("look", list(CAPTION_PRESETS))
def test_all_caption_looks_with_long_words_stay_inside_platform_area(caps, tmp_path: Path, look: str) -> None:  # type: ignore[no-untyped-def]
    plan = directed_recording(caps, tmp_path)
    scene = plan.scenes[0].model_copy(update={"caption_text":
        "Extraordinairementlongidentifiant Voici une première idée et une fin claire.",
        "caption_treatment": CAPTION_PRESETS[look]})
    plan = configure(plan.model_copy(update={"scenes": (scene,)}), PRESETS["tiktok"]["export"])
    result = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
        tmp_path / "caption.mp4", height=480, quality=QualityPreset.DRAFT)
    for t in (.25, 2.5, 5.5):
        frame = pixels(caps, result.video_path, tmp_path, t)
        bright = frame.max(axis=2) > 185
        assert not bright[:, :14].any() and not bright[:, 220:].any(), look
        assert not bright[:55].any() and not bright[365:].any(), look
