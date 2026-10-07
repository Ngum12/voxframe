"""Measure a real cutaway's interval and voice/footage sync around it."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.integration import test_footage_sync as sync
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_render_from_plan import _frame_count
from voxframe.config.style import get_template
from voxframe.config.visuals import VisualBeat
from voxframe.plan.scene_plan import PlanAsset
from voxframe.plan.visual_placement import Placement, place
from voxframe.render.compose import render_from_plan
from voxframe.render.ffpath import run_ffmpeg

caps = sync.caps
pytestmark = pytest.mark.needs_ffmpeg


def with_photo(caps, folder: Path, delay: float = 0):  # type: ignore[no-untyped-def]
    original = directed_recording(caps, folder, delay)
    photo = folder / "cutaway.png"
    Image.new("RGB", (640, 360), (160, 80, 0)).save(photo)
    asset = PlanAsset(id="orange", path=str(photo), width=640, height=360,
                     license_name="CC0", license_author="Test artist", license_source="local")
    return original.model_copy(update={"scenes": (original.scenes[0].model_copy(
        update={"asset": asset}),)})


@pytest.mark.parametrize("delay", [0, .6])
def test_cutaway_and_text_only_appear_on_selected_words_without_changing_sync(
    caps, tmp_path: Path, delay: float,
) -> None:  # type: ignore[no-untyped-def]
    original = with_photo(caps, tmp_path, delay)
    plan = place(original, [Placement(first_word=3, last_word=5, shot="picture", asset_scene=0,
                                     beat=VisualBeat(text="THREE STRONG IDEAS", position="top"))])
    result = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             tmp_path / "placed.mp4", height=240)
    assert _frame_count(caps, result.video_path) == original.total_frames
    sync._assert_in_sync(caps, result.video_path, list(sync.CLAPS))
    assert "THREE STRONG IDEAS" in result.ass_path.read_text().replace(r"\N", " ")
    for moment, expected in ((.5, False), (3, True), (6, False)):
        frame = tmp_path / f"frame-{moment}.png"
        run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-ss", str(moment), "-i",
            str(result.video_path), "-frames:v", "1", "-y", str(frame)])
        pixels = np.array(Image.open(frame).convert("RGB"))
        orange = (pixels[:, :, 0] > 100) & (pixels[:, :, 1] > 40) & (pixels[:, :, 1] < 110) & (
            pixels[:, :, 2] < 60)
        assert (orange.mean() > .3) == expected
    captions = result.srt_path.read_text()
    assert all(word in captions for word in ("Start", "ideas", "make", "count", "now"))
