"""Story edits preserve source clocks and refuse duplicate speech."""
import pytest
from fastapi.testclient import TestClient

from tests.unit import test_api_editing as fixtures
from tests.unit.test_pacing import source
from tests.unit.test_shorts import story
from voxframe.api.app import ApiContext
from voxframe.config.visuals import VisualBeat
from voxframe.plan.editing import EditError
from voxframe.plan.pacing import apply_cuts, suggest_cuts
from voxframe.plan.scene_plan import PlanWord, ScenePlan
from voxframe.plan.shorts import tokens
from voxframe.plan.story_composer import StoryBlock, compose, controls

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


@pytest.mark.parametrize("fps", [24, 29.97, 30, 60])
def test_reordered_passages_preserve_original_clocks_roles_and_words(fps: float) -> None:
    original = story(fps)
    before = original.model_dump_json()
    blocks = [StoryBlock(role="hook", first_word=20, last_word=24),
              StoryBlock(role="ending", first_word=0, last_word=3)]
    result = compose(original, blocks)
    assert result.scenes[0].audio_start > result.scenes[1].audio_start
    assert result.scenes[0].audio_start == result.scenes[0].footage_start
    assert [s.story_role for s in result.scenes] == ["hook", "ending"]
    assert [s.story_block for s in result.scenes] == [0, 1]
    assert all(s.transition_after.kind == "cut" for s in result.scenes)
    assert [t.value.text for t in tokens(result)] == [t.value.text for block in blocks
        for t in tokens(original)[block.first_word:block.last_word + 1]]
    assert result.scenes[-1].end_frame == result.total_frames
    assert result.scenes[1].start_frame == result.scenes[0].end_frame
    assert ScenePlan.model_validate_json(result.model_dump_json()) == result
    assert original.model_dump_json() == before
    saved = controls(result)["proposal"]
    assert [block["role"] for block in saved] == ["hook", "ending"]
    assert saved[0]["first_word"] == 0 and saved[1]["first_word"] == 5


@pytest.mark.parametrize("blocks", [[], [StoryBlock(first_word=0, last_word=3)] * 2,
    [StoryBlock(first_word=3, last_word=1)], [StoryBlock(first_word=0, last_word=999)],
    [StoryBlock(first_word=0, last_word=0)], [StoryBlock(first_word=0, last_word=60)]])
def test_invalid_overlapping_or_out_of_duration_sequences_are_rejected(blocks: list[StoryBlock]) -> None:
    with pytest.raises(EditError):
        compose(story(), blocks)


def test_adjacent_passage_padding_is_not_repeated() -> None:
    plan = story()
    scene = plan.scenes[0].model_copy(update={"words": tuple(
        PlanWord(text=w.text, start=i * .5, end=i * .5 + .4)
        for i, w in enumerate(plan.scenes[0].words))})
    plan = plan.model_copy(update={"scenes": (scene, *plan.scenes[1:])})
    result = compose(plan, [StoryBlock(first_word=0, last_word=3),
                            StoryBlock(first_word=4, last_word=9)])
    left, right = result.scenes
    assert left.audio_start + left.duration_frames / plan.fps <= right.audio_start + 1e-8
    assert left.words[-1].end <= left.end_frame / plan.fps
    assert right.words[0].start >= right.start_frame / plan.fps


def test_stale_director_quotes_removed_but_user_text_and_zoom_survive() -> None:
    plan = story()
    for origin in ("director", "user"):
        scene = plan.scenes[0].model_copy(update={"visual_beat": VisualBeat(
            text="Start with the simplest solution", zoom=1.1, source=origin)})
        result = compose(plan.model_copy(update={"scenes": (scene, *plan.scenes[1:])}),
                         [StoryBlock(first_word=0, last_word=3)])
        beat = result.scenes[0].visual_beat
        assert beat.zoom == 1.1
        assert bool(beat.text) == (origin == "user")


def test_controls_quote_source_and_do_not_claim_semantic_roles() -> None:
    plan = story()
    data = controls(plan)
    assert data["proposal"][0]["role"] == "hook"
    assert data["proposal"][-1]["role"] == "ending"
    assert "transcript order" in data["note"]
    for passage in data["passages"]:
        assert passage["text"] == " ".join(t.value.text for t in
            tokens(plan)[passage["first_word"]:passage["last_word"] + 1])
        assert "context_after" in passage and "warnings" in passage


def test_reordering_after_pause_removal_keeps_caption_emphasis_and_source_offsets() -> None:
    original = source()
    scene = original.scenes[0].model_copy(update={
        "words": (*original.scenes[0].words, PlanWord(text="ending", start=6.4, end=6.7)),
        "text": "First last ending", "caption_emphasis": (1,)})
    original = original.model_copy(update={"scenes": (scene,)})
    paced = apply_cuts(original, (suggest_cuts(original)[0]["id"],))
    result = compose(paced, [StoryBlock(first_word=1, last_word=2),
                             StoryBlock(first_word=0, last_word=0)])
    assert result.scenes[0].audio_start > 4
    assert result.scenes[0].audio_start == result.scenes[0].footage_start
    assert result.scenes[0].caption_emphasis == (0,)
    assert [w.text for w in result.scenes[0].words] == ["last", "ending"]


def test_actual_overlapping_word_timings_are_refused() -> None:
    plan = story()
    words = list(plan.scenes[0].words)
    words[4] = words[4].model_copy(update={"start": words[3].end - .1})
    scene = plan.scenes[0].model_copy(update={"words": tuple(words)})
    with pytest.raises(EditError, match="overlap in time"):
        compose(plan.model_copy(update={"scenes": (scene, *plan.scenes[1:])}),
                [StoryBlock(first_word=0, last_word=3), StoryBlock(first_word=4, last_word=9)])


def test_save_is_one_undoable_edit_and_stale_requests_are_rejected(
    client: TestClient, context: ApiContext, finished_job: str,
) -> None:
    path = context.store.artifact_path(finished_job, "plan")
    original = story()
    original.save(path)
    endpoint = f"/api/jobs/{finished_job}/story"
    data = client.get(endpoint).json()
    choice = {"revision": data["revision"], "blocks": list(reversed(data["proposal"])),
              "vertical": True}
    result = client.put(endpoint, json=choice)
    assert result.status_code == 200, result.text
    assert result.json()["pending_edits"] == 1
    saved = ScenePlan.load(path)
    assert saved.scenes[0].audio_start > saved.scenes[-1].audio_start
    assert client.put(endpoint, json=choice).status_code == 409
    assert client.post(endpoint + "/preview", json=choice).status_code == 409
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert ScenePlan.load(path) == original
    assert client.post(f"/api/jobs/{finished_job}/plan/redo").status_code == 200
    assert ScenePlan.load(path) == saved


def test_invalid_save_does_not_change_project(client: TestClient, context: ApiContext,
                                             finished_job: str) -> None:
    path = context.store.artifact_path(finished_job, "plan")
    original = story()
    original.save(path)
    endpoint = f"/api/jobs/{finished_job}/story"
    data = client.get(endpoint).json()
    result = client.put(endpoint, json={"revision": data["revision"],
        "blocks": [data["proposal"][0]] * 2})
    assert result.status_code == 422
    assert ScenePlan.load(path) == original
