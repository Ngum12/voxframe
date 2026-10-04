"""Cuts preserve source clocks, words, choices and validated frame tiling."""
from __future__ import annotations

import pytest

from voxframe.plan.editing import EditError
from voxframe.plan.pacing import apply_cuts, suggest_cuts
from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan


def source(fps: float = 30) -> ScenePlan:
    return ScenePlan(audio_path="voice.wav", audio_sha256="0" * 64,
                     audio_duration=7, fps=fps, total_frames=round(7 * fps), scenes=(
        PlannedScene(index=0, start_frame=0, end_frame=round(7 * fps), text="First last",
                     caption_emphasis=(1,), footage_start=0,
                     words=(PlanWord(text="First", start=1.5, end=1.8),
                            PlanWord(text="last", start=4.5, end=4.8))),))


@pytest.mark.parametrize("fps", [24, 25, 29.97, 30, 60])
def test_cut_moves_words_and_both_source_clocks_together(fps: float) -> None:
    plan = source(fps)
    cut = suggest_cuts(plan)[0]
    changed = apply_cuts(plan, (cut["id"],))
    assert changed.total_frames == plan.total_frames - cut["end_frame"] + cut["start_frame"]
    assert changed.scenes[0].end_frame == changed.scenes[1].start_frame
    assert changed.scenes[1].audio_start == changed.scenes[1].footage_start == cut["end"]
    assert changed.scenes[1].words[0].start == pytest.approx(4.5 - cut["seconds"])
    assert changed.scenes[1].caption_emphasis == (0,)
    assert changed.scenes[0].transition_after.kind == "cut"
    assert not suggest_cuts(changed)
    assert ScenePlan.model_validate_json(changed.model_dump_json()) == changed
    assert plan.scenes[0].words[1].start == 4.5


@pytest.mark.parametrize("ids", [(), ("unknown",), ("0:60:129", "0:60:129")])
def test_stale_or_duplicate_selection_cannot_edit(ids: tuple[str, ...]) -> None:
    with pytest.raises(EditError):
        apply_cuts(source(), ids)


def test_corrected_captions_and_untranscribed_scenes_are_not_guessed() -> None:
    plan = source()
    for changes in ({"caption_text": "My corrected words"}, {"words": ()}):
        assert not suggest_cuts(plan.model_copy(update={"scenes": (
            plan.scenes[0].model_copy(update=changes),)}))


def test_untranscribed_word_inside_gap_prevents_suggestion() -> None:
    plan = source()
    scene = plan.scenes[0]
    overlapping = PlanWord(text="overlap", start=2.5, end=3)
    assert not suggest_cuts(plan.model_copy(update={"scenes": (
        scene.model_copy(update={"words": (*scene.words, overlapping)}),)}))


def test_card_does_not_advance_source_clock() -> None:
    plan = source()
    card = PlannedScene(index=0, start_frame=0, end_frame=60, card_kind="title", card_text="Title")
    scene = plan.scenes[0].model_copy(update={"index": 1, "start_frame": 60, "end_frame": 270,
        "words": tuple(w.model_copy(update={"start": w.start + 2, "end": w.end + 2})
                       for w in plan.scenes[0].words)})
    titled = ScenePlan.model_validate({**plan.model_dump(), "scenes": (card, scene), "total_frames": 270})
    changed = apply_cuts(titled, (suggest_cuts(titled)[0]["id"],))
    assert changed.scenes[0] == card
    assert changed.scenes[1].audio_start == 0
    assert changed.scenes[2].audio_start == changed.scenes[2].footage_start


def test_pause_crossing_scene_boundary_removes_empty_scene_and_preserves_source() -> None:
    plan = source()
    first = plan.scenes[0].model_copy(update={"end_frame": 70, "words": plan.scenes[0].words[:1], "text": "First"})
    empty = PlannedScene(index=1, start_frame=70, end_frame=100, footage_start=70/30)
    last = plan.scenes[0].model_copy(update={"index": 2, "start_frame": 100,
        "footage_start": 100/30, "words": plan.scenes[0].words[1:], "text": "last"})
    divided = ScenePlan.model_validate({**plan.model_dump(), "scenes": (first, empty, last)})
    cut = suggest_cuts(divided)[0]
    changed = apply_cuts(divided, (cut["id"],))
    assert len(changed.scenes) == 2
    assert changed.scenes[1].footage_start == changed.scenes[1].audio_start == cut["end"]
    assert changed.scenes[0].transition_after.kind == "cut"
    assert changed.scenes[1].words[0].start == pytest.approx(4.5 - cut["seconds"])


def test_zero_duration_transcript_words_are_retained() -> None:
    plan = source()
    scene = plan.scenes[0]
    first = scene.words[0].model_copy(update={"start": 0, "end": 0})
    plan = plan.model_copy(update={"scenes": (scene.model_copy(update={
        "words": (first, scene.words[1])}),)})
    edited = apply_cuts(plan, (suggest_cuts(plan)[0]["id"],))
    assert [w.text for s in edited.scenes for w in s.words] == ["First", "last"]


def test_card_and_corrected_caption_boundaries_block_cross_scene_cuts() -> None:
    plan = source()
    first = plan.scenes[0].model_copy(update={"end_frame": 60, "words": plan.scenes[0].words[:1]})
    last = plan.scenes[0].model_copy(update={"index": 2, "start_frame": 90,
                                          "words": plan.scenes[0].words[1:]})
    for middle in (PlannedScene(index=1, start_frame=60, end_frame=90,
                               card_kind="chapter", card_text="Pause"),
                   PlannedScene(index=1, start_frame=60, end_frame=90,
                                text="Correction", caption_text="Corrected")):
        guarded = ScenePlan.model_validate({**plan.model_dump(), "scenes": (first, middle, last)})
        assert not suggest_cuts(guarded)


def test_incomplete_saved_source_map_is_rejected_before_rendering() -> None:
    from pydantic import ValidationError

    plan = source()
    edited = apply_cuts(plan, (suggest_cuts(plan)[0]["id"],))
    payload = edited.model_dump()
    payload["scenes"][1]["audio_start"] = None
    with pytest.raises(ValidationError, match="audio_start"):
        ScenePlan.model_validate(payload)


def test_cut_clamps_subframe_word_edges_to_the_retained_scene() -> None:
    plan = source()
    first = plan.scenes[0].model_copy(update={"end_frame": 60, "words": plan.scenes[0].words[:1]})
    last = plan.scenes[0].model_copy(update={"index": 1, "start_frame": 60,
        "words": (PlanWord(text="edge", start=1.999, end=2.02), *plan.scenes[0].words[1:])})
    divided = ScenePlan.model_validate({**plan.model_dump(), "scenes": (first, last)})
    candidates = suggest_cuts(divided)
    assert candidates
    edited = apply_cuts(divided, (candidates[-1]["id"],))
    assert all(w.start >= s.start_frame / edited.fps and w.end <= s.end_frame / edited.fps
               for s in edited.scenes for w in s.words)
    assert ScenePlan.model_validate_json(edited.model_dump_json()) == edited
