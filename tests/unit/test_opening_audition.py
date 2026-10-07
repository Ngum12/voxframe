"""Opening treatments preserve the source edit and apply only reviewed snapshots."""
from pathlib import Path

import pytest

from tests.unit import test_api_editing as fixtures
from tests.unit import test_complete_audition as complete
from tests.unit.test_visual_placement import recording
from voxframe.config.captions import CAPTION_PRESETS
from voxframe.config.visuals import VisualBeat
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.editing import EditError
from voxframe.plan.opening_audition import OpeningChoice, audition, controls
from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan, Shot
from voxframe.plan.shorts import revision, tokens

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library
render = complete.render


def story(fps: float = 30) -> ScenePlan:
    original = recording(fps)
    text = "Why does this fail? Here are 3 fixes. Keep going now."
    words = tuple(PlanWord(text=word, start=.2 + i * .5, end=.5 + i * .5)
                  for i, word in enumerate(text.split()))
    return original.model_copy(update={"audio_mix": AudioMix(music_db=-7, music_arc="rise"),
        "music_path": "music.wav", "music_credit": "Artist credit",
        "scenes": (original.scenes[0].model_copy(update={"text": text, "words": words,
            "caption_emphasis": (2, 6), "caption_treatment": CAPTION_PRESETS["karaoke"]}),)})


def choice(plan: ScenePlan, **changes) -> OpeningChoice:  # type: ignore[no-untyped-def]
    return OpeningChoice(revision=revision(plan), last_word=3, **changes)


def source_at(plan: ScenePlan, frame: int) -> tuple:
    scene = next(s for s in plan.scenes if s.start_frame <= frame < s.end_frame)
    source = scene.audio_start
    if source is None:
        source = max(0, scene.start_frame / plan.fps - plan.card_seconds_before(scene.index))
    offset = (frame - scene.start_frame) / plan.fps
    footage = scene.footage_start + offset if scene.footage_start is not None else None
    return source + offset, footage, scene.asset, scene.shot


@pytest.mark.parametrize("fps", [24, 29.97, 30, 60])
@pytest.mark.parametrize("look", ["authority", "energy", "cinema"])
def test_each_opening_preserves_word_clocks_all_source_frames_music_and_tail(fps, look):  # type: ignore[no-untyped-def]
    original = story(fps)
    before = original.model_dump_json()
    draft, opening = audition(original, choice(original, look=look))
    assert draft.total_frames == original.total_frames and draft.audio_duration == original.audio_duration
    assert [t.value for t in tokens(draft)] == [t.value for t in tokens(original)]
    assert draft.music_path == original.music_path and draft.music_credit == original.music_credit
    assert draft.audio_mix == original.audio_mix
    for frame in range(original.total_frames):
        assert source_at(draft, frame)[:2] == pytest.approx(source_at(original, frame)[:2])
        assert source_at(draft, frame)[2:] == source_at(original, frame)[2:]
    after = [s for s in draft.scenes if s.start_frame / fps >= opening["end"]]
    assert after and all(s.caption_treatment == CAPTION_PRESETS["karaoke"] for s in after)
    beats = [s.visual_beat for s in draft.scenes if s.visual_beat]
    assert " ".join(beat.text for beat in beats) == "Why does this fail?"
    assert len(beats) == (2 if look == "energy" else 1)
    assert original.model_dump_json() == before
    assert ScenePlan.model_validate_json(draft.model_dump_json()) == draft


def test_controls_choose_real_sentence_and_refuse_late_words_and_long_stories():
    original = story()
    data = controls(original)
    assert data["default_last_word"] == 3 and data["endings"][3]["quote"] == "Why does this fail?"
    assert all(ending["end"] <= 6.5 for ending in data["endings"])
    with pytest.raises(EditError, match="first six seconds"):
        audition(original, OpeningChoice(revision=revision(original), last_word=50))
    long = original.model_copy(update={"total_frames": 90 * 30})
    assert not controls(long)["eligible"] and not controls(long)["endings"]
    untimed = original.model_copy(update={"scenes": (original.scenes[0].model_copy(update={"words": ()}),)})
    assert not controls(untimed)["endings"]


def test_caption_corrections_emphasis_and_pinned_body_remain_intact():
    original = story()
    first = original.scenes[0].model_copy(update={"end_frame": 120, "words": original.scenes[0].words[:8],
        "text": "Why does this fail? Here are 3 fixes.", "caption_text": "Why does this work? Here are 3 fixes."})
    last = original.scenes[0].model_copy(update={"index": 1, "start_frame": 120,
        "audio_start": 4, "footage_start": 4, "text": "Keep going now.",
        "words": original.scenes[0].words[8:], "caption_emphasis": (1,),
        "visual_beat": VisualBeat(text="MY ENDING", source="user")})
    original = original.model_copy(update={"scenes": (first, last)})
    result, _ = audition(original, choice(original, look="energy", match_captions=False))
    assert [t.value for t in tokens(result)] == [t.value for t in tokens(original)]
    assert result.scenes[-1].model_dump(exclude={"index"}) == last.model_dump(exclude={"index"})
    assert any(s.is_corrected and "work?" in s.caption_text for s in result.scenes)
    emphasized = [word.text for scene in result.scenes
                  for index, word in enumerate(scene.caption_words())
                  if index in scene.caption_emphasis]
    assert "this" in emphasized


