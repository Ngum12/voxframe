"""Arbitrary section cuts retain source positions and frame-aligned narration."""
import pytest
from fastapi.testclient import TestClient

from tests.unit import test_api_editing as fixtures
from tests.unit.test_pacing import source
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import PlannedScene, ScenePlan
from voxframe.plan.shorts import revision
from voxframe.plan.trim import trim

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


@pytest.mark.parametrize("fps", [24, 25, 29.97, 30, 60])
def test_keep_and_remove_follow_original_clocks_after_repeated_edits(fps):
    plan = source(fps)
    first, last = round(2 * fps), round(4 * fps)
    removed = trim(plan, first, last, "remove")
    assert removed.total_frames == plan.total_frames - last + first
    assert removed.scenes[1].audio_start == removed.scenes[1].footage_start == last / fps
    assert removed.scenes[0].transition_after.kind == "cut"
    assert removed.scenes[1].words[0].start == pytest.approx(4.5 - (last - first) / fps)
    kept = trim(removed, first, removed.total_frames, "keep")
    assert kept.scenes[0].audio_start == last / fps
    assert kept.scenes[0].footage_start == last / fps
    assert ScenePlan.model_validate_json(kept.model_dump_json()) == kept
    assert plan.total_frames == round(7 * fps)


def test_remove_whole_middle_scene_forces_a_jump_cut():
    plan = source()
    scenes = tuple(PlannedScene(index=i, start_frame=i * 70, end_frame=(i + 1) * 70,
                               footage_start=i * 70 / 30) for i in range(3))
    plan = plan.model_copy(update={"scenes": scenes})
    changed = trim(plan, 70, 140, "remove")
    assert changed.total_frames == 140
    assert changed.scenes[1].audio_start == 140 / 30
    assert changed.scenes[0].transition_after.kind == "cut"


def test_card_trim_does_not_remove_recorded_voice_time():
    plan = source()
    card = PlannedScene(index=0, start_frame=0, end_frame=60, card_kind="title", card_text="Intro")
    scene = plan.scenes[0].model_copy(update={"index": 1, "start_frame": 60, "end_frame": 270,
        "words": tuple(word.model_copy(update={"start": word.start + 2, "end": word.end + 2}) for word in plan.scenes[0].words)})
    titled = ScenePlan.model_validate({**plan.model_dump(), "total_frames": 270, "scenes": (card, scene)})
    changed = trim(titled, 0, 60, "remove")
    assert changed.audio_duration == plan.audio_duration
    assert changed.scenes[0].audio_start == 0
    assert changed.scenes[0].words[0].start == 1.5


@pytest.mark.parametrize("start,end,mode", [(-1, 30, "remove"), (0, 0, "keep"), (30, 20, "remove"), (0, 211, "keep"), (0, 210, "remove"), (0, 210, "keep"), (0, 30, "split")])
def test_empty_out_of_bounds_and_noop_edits_are_rejected(start, end, mode):
    with pytest.raises(EditError):
        trim(source(), start, end, mode)


def test_corrected_captions_are_not_silently_discarded():
    plan = source()
    plan = plan.model_copy(update={"scenes": (plan.scenes[0].model_copy(update={"caption_text": "My correction"}),)})
    with pytest.raises(EditError, match="corrected captions"):
        trim(plan, 30, 60, "remove")


def test_save_preview_revision_and_history(client, context, finished_job, monkeypatch, tmp_path):
    plan = source()
    path = context.store.artifact_path(finished_job, "plan")
    plan.save(path)
    choice = {"revision": revision(plan), "start_frame": 60, "end_frame": 120, "mode": "remove"}
    endpoint = f"/api/jobs/{finished_job}/trim"
    output = tmp_path / ("b" * 24 + ".mp4")
    output.write_bytes(b"preview")
    monkeypatch.setattr("voxframe.render.compose.short_preview.short_preview", lambda *args: output)
    assert client.post(endpoint + "/preview", json=choice).status_code == 200
    assert ScenePlan.load(path) == plan
    assert client.put(endpoint, json={**choice, "revision": "0" * 24}).status_code == 409
    assert client.put(endpoint, json={**choice, "start_frame": 60.2}).status_code == 422
    response = client.put(endpoint, json=choice)
    assert response.status_code == 200, response.text
    assert response.json()["pending_edits"] == 1
    expected = ScenePlan.load(path)
    assert expected.total_frames == 150
    assert client.put(endpoint, json=choice).status_code == 409
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert ScenePlan.load(path) == plan
    assert client.post(f"/api/jobs/{finished_job}/plan/redo").status_code == 200
    assert ScenePlan.load(path) == expected
    with TestClient(client.app, base_url=fixtures.LOOPBACK) as anonymous:
        assert anonymous.put(endpoint, json=choice).status_code == 401


def test_partial_untimed_captions_are_not_silently_removed():
    plan = source()
    plan = plan.model_copy(update={"scenes": (plan.scenes[0].model_copy(update={"words": ()}),)})
    with pytest.raises(EditError, match="word timings"):
        trim(plan, 30, 60, "remove")
