"""Paired treatments preserve source clocks, the middle and one reviewed edit."""
from pathlib import Path

import pytest

from tests.unit import test_opening_audition as opening
from voxframe.config.visuals import VisualBeat
from voxframe.plan.bookend_audition import BookendChoice, audition, controls
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import PlannedScene, ScenePlan, Shot
from voxframe.plan.shorts import revision, tokens

client = opening.client
context = opening.context
finished_job = opening.finished_job
library = opening.library
render = opening.render
saved = opening.saved


def choice(plan, **changes):  # type: ignore[no-untyped-def]
    return BookendChoice(revision=revision(plan), last_word=3, first_word=8, **changes)


@pytest.mark.parametrize("fps", [24, 29.97, 30, 60])
@pytest.mark.parametrize("first", ["authority", "energy", "cinema"])
@pytest.mark.parametrize("last", ["authority", "energy", "cinema"])
def test_all_pairs_preserve_every_source_frame_words_middle_and_sound(fps, first, last):  # type: ignore[no-untyped-def]
    original = opening.story(fps)
    beat = VisualBeat(text="The middle stays mine", source="director")
    original = original.model_copy(update={"scenes": (original.scenes[0].model_copy(update={"visual_beat": beat}),)})
    before = original.model_dump_json()
    draft, start, end = audition(original, choice(original, opening_look=first, closing_look=last))
    assert draft.total_frames == original.total_frames
    assert draft.audio_duration == original.audio_duration
    assert draft.audio_mix == original.audio_mix and draft.score == original.score
    assert draft.music_path == original.music_path and draft.music_credit == original.music_credit
    assert [t.value for t in tokens(draft)] == [t.value for t in tokens(original)]
    for frame in range(original.total_frames):
        assert opening.source_at(draft, frame)[:2] == pytest.approx(opening.source_at(original, frame)[:2])
        assert opening.source_at(draft, frame)[2:] == opening.source_at(original, frame)[2:]
    for kind, text, look in (("opening", "Why does this fail?", first), ("closing", "Keep going now.", last)):
        beats = [s.visual_beat for s in draft.scenes if s.visual_beat and s.visual_beat.kind == kind]
        assert " ".join(b.text for b in beats) == text
        assert all(b.look == look for b in beats)
        assert len(beats) == (2 if look == "energy" else 1)
    middle = [s for s in draft.scenes if s.start_frame / fps >= start["end"] and s.end_frame / fps <= end["start"]]
    assert middle and all(s.visual_beat == beat for s in middle)
    assert all(s.caption_treatment == original.scenes[0].caption_treatment for s in middle)
    assert original.model_dump_json() == before
    assert ScenePlan.model_validate_json(draft.model_dump_json()) == draft


def test_defaults_are_disjoint_and_overlap_is_rejected():
    plan = opening.story()
    data = controls(plan)
    assert (data["default_last_word"], data["default_first_word"]) == (3, 8)
    with pytest.raises(EditError, match="overlap"):
        audition(plan, BookendChoice(revision=revision(plan), last_word=6, first_word=6))
    with pytest.raises(EditError, match="story changed"):
        audition(plan, choice(plan).model_copy(update={"revision": "f" * 24}))
    single = plan.model_copy(update={"scenes": (plan.scenes[0].model_copy(update={"text": "One", "words": plan.scenes[0].words[:1]}),)})
    assert controls(single)["default_last_word"] is None


@pytest.mark.parametrize("allow_opening,allow_closing", [(False, False), (True, False), (False, True)])
def test_each_original_pinned_end_needs_its_own_permission(allow_opening, allow_closing):  # type: ignore[no-untyped-def]
    plan = opening.story()
    plan = plan.model_copy(update={"scenes": (plan.scenes[0].model_copy(update={"visual_beat": VisualBeat(text="Pinned", source="user")}),)})
    with pytest.raises(EditError, match="pinned"):
        audition(plan, choice(plan, replace_opening=allow_opening, replace_closing=allow_closing))
    draft, start, end = audition(plan, choice(plan, replace_opening=True, replace_closing=True))
    middle = [s for s in draft.scenes if s.start_frame / plan.fps >= start["end"] and s.end_frame / plan.fps <= end["start"]]
    assert all(s.visual_beat.text == "Pinned" for s in middle)


def test_speaker_choices_require_footage_and_caption_matching_can_be_disabled():
    plan = opening.story()
    draft, _, _ = audition(plan, choice(plan, match_captions=False))
    assert all(s.caption_treatment == plan.scenes[0].caption_treatment for s in draft.scenes)
    plan = plan.model_copy(update={"footage": None, "scenes": (plan.scenes[0].model_copy(update={"shot": Shot.PICTURE, "footage_start": None}),)})
    for key in ("opening_shot", "closing_shot"):
        with pytest.raises(EditError, match="speaker footage"):
            audition(plan, choice(plan, **{key: "speaker"}))


