"""Closing treatments preserve the full source edit and reviewed winner identity."""
from pathlib import Path

import pytest

from tests.unit import test_opening_audition as opening
from voxframe.config.captions import CAPTION_PRESETS
from voxframe.config.visuals import VisualBeat
from voxframe.plan.closing_audition import ClosingChoice, audition, controls
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import PlannedScene, ScenePlan, Shot
from voxframe.plan.shorts import revision, tokens

client = opening.client
context = opening.context
finished_job = opening.finished_job
library = opening.library
render = opening.render
saved = opening.saved


def choice(plan: ScenePlan, **changes) -> ClosingChoice:  # type: ignore[no-untyped-def]
    return ClosingChoice(revision=revision(plan), first_word=8, **changes)


@pytest.mark.parametrize("fps", [24, 29.97, 30, 60])
@pytest.mark.parametrize("look", ["authority", "energy", "cinema"])
def test_closing_preserves_words_all_source_frames_music_and_earlier_caption_choices(fps, look):  # type: ignore[no-untyped-def]
    original = opening.story(fps)
    before = original.model_dump_json()
    draft, closing = audition(original, choice(original, look=look))
    assert draft.total_frames == original.total_frames and draft.audio_duration == original.audio_duration
    assert [token.value for token in tokens(draft)] == [token.value for token in tokens(original)]
    assert draft.music_path == original.music_path and draft.music_credit == original.music_credit
    assert draft.audio_mix == original.audio_mix and draft.score == original.score
    for frame in range(original.total_frames):
        assert opening.source_at(draft, frame)[:2] == pytest.approx(opening.source_at(original, frame)[:2])
        assert opening.source_at(draft, frame)[2:] == opening.source_at(original, frame)[2:]
    earlier = [scene for scene in draft.scenes if scene.end_frame / fps <= closing["start"]]
    assert earlier and all(scene.caption_treatment == CAPTION_PRESETS["karaoke"] for scene in earlier)
    beats = [scene.visual_beat for scene in draft.scenes if scene.visual_beat]
    assert " ".join(beat.text for beat in beats) == "Keep going now."
    assert len(beats) == (2 if look == "energy" else 1)
    assert all(beat.kind == "closing" for beat in beats)
    assert original.model_dump_json() == before
    assert ScenePlan.model_validate_json(draft.model_dump_json()) == draft


def test_controls_choose_final_sentence_and_refuse_earlier_words_long_stories_or_unknown_tail():
    original = opening.story()
    data = controls(original)
    assert data["default_first_word"] == 8
    assert all(ending["quote"].endswith("now.") for ending in data["starts"])
    with pytest.raises(EditError, match="final six seconds"):
        audition(original, ClosingChoice(revision=revision(original), first_word=99))
    long = original.model_copy(update={"total_frames": 90 * 30})
    assert not controls(long)["eligible"] and not controls(long)["starts"]
    untimed = original.model_copy(update={"scenes": (original.scenes[0].model_copy(update={"words": ()}),)})
    assert not controls(untimed)["starts"]
    scene = original.scenes[0].model_copy(update={"end_frame": 180})
    unknown_tail = original.model_copy(update={"scenes": (scene, PlannedScene(index=1,
        start_frame=180, end_frame=210, text="Untranscribed closing words"))})
    assert not controls(unknown_tail)["starts"]


def test_corrections_emphasis_and_pinned_opening_remain_intact():
    original = opening.story()
    first = original.scenes[0].model_copy(update={"end_frame": 120, "words": original.scenes[0].words[:8],
        "text": "Why does this fail? Here are 3 fixes.", "audio_start": 0,
        "visual_beat": VisualBeat(text="MY OPENING", source="user")})
    last = original.scenes[0].model_copy(update={"index": 1, "start_frame": 120,
        "audio_start": 4, "footage_start": 4, "text": "Keep going now.",
        "caption_text": "Keep growing now.", "words": original.scenes[0].words[8:],
        "caption_emphasis": (1,)})
    original = original.model_copy(update={"scenes": (first, last)})
    result, _ = audition(original, choice(original, look="energy", match_captions=False))
    assert [token.value for token in tokens(result)] == [token.value for token in tokens(original)]
    assert result.scenes[0] == first
    assert any(scene.is_corrected and "growing" in scene.caption_text for scene in result.scenes)
    assert "growing" in [word.text for scene in result.scenes
        for index, word in enumerate(scene.caption_words()) if index in scene.caption_emphasis]


