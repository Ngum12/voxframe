"""Direction adds picture beats while preserving the recording's clocks and words."""
from pathlib import Path

import pytest

from tests.unit.test_shorts import story
from voxframe.config.captions import CAPTION_PRESETS
from voxframe.config.style import get_template
from voxframe.config.visuals import VisualBeat
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import Footage, PlanAsset, ScenePlan, Shot
from voxframe.plan.shorts import build_short, tokens
from voxframe.plan.visual_director import direct, set_visual
from voxframe.render.captions.visual_beats import append_visual_beats
from voxframe.render.compose.footage import crop_window


def passage() -> ScenePlan:
    return build_short(story(), 0, 16)


@pytest.mark.parametrize("look", ["authority", "energy", "cinema"])
def test_director_quotes_real_words_and_preserves_every_source_clock(look: str) -> None:
    original = passage()
    result = direct(original, look)
    assert result.total_frames == original.total_frames
    assert len(result.scenes) > len(original.scenes)
    assert [t.value.text for t in tokens(result)] == [t.value.text for t in tokens(original)]
    for before, after in zip(tokens(original), tokens(result), strict=True):
        assert after.value.start == pytest.approx(before.value.start, abs=1 / result.fps)
        assert after.value.end == pytest.approx(before.value.end, abs=1 / result.fps)
    for scene in result.scenes:
        assert scene.audio_start == pytest.approx(original.scenes[0].audio_start + scene.start_frame / result.fps)
        assert scene.footage_start == pytest.approx(original.scenes[0].footage_start + scene.start_frame / result.fps)
        if scene.visual_beat.text:
            assert scene.visual_beat.text in " ".join(w.text for w in scene.caption_words())
    assert result.scenes[0].visual_beat.kind == "opening"
    assert result.scenes[-1].visual_beat.kind == "closing"
    assert result.scenes[0].visual_beat.zoom == result.scenes[-1].visual_beat.zoom == 1
    assert ScenePlan.model_validate_json(result.model_dump_json()) == result
    assert original.scenes[0].visual_beat is None


def test_manual_beat_and_shot_survive_redirection() -> None:
    original = passage().model_copy(update={"footage": Footage(
        path="video.mp4", width=640, height=360, fps=25, duration=72)})
    directed = direct(original, "energy")
    pinned = set_visual(directed, 1, VisualBeat(text="My own headline", zoom=1.23, position="center"))
    selected = pinned.scenes[1].model_copy(update={"shot": Shot.SPEAKER, "shot_source": "user"})
    pinned = pinned.model_copy(update={"scenes": (pinned.scenes[0], selected, *pinned.scenes[2:])})
    result = direct(pinned, "cinema")
    chosen = next(s for s in result.scenes if s.start_frame == selected.start_frame)
    assert chosen.end_frame == selected.end_frame
    assert chosen.visual_beat == selected.visual_beat and chosen.visual_beat.source == "user"
    assert chosen.shot == selected.shot and chosen.shot_source == "user"
    assert set_visual(pinned, 1, None).scenes[1].visual_beat is None


def test_corrected_words_and_caption_choices_preserved_unless_requested() -> None:
    plan = passage()
    scene = plan.scenes[0].model_copy(update={"caption_text": plan.scenes[0].text.replace("fail?", "work?"),
        "caption_treatment": CAPTION_PRESETS["cinema"], "caption_emphasis": (8,)})
    plan = plan.model_copy(update={"scenes": (scene,)})
    result = direct(plan, "energy")
    assert [t.value.text for t in tokens(result)] == [t.value.text for t in tokens(plan)]
    assert all(s.caption_treatment == scene.caption_treatment for s in result.scenes)
    assert sum(len(s.caption_emphasis) for s in result.scenes) == 1
    matched = direct(plan, "energy", match_captions=True)
    assert matched.caption_treatment == CAPTION_PRESETS["electric"]
    assert all(s.caption_treatment is None for s in matched.scenes)


def test_cutaways_return_to_speaker_and_stay_within_budget() -> None:
    plan = passage()
    footage = Footage(path="video.mp4", width=640, height=360, fps=25, duration=72)
    asset = PlanAsset(id="picture", path="image.jpg", width=640, height=360,
                     license_name="CC0", license_author="test", license_source="test")
    plan = plan.model_copy(update={"footage": footage,
        "scenes": (plan.scenes[0].model_copy(update={"asset": asset, "match_score": .9}),)})
    result = direct(plan, "energy")
    assert result.scenes[0].shot == result.scenes[-1].shot == Shot.SPEAKER
    pictures = [s for s in result.scenes if s.shot == Shot.PICTURE]
    assert pictures
    assert sum(s.duration_frames for s in pictures) <= result.total_frames * .4
    assert all(a.shot != Shot.PICTURE or b.shot != Shot.PICTURE
               for a, b in zip(result.scenes, result.scenes[1:], strict=False))


