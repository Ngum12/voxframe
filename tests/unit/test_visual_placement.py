"""Word-boundary placement keeps the story clock and protects explicit choices."""
import pytest
from fastapi.testclient import TestClient

from tests.unit import test_api_editing as fixtures
from tests.unit.test_pacing import source
from voxframe.api.app import ApiContext
from voxframe.config.visuals import VisualBeat
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import Footage, PlanAsset, PlanWord, ScenePlan, Shot
from voxframe.plan.shorts import tokens
from voxframe.plan.visual_director import direct
from voxframe.plan.visual_placement import Placement, controls, place

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


def recording(fps: float = 30) -> ScenePlan:
    plan = source(fps)
    asset = PlanAsset(id="visual", path="photo.png", width=640, height=360,
                     license_name="CC0", license_author="Artist", license_source="local")
    return plan.model_copy(update={"footage": Footage(path="recording.mp4", width=640,
        height=360, fps=30, duration=7), "scenes": (plan.scenes[0].model_copy(update={
            "asset": asset, "shot": Shot.SPEAKER}),)})


@pytest.mark.parametrize("fps", [24, 29.97, 30, 60])
def test_partial_picture_and_text_keeps_frames_words_emphasis_and_both_clocks(fps: float) -> None:
    original = recording(fps)
    before = original.model_dump_json()
    result = place(original, [Placement(first_word=1, last_word=1, shot="picture",
        asset_scene=0, beat=VisualBeat(text="MY POINT", position="top"))])
    assert result.total_frames == original.total_frames
    assert result.audio_duration == original.audio_duration
    assert len(result.scenes) == 3
    selected = next(s for s in result.scenes if s.shot_source == "user")
    assert selected.shot == Shot.PICTURE and selected.asset == original.scenes[0].asset
    assert selected.caption_emphasis == (0,)
    assert selected.visual_beat.text == "MY POINT" and selected.visual_beat.source == "user"
    assert result.scenes[0].shot == result.scenes[-1].shot == Shot.SPEAKER
    for scene in result.scenes:
        assert scene.audio_start == scene.footage_start == scene.start_frame / fps
    for before_word, after in zip(tokens(original), tokens(result), strict=True):
        assert after.value == before_word.value
    assert original.model_dump_json() == before
    assert ScenePlan.model_validate_json(result.model_dump_json()) == result
    redirected = direct(result, "energy")
    pinned = [s for s in redirected.scenes if s.visual_beat and s.visual_beat.source == "user"]
    assert len(pinned) == 1 and pinned[0] == selected


@pytest.mark.parametrize("edit", [Placement(first_word=2, last_word=2, shot="speaker"),
    Placement(first_word=1, last_word=0, shot="speaker"),
    Placement(first_word=0, last_word=0),
    Placement(first_word=0, last_word=0, shot="picture"),
    Placement(first_word=0, last_word=0, shot="picture", asset_scene=99),
    Placement(first_word=0, last_word=0, shot="speaker", asset_scene=0)])
def test_invalid_choices_are_refused(edit: Placement) -> None:
    with pytest.raises(EditError):
        place(recording(), [edit])


def test_audio_only_picture_allowed_but_speaker_is_refused() -> None:
    original = recording()
    plan = original.model_copy(update={"footage": None, "scenes": (
        original.scenes[0].model_copy(update={"shot": Shot.PICTURE, "footage_start": None}),)})
    result = place(plan, [Placement(first_word=0, last_word=0, shot="picture", asset_scene=0)])
    assert any(s.asset_source == "user" for s in result.scenes)
    with pytest.raises(EditError, match="speaker footage"):
        place(plan, [Placement(first_word=0, last_word=0, shot="speaker")])


def test_overlap_refused_and_adjacent_padding_shared_without_word_changes() -> None:
    plan = recording()
    words = tuple(PlanWord(text=str(i), start=i * .5, end=i * .5 + .4) for i in range(10))
    plan = plan.model_copy(update={"scenes": (plan.scenes[0].model_copy(update={
        "words": words, "text": " ".join(w.text for w in words)}),)})
    edits = [Placement(first_word=0, last_word=3, shot="speaker"),
             Placement(first_word=4, last_word=9, shot="picture", asset_scene=0)]
    result = place(plan, edits)
    assert [t.value for t in tokens(result)] == [t.value for t in tokens(plan)]
    assert result.total_frames == plan.total_frames
    with pytest.raises(EditError, match="overlap"):
        place(plan, [edits[0], edits[0]])