def test_api_three_snapshots_apply_both_ends_as_one_exact_undoable_edit(client, context, finished_job, saved, render):  # type: ignore[no-untyped-def]
    original, path = saved
    endpoint = f"/api/jobs/{finished_job}/bookend-auditions"
    assert client.get(endpoint).json()["default_first_word"] == 8
    previews = []
    for look in ("authority", "energy", "cinema"):
        response = client.post(endpoint + "/preview", json=choice(original, opening_look=look, closing_look=look).model_dump())
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["has_music"] and preview["opening"]["quote"] == "Why does this fail?"
        assert preview["closing"]["quote"] == "Keep going now."
        assert not {"plan", "stamps", "engine"}.intersection(preview)
        assert client.get(preview["url"], headers={"range": "bytes=0-2"}).status_code == 206
        previews.append(preview)
    assert len({p["preview_id"] for p in previews}) == 3
    assert ScenePlan.load(path) == original
    expected = render[1][0]
    response = client.put(f"/api/jobs/{finished_job}/complete-auditions", json={"revision": revision(original), "preview_id": previews[1]["preview_id"]})
    assert response.status_code == 200 and response.json()["pending_edits"] == 1
    assert ScenePlan.load(path) == expected
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert ScenePlan.load(path) == original


@pytest.mark.parametrize("change", ["revision", "music", "voice"])
def test_stale_pair_cannot_overwrite_new_edits_or_media(client, finished_job, saved, render, change):  # type: ignore[no-untyped-def]
    original, path = saved
    preview = client.post(f"/api/jobs/{finished_job}/bookend-auditions/preview", json=choice(original).model_dump()).json()
    if change == "revision":
        original.model_copy(update={"music_credit": "New credit"}).save(path)
    else:
        Path(original.music_path if change == "music" else original.audio_path).write_bytes(b"changed")
    before = path.read_bytes()
    response = client.put(f"/api/jobs/{finished_job}/complete-auditions", json={"revision": revision(original), "preview_id": preview["preview_id"]})
    assert response.status_code == 409 and path.read_bytes() == before


def test_api_invalid_pair_sandbox_and_authentication(client, finished_job, saved, render):  # type: ignore[no-untyped-def]
    original, path = saved
    url = f"/api/jobs/{finished_job}/bookend-auditions"
    assert client.post(url + "/preview", json=choice(original).model_copy(update={"revision": "f" * 24}).model_dump()).status_code == 409
    assert client.post(url + "/preview", json=choice(original).model_copy(update={"last_word": 8}).model_dump()).status_code == 422
    assert client.post(url + "/preview", json=choice(original).model_copy(update={"first_word": 99}).model_dump()).status_code == 422
    outside = original.model_copy(update={"audio_path": "/outside/private.wav"})
    outside.save(path)
    response = client.post(url + "/preview", json=choice(outside).model_dump())
    assert response.status_code == 403 and "/outside" not in response.text and not render
    client.headers.clear()
    assert client.get(url).status_code == 401
    assert client.post(url + "/preview", json=choice(outside).model_dump()).status_code == 401


def test_outro_cards_discontinuous_clocks_and_corrected_quotes_survive():
    original = opening.story()
    scene = original.scenes[0].model_copy(update={"audio_start": 10, "footage_start": 10,
        "caption_text": "Why does this work? Here are 3 fixes. Keep growing now."})
    card = PlannedScene(index=1, start_frame=210, end_frame=240, card_kind="chapter", card_text="Thank you")
    original = original.model_copy(update={"scenes": (scene, card), "total_frames": 240})
    draft, start, end = audition(original, choice(original, opening_look="energy", closing_look="cinema"))
    assert start["quote"] == "Why does this work?" and end["quote"] == "Keep growing now."
    assert draft.scenes[-1].model_dump(exclude={"index"}) == card.model_dump(exclude={"index"})
    assert [t.value for t in tokens(draft)] == [t.value for t in tokens(original)]
    for frame in range(210):
        assert opening.source_at(draft, frame)[:2] == pytest.approx(opening.source_at(original, frame)[:2])


def test_unknown_final_speech_needs_timings_before_a_pair_can_be_chosen():
    plan = opening.story()
    first = plan.scenes[0].model_copy(update={"end_frame": 180})
    tail = PlannedScene(index=1, start_frame=180, end_frame=210, text="Unknown final speech")
    plan = plan.model_copy(update={"scenes": (first, tail)})
    data = controls(plan)
    assert data["eligible"] and data["default_last_word"] is None
    with pytest.raises(EditError, match="final six seconds"):
        audition(plan, choice(plan))
