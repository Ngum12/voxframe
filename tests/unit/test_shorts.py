"""Passages quote the transcript and keep source clocks through earlier edits."""
from __future__ import annotations

import pytest

from tests.unit.test_pacing import source
from voxframe.config.captions import CAPTION_PRESETS
from voxframe.config.settings import AspectRatio
from voxframe.plan.editing import EditError
from voxframe.plan.pacing import apply_cuts, suggest_cuts
from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan
from voxframe.plan.shorts import build_short, controls, revision, suggestions, tokens


def story(fps: float = 30) -> ScenePlan:
    passages = ("Why does this fail? Here are 3 ways to fix the problem. Start with the simplest solution.",
                "Comment éviter cette erreur? Voici 2 étapes pour avancer. La première étape vous donne une base solide.",
                "A common myth holds us back. Try a different approach today. The result is a clearer and simpler explanation.")
    scenes = []
    for i, text in enumerate(passages):
        words = text.split()
        scenes.append(PlannedScene(index=i, start_frame=round(i * 24 * fps),
            end_frame=round((i + 1) * 24 * fps), text=text, footage_start=round(i * 24 * fps) / fps,
            words=tuple(PlanWord(text=w, start=i * 24 + j, end=i * 24 + j + .7)
                        for j, w in enumerate(words))))
    return ScenePlan(audio_path="source.wav", audio_sha256="a" * 64,
                     audio_duration=72, fps=fps, total_frames=round(72 * fps), scenes=tuple(scenes))


def test_three_distinct_candidates_quote_actual_words_and_endings() -> None:
    plan = story()
    result = suggestions(plan)
    assert len(result) == 3
    words = tokens(plan)
    for candidate in result:
        chosen = words[candidate["first_word"]:candidate["last_word"] + 1]
        assert candidate["text"] == " ".join(t.value.text for t in chosen)
        assert candidate["opening"] in candidate["text"]
        assert candidate["ending"] in candidate["text"]
        assert 3 <= candidate["seconds"] <= 60
        assert chosen[-1].value.text.endswith((".", "?", "!"))
        assert candidate["reasons"]
        built = build_short(plan, candidate["first_word"], candidate["last_word"])
        assert built.total_frames / built.fps == pytest.approx(candidate["seconds"])
    assert any("question" in " ".join(c["reasons"]) for c in result)


@pytest.mark.parametrize("fps", [24, 29.97, 30, 60])
def test_late_words_rebase_without_losing_source_positions(fps: float) -> None:
    plan = story(fps)
    first = next(i for i, token in enumerate(tokens(plan)) if token.value.start == 26)
    last = first + 8
    cut = build_short(plan, first, last)
    assert cut.aspect == AspectRatio.VERTICAL
    assert cut.scenes[0].audio_start == cut.scenes[0].footage_start
    assert cut.scenes[0].audio_start > 25
    assert cut.scenes[0].words[0].start < .2
    assert ScenePlan.model_validate_json(cut.model_dump_json()) == cut
    assert revision(cut) != revision(plan)
    assert plan.scenes[1].words[0].start == 24


def test_second_stage_keeps_first_stage_source_map() -> None:
    original = source()
    original = original.model_copy(update={"scenes": (original.scenes[0].model_copy(update={
        "words": (*original.scenes[0].words, PlanWord(text="ending", start=6.4, end=6.7))}),)})
    paced = apply_cuts(original, (suggest_cuts(original)[0]["id"],))
    cut = build_short(paced, 0, 2)
    assert len(cut.scenes) == 2
    assert cut.scenes[1].audio_start >= 4.3
    assert cut.scenes[1].audio_start == cut.scenes[1].footage_start
    assert cut.scenes[0].transition_after.kind == "cut"