def test_pinned_closing_requires_explicit_replacement_and_new_beats_stay_pinned():
    original = opening.story()
    original = original.model_copy(update={"scenes": (original.scenes[0].model_copy(update={
        "visual_beat": VisualBeat(text="Pinned ending", source="user")}),)})
    assert all(ending["pinned"] for ending in controls(original)["starts"])
    with pytest.raises(EditError, match="pinned"):
        audition(original, choice(original))
    result, _ = audition(original, choice(original, replace_pinned=True))
    closing = [scene for scene in result.scenes if scene.visual_beat and scene.visual_beat.text == "Keep going now."]
    assert len(closing) == 1 and closing[0].visual_beat.source == "user"


def test_picture_choice_changes_only_closing_and_speaker_requires_footage():
    original = opening.story()
    result, closing = audition(original, choice(original, shot="picture", asset_scene=0))
    assert any(scene.shot == Shot.PICTURE and scene.asset_source == "user" for scene in result.scenes)
    assert all(scene.shot == Shot.SPEAKER for scene in result.scenes if scene.end_frame / result.fps <= closing["start"])
    for changes in ({"shot": "picture"}, {"shot": "picture", "asset_scene": 99}, {"asset_scene": 0}):
        with pytest.raises(EditError):
            audition(original, choice(original, **changes))
    with pytest.raises(EditError, match="speaker footage"):
        audition(original.model_copy(update={"footage": None}), choice(original, shot="speaker"))


def test_outro_cards_and_discontinuous_clocks_are_kept_and_selection_cannot_cross_cards():
    original = opening.story()
    scene = original.scenes[0].model_copy(update={"audio_start": 10, "footage_start": 10})
    card = PlannedScene(index=1, start_frame=210, end_frame=240, card_kind="chapter", card_text="Thank you")
    titled = original.model_copy(update={"scenes": (scene, card), "total_frames": 240})
    draft, _ = audition(titled, choice(titled))
    assert draft.scenes[-1].model_dump(exclude={"index"}) == card.model_dump(exclude={"index"})
    for frame in range(210):
        assert opening.source_at(draft, frame)[:2] == pytest.approx(opening.source_at(titled, frame)[:2])
    first = scene.model_copy(update={"end_frame": 120, "words": scene.words[:8]})
    middle = card.model_copy(update={"start_frame": 120, "end_frame": 150})
    last = scene.model_copy(update={"index": 2, "start_frame": 150, "end_frame": 240,
        "text": "Keep going now.", "words": tuple(word.model_copy(update={
            "start": word.start + 1, "end": word.end + 1}) for word in scene.words[8:])})
    divided = original.model_copy(update={"scenes": (first, middle, last), "total_frames": 240})
    assert all(ending["first_word"] >= 8 for ending in controls(divided)["starts"])


def test_last_word_punch_uses_one_beat_if_no_whole_frame_fits_between_words():
    original = opening.story()
    words = list(original.scenes[0].words)
    words[9] = words[9].model_copy(update={"end": 5.204})
    words[10] = words[10].model_copy(update={"start": 5.205})
    original = original.model_copy(update={"scenes": (original.scenes[0].model_copy(update={"words": tuple(words)}),)})
    draft, _ = audition(original, choice(original, look="energy"))
    beats = [scene.visual_beat for scene in draft.scenes if scene.visual_beat]
    assert len(beats) == 1 and beats[0].text == "Keep going now."
    assert [token.value for token in tokens(draft)] == [token.value for token in tokens(original)]