def test_pinned_opening_requires_explicit_replacement_and_new_beats_stay_pinned():
    original = story()
    original = original.model_copy(update={"scenes": (original.scenes[0].model_copy(update={
        "visual_beat": VisualBeat(text="Pinned opening", source="user")}),)})
    assert controls(original)["endings"][3]["pinned"]
    with pytest.raises(EditError, match="pinned"):
        audition(original, choice(original))
    result, _ = audition(original, choice(original, replace_pinned=True))
    opening = [s for s in result.scenes if s.visual_beat and s.visual_beat.text == "Why does this fail?"]
    assert len(opening) == 1 and opening[0].visual_beat.source == "user"


def test_picture_or_speaker_changes_only_opening_and_picture_requires_existing_media():
    original = story()
    result, window = audition(original, choice(original, shot="picture", asset_scene=0))
    assert any(s.shot == Shot.PICTURE and s.asset_source == "user" for s in result.scenes)
    assert all(s.shot == Shot.SPEAKER for s in result.scenes if s.start_frame / result.fps >= window["end"])
    for changes in ({"shot": "picture"}, {"shot": "picture", "asset_scene": 99}, {"asset_scene": 0}):
        with pytest.raises(EditError):
            audition(original, choice(original, **changes))
    with pytest.raises(EditError, match="speaker footage"):
        audition(original.model_copy(update={"footage": None}), choice(original, shot="speaker"))


def test_intro_cards_and_discontinuous_source_clocks_are_kept():
    original = story()
    card = PlannedScene(index=0, start_frame=0, end_frame=30, card_kind="title", card_text="Title")
    scene = original.scenes[0].model_copy(update={"index": 1, "start_frame": 30, "end_frame": 240,
        "audio_start": 10, "footage_start": 10,
        "words": tuple(w.model_copy(update={"start": w.start + 1, "end": w.end + 1}) for w in original.scenes[0].words)})
    titled = original.model_copy(update={"scenes": (card, scene), "total_frames": 240})
    draft, _ = audition(titled, choice(titled))
    assert draft.scenes[0] == card
    for frame in range(30, 240):
        assert source_at(draft, frame)[:2] == pytest.approx(source_at(titled, frame)[:2])


@pytest.fixture
def saved(context, finished_job):  # type: ignore[no-untyped-def]
    folder = context.store.job_directory(finished_job)
    voice, music = folder / "voice.wav", folder / "music.wav"
    voice.write_bytes(b"voice")
    music.write_bytes(b"music")
    original = story().model_copy(update={"audio_path": str(voice), "music_path": str(music), "footage": None,
        "scenes": tuple(s.model_copy(update={"asset": None, "shot": Shot.PICTURE}) for s in story().scenes)})
    path = context.store.artifact_path(finished_job, "plan")
    original.save(path)
    return original, path


def test_api_previews_three_distinct_complete_snapshots_and_saves_exact_winner_with_undo(client, context, finished_job, saved, render):  # type: ignore[no-untyped-def]
    original, path = saved
    endpoint = f"/api/jobs/{finished_job}/opening-auditions"
    data = client.get(endpoint).json()
    previews = []
    for look in ("authority", "energy", "cinema"):
        response = client.post(endpoint + "/preview", json=choice(original, look=look).model_dump())
        assert response.status_code == 200, response.text
        previews.append(response.json())
        assert previews[-1]["has_music"] and previews[-1]["opening"]["quote"] == "Why does this fail?"
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
def test_stale_opening_cannot_overwrite_new_edits_or_changed_media(client, context, finished_job, saved, render, change):  # type: ignore[no-untyped-def]
    original, path = saved
    endpoint = f"/api/jobs/{finished_job}/opening-auditions"
    preview = client.post(endpoint + "/preview", json=choice(original).model_dump()).json()
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
    url = f"/api/jobs/{finished_job}/opening-auditions/preview"
    assert client.post(url, json=choice(original).model_copy(update={"revision": "f" * 24}).model_dump()).status_code == 409
    assert client.post(url, json=choice(original).model_copy(update={"last_word": 99}).model_dump()).status_code == 422
    outside = original.model_copy(update={"audio_path": "/outside/private.wav"})
    outside.save(path)
    response = client.post(url, json=choice(outside).model_dump())
    assert response.status_code == 403 and "/outside" not in response.text
    assert not render


def test_opening_routes_require_authentication(client, finished_job, saved):  # type: ignore[no-untyped-def]
    client.headers.clear()
    url = f"/api/jobs/{finished_job}/opening-auditions"
    assert client.get(url).status_code == 401
    assert client.post(url + "/preview", json=choice(saved[0]).model_dump()).status_code == 401


def test_word_punch_uses_one_beat_when_no_safe_internal_frame_boundary_exists():
    original = story()
    words = list(original.scenes[0].words)
    words[1] = words[1].model_copy(update={"end": 1.204})
    words[2] = words[2].model_copy(update={"start": 1.205})
    original = original.model_copy(update={"scenes": (original.scenes[0].model_copy(
        update={"words": tuple(words)}),)})
    draft, _ = audition(original, choice(original, look="energy"))
    beats = [scene.visual_beat for scene in draft.scenes if scene.visual_beat]
    assert len(beats) == 1 and beats[0].text == "Why does this fail?"
    assert [token.value for token in tokens(draft)] == [token.value for token in tokens(original)]
