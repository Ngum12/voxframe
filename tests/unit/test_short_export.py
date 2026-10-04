"""Saved export choices fit safe composition without changing source editing."""
from pathlib import Path

import pytest

from tests.unit.test_visual_director import passage
from voxframe.config.captions import CAPTION_PRESETS
from voxframe.config.short_export import PRESETS, SafeArea, ShortExport, safe_caption_style
from voxframe.config.style import CaptionPosition, CaptionStyle, get_template
from voxframe.plan.audio_mix import Destination
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.short_export import configure
from voxframe.plan.visual_director import direct
from voxframe.render.captions.ass import build_ass
from voxframe.render.captions.short_progress import append_short_progress
from voxframe.render.compose.from_plan import _scenes_for_captions


@pytest.mark.parametrize("platform", list(PRESETS))
def test_preset_preserves_source_edit_and_mix_levels(platform: str) -> None:
    original = direct(passage(), "energy")
    original = original.model_copy(update={"audio_mix": original.audio_mix.model_copy(
        update={"voice_db": -2, "music_db": -6, "voice_polish": False})})
    export = PRESETS[platform]["export"]
    plan = configure(original, export)
    assert plan.scenes == original.scenes and plan.total_frames == original.total_frames
    assert plan.aspect.value == "9:16"
    assert plan.audio_mix.voice_db == -2 and plan.audio_mix.music_db == -6
    assert not plan.audio_mix.voice_polish
    assert plan.audio_mix.destination == (Destination.WHATSAPP if platform == "whatsapp" else
                                         Destination.YOUTUBE if platform == "youtube" else Destination.SOCIAL)
    assert ScenePlan.model_validate_json(plan.model_dump_json()) == plan
    assert configure(plan, None).short_export is None
    assert configure(plan, None).audio_mix == plan.audio_mix


def test_duration_and_untrusted_guide_values_rejected() -> None:
    with pytest.raises(EditError):
        configure(passage().model_copy(update={"total_frames": 3000}), ShortExport())
    for settings in ({"height": 720}, {"accent": r"{\pos(0,0)}"},
                     {"safe_area": {"bottom": .9}}, {"platform": "unknown"}):
        with pytest.raises(ValueError):
            ShortExport.model_validate(settings)


@pytest.mark.parametrize("look", list(CAPTION_PRESETS))
@pytest.mark.parametrize("position", list(CaptionPosition))
def test_all_caption_looks_use_safe_styles(look: str, position: CaptionPosition) -> None:
    plan = passage()
    treatment = CAPTION_PRESETS[look].model_copy(update={"position": position})
    export = ShortExport(safe_area=SafeArea(top=.25, bottom=.35, left=.25, right=.25))
    effective = safe_caption_style(treatment.apply(CaptionStyle()), export.safe_area,
                                   progress=export.progress)
    assert effective.margin_horizontal_ratio >= .26
    if position != CaptionPosition.CENTER:
        assert effective.margin_vertical_ratio >= (.285 if position == CaptionPosition.TOP else .36)
    ass = build_ass(_scenes_for_captions(plan), CaptionStyle(), 270, 480, 30,
                    scene_treatments={0: treatment}, safe_area=export.safe_area, progress=True)
    assert "Style: Caption0," in ass and "Dialogue: " in ass
    assert "Style: Caption0,Inter," in ass


def test_progress_uses_last_frame_and_does_not_enter_spoken_captions(tmp_path: Path) -> None:
    plan = configure(passage(), ShortExport())
    path = tmp_path / "captions.ass"
    before = build_ass(_scenes_for_captions(plan), get_template().captions, 270, 480, 30)
    path.write_text(before)
    append_short_progress(path, plan, 270, 480)
    result = path.read_text()
    assert "Dialogue: -1,0:00:00.00" in result
    assert f"\\t(0,{round((plan.total_frames - 1) / plan.fps * 1000)},\\clip" in result
    assert "Style: ShortProgress," in result
    assert result.count("Dialogue: 0,") == before.count("Dialogue: 0,")
    no_bar = configure(plan, plan.short_export.model_copy(update={"progress": False}))
    path.write_text(before)
    append_short_progress(path, no_bar, 270, 480)
    assert path.read_text() == before