def test_api_three_complete_snapshots_save_exact_reviewed_winner_with_undo(client, context, finished_job, saved, render):  # type: ignore[no-untyped-def]
    original, path = saved
    endpoint = f"/api/jobs/{finished_job}/closing-auditions"
    data = client.get(endpoint).json()
    previews = []
    for look in ("authority", "energy", "cinema"):
        response = client.post(endpoint + "/preview", json=choice(original, look=look).model_dump())
        assert response.status_code == 200, response.text
        previews.append(response.json())
        assert previews[-1]["has_music"] and previews[-1]["closing"]["quote"] == "Keep going now."
        assert not {"plan", "stamps", "engine"}.intersection(previews[-1])
        assert client.get(previews[-1]["url"], headers={"range": "bytes=0-2"}).status_code == 206
    assert len({preview["preview_id"] for preview in previews}) == 3
    assert ScenePlan.load(path) == original
    winner = {"revision": data["revision"], "preview_id": previews[1]["preview_id"]}
    response = client.put(f"/api/jobs/{finished_job}/complete-auditions", json=winner)
    assert response.status_code == 200 and response.json()["pending_edits"] == 1
    assert ScenePlan.load(path) == render[1][0]
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert ScenePlan.load(path) == original


@pytest.mark.parametrize("change", ["revision", "music", "voice"])
def test_stale_closing_cannot_overwrite_new_edits_or_changed_media(client, context, finished_job, saved, render, change):  # type: ignore[no-untyped-def]
    original, path = saved
    preview = client.post(f"/api/jobs/{finished_job}/closing-auditions/preview", json=choice(original).model_dump()).json()
    if change == "revision":
        original.model_copy(update={"music_credit": "New credit"}).save(path)
    else:
        Path(original.music_path if change == "music" else original.audio_path).write_bytes(b"changed media")
    before = path.read_bytes()
    response = client.put(f"/api/jobs/{finished_job}/complete-auditions", json={
        "revision": revision(original), "preview_id": preview["preview_id"]})
    assert response.status_code == 409 and path.read_bytes() == before


def test_preview_rejects_stale_controls_invalid_bounds_and_sandbox_escape(client, context, finished_job, saved, render):  # type: ignore[no-untyped-def]
    original, path = saved
    url = f"/api/jobs/{finished_job}/closing-auditions/preview"
    assert client.post(url, json=choice(original).model_copy(update={"revision": "f" * 24}).model_dump()).status_code == 409
    assert client.post(url, json=choice(original).model_copy(update={"first_word": 99}).model_dump()).status_code == 422
    outside = original.model_copy(update={"audio_path": "/outside/private.wav"})
    outside.save(path)
    response = client.post(url, json=choice(outside).model_dump())
    assert response.status_code == 403 and "/outside" not in response.text
    assert not render


def test_closing_routes_require_authentication(client, finished_job, saved):  # type: ignore[no-untyped-def]
    client.headers.clear()
    url = f"/api/jobs/{finished_job}/closing-auditions"
    assert client.get(url).status_code == 401
    assert client.post(url + "/preview", json=choice(saved[0]).model_dump()).status_code == 401


def test_an_automatic_opening_quote_survives_splitting_its_scene_for_the_closing():
    original = opening.story()
    beat = VisualBeat(text="Why does this fail?", kind="opening", source="director")
    original = original.model_copy(update={"scenes": (original.scenes[0].model_copy(
        update={"visual_beat": beat}),)})
    draft, closing = audition(original, choice(original, look="energy"))
    earlier = [scene for scene in draft.scenes if scene.end_frame / draft.fps <= closing["start"]]
    assert earlier and all(scene.visual_beat == beat for scene in earlier)
    assert any(scene.visual_beat and scene.visual_beat.kind == "closing" for scene in draft.scenes)