def test_cards_and_corrected_captions_survive() -> None:
    from voxframe.plan.scene_plan import PlannedScene

    plan = recording()
    scene = plan.scenes[0].model_copy(update={"caption_text": "Hello world", "index": 1,
        "start_frame": 60, "end_frame": 270, "words": tuple(w.model_copy(update={
            "start": w.start + 2, "end": w.end + 2}) for w in plan.scenes[0].words)})
    card = PlannedScene(index=0, start_frame=0, end_frame=60,
                        card_kind="title", card_text="Title")
    plan = ScenePlan.model_validate({**plan.model_dump(), "scenes": (card, scene),
                                    "total_frames": 270})
    result = place(plan, [Placement(first_word=1, last_word=1, beat=VisualBeat(text=""))])
    assert result.scenes[0] == card
    assert [t.value for t in tokens(result)] == [t.value for t in tokens(plan)]
    selected = next(s for s in result.scenes if s.visual_beat)
    assert selected.audio_start == pytest.approx(selected.start_frame / plan.fps - 2)
    assert selected.caption_emphasis == (0,)


def test_a_placement_cannot_cover_speech_on_both_sides_of_a_card() -> None:
    from voxframe.plan.scene_plan import PlannedScene

    plan = recording()
    original = plan.scenes[0]
    first = original.model_copy(update={"end_frame": 90, "audio_start": 0, "text": "First",
        "words": original.words[:1], "caption_emphasis": ()})
    card = PlannedScene(index=1, start_frame=90, end_frame=120,
                        card_kind="chapter", card_text="Next")
    last = original.model_copy(update={"index": 2, "start_frame": 120, "end_frame": 240,
        "text": "last", "words": (original.words[1].model_copy(update={"start": 5.5,
            "end": 5.8}),), "audio_start": 3, "footage_start": 3, "caption_emphasis": (0,)})
    plan = ScenePlan.model_validate({**plan.model_dump(), "scenes": (first, card, last),
                                    "total_frames": 240})
    with pytest.raises(EditError, match="crossing a card"):
        place(plan, [Placement(first_word=0, last_word=1, shot="speaker")])


def test_a_boundary_inside_overlapping_speech_is_refused() -> None:
    plan = recording()
    words = (plan.scenes[0].words[0].model_copy(update={"end": 4.6}), plan.scenes[0].words[1])
    plan = plan.model_copy(update={"scenes": (plan.scenes[0].model_copy(update={"words": words}),)})
    with pytest.raises(EditError, match="overlap at the cut"):
        place(plan, [Placement(first_word=1, last_word=1, shot="speaker")])


def test_untouched_scenes_receive_source_clocks_when_a_neighbour_is_split() -> None:
    original = recording()
    scene = original.scenes[0]
    first = scene.model_copy(update={"end_frame": 90, "text": "First",
        "words": scene.words[:1], "caption_emphasis": ()})
    second = scene.model_copy(update={"index": 1, "start_frame": 90, "text": "last",
        "footage_start": 3, "words": scene.words[1:], "caption_emphasis": (0,)})
    plan = ScenePlan.model_validate({**original.model_dump(), "scenes": (first, second)})
    result = place(plan, [Placement(first_word=1, last_word=1, shot="picture", asset_scene=0)])
    assert result.scenes[0].audio_start == 0
    for s in result.scenes:
        assert s.audio_start == s.footage_start
        assert s.audio_start == pytest.approx(s.start_frame / plan.fps)
    assert result.total_frames == plan.total_frames


def test_existing_blend_at_a_placement_boundary_becomes_a_precise_cut() -> None:
    from voxframe.config.transitions import TransitionTreatment

    original = recording()
    scene = original.scenes[0]
    first = scene.model_copy(update={"end_frame": 90, "text": "First",
        "words": (scene.words[0].model_copy(update={"start": 0, "end": 3}),),
        "transition_after": TransitionTreatment(kind="crossfade"), "caption_emphasis": ()})
    second = scene.model_copy(update={"index": 1, "start_frame": 90, "text": "last",
        "footage_start": 3, "words": scene.words[1:], "caption_emphasis": (0,)})
    plan = ScenePlan.model_validate({**original.model_dump(), "scenes": (first, second)})
    result = place(plan, [Placement(first_word=0, last_word=0, shot="picture", asset_scene=0)])
    assert result.scenes[0].transition_after.kind == "cut"
    assert result.total_frames == plan.total_frames