def test_bounds_and_escaped_graphics_with_no_spoken_subtitle_changes(tmp_path: Path) -> None:
    with pytest.raises(EditError):
        direct(story(), "energy")
    with pytest.raises(EditError):
        direct(passage(), "unknown")
    with pytest.raises(ValueError):
        VisualBeat(zoom=1.26)
    plan = set_visual(passage(), 0, VisualBeat(text=r"Hello {\\pos(0,0)} world", position="top"))
    path = tmp_path / "captions.ass"
    path.write_text("spoken captions unchanged\n")
    append_visual_beats(path, plan, get_template(), 270, 480)
    rendered = path.read_text()
    assert rendered.startswith("spoken captions unchanged\n")
    assert "Dialogue: 1," in rendered and "Dialogue: 2," in rendered
    assert r"\{\\\\pos(0,0)\}" in rendered
    assert rendered.count("Dialogue: 2,") == 1


def test_zoom_uses_the_same_center_and_larger_source_scale() -> None:
    footage = Footage(path="source.mp4", width=640, height=360, fps=25, duration=72)
    normal = crop_window(footage, 270, 480)
    zoomed = crop_window(footage, 270, 480, zoom=1.2)
    assert zoomed != normal


def test_changing_look_replaces_automatic_cadence_without_accumulating_cuts() -> None:
    plan = passage()
    energy = direct(plan, "energy")
    cinema = direct(energy, "cinema")
    assert len(cinema.scenes) == len(direct(plan, "cinema").scenes)
    assert len(cinema.scenes) < len(energy.scenes)
    assert direct(cinema, "energy").scenes == energy.scenes


def test_auto_text_is_suppressed_when_tracked_face_and_captions_fill_frame(tmp_path: Path) -> None:
    from voxframe.plan.scene_plan import TrackPoint

    original = passage().model_copy(update={"footage": Footage(
        path="video.mp4", width=640, height=360, fps=25, duration=72,
        track=(TrackPoint(t=0, x=.5, y=.5, h=.95),))})
    original = original.model_copy(update={"scenes": (original.scenes[0].model_copy(
        update={"shot": Shot.SPEAKER}),)})
    plan = set_visual(original, 0, VisualBeat(text="Keep the face clear"))
    path = tmp_path / "auto.ass"
    path.write_text("captions\n")
    append_visual_beats(path, plan, get_template(), 270, 480)
    assert path.read_text() == "captions\n"
    manual = set_visual(original, 0, VisualBeat(text="Review manual placement", position="top"))
    append_visual_beats(path, manual, get_template(), 270, 480)
    assert "Dialogue: 2," in path.read_text()


def test_later_caption_change_does_not_render_an_outdated_automatic_quote(tmp_path: Path) -> None:
    plan = direct(passage(), "energy")
    first = plan.scenes[0].model_copy(update={"caption_text": "A corrected opening"})
    plan = plan.model_copy(update={"scenes": (first, *plan.scenes[1:])})
    path = tmp_path / "changed.ass"
    path.write_text("captions\n")
    append_visual_beats(path, plan, get_template(), 270, 480)
    assert "OPENING" not in path.read_text()


def test_zoom_invalidates_picture_cache_but_text_edits_reuse_it() -> None:
    from voxframe.render.compose.scenes import _footage_signature

    plan = passage().model_copy(update={"footage": Footage(
        path="video.mp4", width=640, height=360, fps=25, duration=72)})
    scene = plan.scenes[0].model_copy(update={"shot": Shot.SPEAKER})
    first = scene.model_copy(update={"visual_beat": VisualBeat(text="First", zoom=1.2)})
    second = scene.model_copy(update={"visual_beat": VisualBeat(text="Edited", zoom=1.2)})
    zoomed = scene.model_copy(update={"visual_beat": VisualBeat(text="Edited", zoom=1.22)})
    key = _footage_signature(plan, first, first.duration_frames)
    assert key == _footage_signature(plan, second, second.duration_frames)
    assert key != _footage_signature(plan, zoomed, zoomed.duration_frames)
