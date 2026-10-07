"""Kit transitions and photo styling keep the exported speech in sync."""
from pathlib import Path

import pytest

from tests.integration import test_footage_sync as sync
from tests.integration.test_render_from_plan import _frame_count
from tests.integration.test_visual_placement_render import with_photo
from voxframe.config.camera import CameraMove
from voxframe.config.creative_presets import BeatStyle, CreativeSettings
from voxframe.config.style import get_template
from voxframe.config.transitions import TRANSITION_PRESETS
from voxframe.config.visuals import VisualBeat
from voxframe.plan.signature_kit import apply_kit
from voxframe.plan.visual_placement import Placement, place
from voxframe.render.compose import render_from_plan

caps = sync.caps
pytestmark = pytest.mark.needs_ffmpeg


def test_visual_kit_keeps_frame_count_source_sync_and_current_text(caps, tmp_path: Path):  # type: ignore[no-untyped-def]
    original = with_photo(caps, tmp_path, delay=.6)
    story = place(original, [Placement(first_word=3, last_word=4, shot="picture", asset_scene=0,
                                       beat=VisualBeat(text="CURRENT WORDS", position="top"))])
    kit = CreativeSettings(transition_treatment=TRANSITION_PRESETS["cinematic"],
                           camera_move=CameraMove(direction="out"),
                           beat_style=BeatStyle(look="cinema", position="top"))
    draft = apply_kit(story, kit)
    result = render_from_plan(draft, Path(draft.audio_path), get_template(), caps,
                             tmp_path / "signature.mp4", height=240)
    assert _frame_count(caps, result.video_path) == original.total_frames
    sync._assert_in_sync(caps, result.video_path, list(sync.CLAPS))
    assert "CURRENT WORDS" in result.ass_path.read_text().replace(r"\N", " ")
    assert all(word in result.srt_path.read_text() for word in ("Start", "ideas", "count", "now"))