def test_corrected_captions_and_deliberate_looks_survive_partial_selection() -> None:
    plan = story()
    scene = plan.scenes[0].model_copy(update={"caption_text": plan.scenes[0].text.replace("fail?", "work?"),
        "caption_treatment": CAPTION_PRESETS["electric"], "caption_emphasis": (8,)})
    plan = plan.model_copy(update={"scenes": (scene, *plan.scenes[1:])})
    cut = build_short(plan, 3, 12, vertical=False)
    assert cut.aspect == plan.aspect
    assert "work?" in cut.scenes[0].caption_text
    assert [w.text for w in cut.scenes[0].caption_words()] == [t.value.text for t in tokens(plan)[3:13]]
    assert cut.scenes[0].caption_treatment == scene.caption_treatment
    assert cut.scenes[0].caption_emphasis == (5,)
    assert cut.scenes[0].text != cut.scenes[0].caption_text


def test_title_and_chapter_cards_do_not_shift_source_audio() -> None:
    plan = story()
    scenes = []
    cursor = 0
    for original in plan.scenes:
        card = PlannedScene(index=len(scenes), start_frame=cursor, end_frame=cursor + 60,
                            card_kind="chapter", card_text="Chapter")
        scenes.append(card)
        cursor += 60
        shift = cursor / plan.fps - original.start_frame / plan.fps
        scenes.append(original.model_copy(update={"index": len(scenes), "start_frame": cursor,
            "end_frame": cursor + original.duration_frames, "words": tuple(
                w.model_copy(update={"start": w.start + shift, "end": w.end + shift})
                for w in original.words)}))
        cursor += original.duration_frames
    titled = ScenePlan.model_validate({**plan.model_dump(), "total_frames": cursor, "scenes": scenes})
    for candidate in suggestions(titled):
        chosen = tokens(titled)[candidate["first_word"]:candidate["last_word"] + 1]
        assert len({t.group for t in chosen}) == 1
        built = build_short(titled, candidate["first_word"], candidate["last_word"])
        assert candidate["seconds"] == pytest.approx(built.total_frames / built.fps)
    first = next(i for i, t in enumerate(tokens(titled)) if t.value.start == 28)
    cut = build_short(titled, first, first + 6)
    assert not any(s.is_card for s in cut.scenes)
    assert cut.scenes[0].audio_start == pytest.approx(24)
    assert cut.scenes[0].footage_start == pytest.approx(24)


@pytest.mark.parametrize("first,last", [(-1, 5), (5, 2), (0, 999), (0, 0), (0, 55)])
def test_invalid_or_out_of_length_selections_leave_original_intact(first: int, last: int) -> None:
    plan = story()
    before = plan.model_dump_json()
    with pytest.raises(EditError):
        build_short(plan, first, last)
    assert plan.model_dump_json() == before


def test_missing_word_timings_have_no_fabricated_suggestions() -> None:
    plan = story()
    untimed = plan.model_copy(update={"scenes": tuple(s.model_copy(update={"words": ()}) for s in plan.scenes)})
    assert controls(untimed)["words"] == [] and not suggestions(untimed)


def test_unpunctuated_ending_is_flagged_for_review() -> None:
    result = suggestions(source())
    assert result and any("no sentence punctuation" in reason for reason in result[0]["reasons"])


def test_audio_and_footage_with_different_highlight_origins_stay_independent() -> None:
    plan = story()
    plan = plan.model_copy(update={"scenes": tuple(s.model_copy(update={
        "audio_start": s.start_frame / plan.fps,
        "footage_start": s.start_frame / plan.fps + 100}) for s in plan.scenes)})
    cut = build_short(plan, 22, 30)
    for scene in cut.scenes:
        assert scene.footage_start - scene.audio_start == pytest.approx(100)


def test_punctuation_only_words_do_not_crash_suggestion_ranking() -> None:
    plan = story()
    scene = plan.scenes[0]
    words = (scene.words[0].model_copy(update={"text": "..."}), *scene.words[1:])
    plan = plan.model_copy(update={"scenes": (scene.model_copy(update={"words": words}), *plan.scenes[1:])})
    assert suggestions(plan)