def test_replacing_a_visual_preserves_hold_and_does_not_claim_an_old_match_score() -> None:
    from voxframe.plan.editing import MOTION_OFF
    from voxframe.plan.scene_plan import MotionKind

    original = recording()
    scene = original.scenes[0]
    first = scene.model_copy(update={"end_frame": 90, "text": "First",
        "words": scene.words[:1], "caption_emphasis": ()})
    old_asset = scene.asset.model_copy(update={"id": "old", "path": "old.png"})
    second = scene.model_copy(update={"index": 1, "start_frame": 90, "text": "last",
        "footage_start": 3, "words": scene.words[1:], "caption_emphasis": (0,),
        "asset": old_asset, "motion": MotionKind.NONE, "motion_reason": MOTION_OFF,
        "match_score": .9, "semantic_score": .85})
    plan = ScenePlan.model_validate({**original.model_dump(), "scenes": (first, second)})
    result = place(plan, [Placement(first_word=1, last_word=1, shot="picture", asset_scene=0)])
    selected = next(s for s in result.scenes if s.shot_source == "user")
    assert selected.motion == MotionKind.NONE and selected.motion_reason == MOTION_OFF
    assert selected.match_score == selected.semantic_score == 0
    assert selected.alternatives[0].id == "old"
    assert selected.asset.license_author == "Artist"


def test_api_preview_is_read_only_and_save_is_one_stale_guarded_history_step(
    client: TestClient, context: ApiContext, finished_job: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = context.store.artifact_path(finished_job, "plan")
    original = recording()
    original.save(path)
    endpoint = f"/api/jobs/{finished_job}/visual-placement"
    data = client.get(endpoint).json()
    assert data["visuals"][0]["credit"] == "Artist"
    assert data["has_speaker"] and data == controls(original)
    choice = {"revision": data["revision"], "placements": [{"first_word": 1,
        "last_word": 1, "shot": "picture", "asset_scene": 0,
        "beat": {"text": "MY POINT", "source": "director"}}]}
    drafts = []

    def preview(draft: ScenePlan, directory):  # type: ignore[no-untyped-def]
        drafts.append(draft)
        return directory / ("a" * 24 + ".mp4")

    monkeypatch.setattr("voxframe.render.compose.short_preview.short_preview", preview)
    response = client.post(endpoint + "/preview", json=choice)
    assert response.status_code == 200, response.text
    assert "MY POINT" in str(response.json()["storyboard"])
    assert ScenePlan.load(path) == original
    result = client.put(endpoint, json=choice)
    assert result.status_code == 200 and result.json()["pending_edits"] == 1
    saved = ScenePlan.load(path)
    assert saved == drafts[0]
    assert client.put(endpoint, json=choice).status_code == 409
    assert client.post(endpoint + "/preview", json=choice).status_code == 409
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert ScenePlan.load(path) == original
    assert client.post(f"/api/jobs/{finished_job}/plan/redo").status_code == 200
    assert ScenePlan.load(path) == saved


def test_placement_thumbnail_shows_the_asset_even_when_scene_shows_speaker(
    client: TestClient, context: ApiContext, finished_job: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = recording()
    folder = context.store.job_directory(finished_job)
    photo = folder / "picture.jpg"
    photo.write_bytes(b"project visual")
    scene = original.scenes[0].model_copy(update={"asset": original.scenes[0].asset.model_copy(
        update={"path": str(photo)})})
    original.model_copy(update={"scenes": (scene,)}).save(
        context.store.artifact_path(finished_job, "plan"))
    seen = []

    def thumbnail(_context, _job, source, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(source)
        return source

    monkeypatch.setattr("voxframe.api.app._thumbnail_for", thumbnail)
    result = client.get(f"/api/jobs/{finished_job}/scenes/0/thumbnail?asset_only=true")
    assert result.status_code == 200 and result.content == b"project visual"
    assert seen == [photo.resolve()]
