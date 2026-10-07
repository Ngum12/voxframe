"""Reordered recording passages keep the measured claps on their flashes."""
from pathlib import Path

import pytest

from tests.integration import test_footage_sync as sync
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_render_from_plan import _frame_count
from voxframe.config.style import get_template
from voxframe.plan.story_composer import StoryBlock, compose
from voxframe.render.compose import render_from_plan

caps = sync.caps
pytestmark = pytest.mark.needs_ffmpeg


@pytest.mark.parametrize("delay", [0, .6])
def test_reverse_order_audio_picture_captions_and_frames(caps, tmp_path: Path,
                                                       delay: float) -> None:  # type: ignore[no-untyped-def]
    original = directed_recording(caps, tmp_path, delay)
    plan = compose(original, [StoryBlock(role="hook", first_word=4, last_word=8),
                              StoryBlock(role="ending", first_word=0, last_word=3)])
    result = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             tmp_path / "reordered.mp4", height=240)
    expected = [s.start_frame / plan.fps + clap - s.audio_start
                for s in plan.scenes for clap in sync.CLAPS
                if s.audio_start <= clap < s.audio_start + s.duration_frames / plan.fps]
    sync._assert_in_sync(caps, result.video_path, expected)
    assert _frame_count(caps, result.video_path) == plan.total_frames
    captions = result.srt_path.read_text()
    assert captions.index("make") < captions.index("Start")
    assert original.scenes[0].audio_start is None
