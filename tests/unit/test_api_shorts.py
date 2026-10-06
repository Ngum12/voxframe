"""Short drafts never save edits; applying a selection uses authenticated history."""
import pytest
from fastapi.testclient import TestClient

from tests.unit import test_api_editing as fixtures
from tests.unit.test_shorts import story
from voxframe.api.app import ApiContext
from voxframe.plan.scene_plan import ScenePlan

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


@pytest.mark.parametrize("look", ["authority", "energy", "cinema"])
def test_storyboard_is_read_only_and_save_is_one_undoable_directed_edit(
    client: TestClient, context: ApiContext, finished_job: str, look: str,
) -> None:
    from voxframe.plan.storyboard import audition, storyboard

    path = context.store.artifact_path(finished_job, "plan")
    original = story()
    original.save(path)
    endpoint = f"/api/jobs/{finished_job}/shorts"
    choice = {"revision": client.get(endpoint).json()["revision"], "first_word": 0,
              "last_word": 16, "vertical": True, "look": look, "match_captions": True}
    expected = audition(original, 0, 16, look=look, match_captions=True)
    response = client.post(endpoint + "/storyboard", json=choice)
    assert response.status_code == 200, response.text
    assert response.json() == storyboard(expected)
    assert ScenePlan.load(path) == original
    result = client.put(endpoint, json=choice)
    assert result.status_code == 200, result.text
    assert result.json()["pending_edits"] == 1
    assert ScenePlan.load(path) == expected
    assert client.post(endpoint + "/storyboard", json=choice).status_code == 409
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert ScenePlan.load(path) == original


def test_word_selection_saved_and_undoable_with_stale_revision_guard(client: TestClient, context: ApiContext, finished_job: str) -> None:
    path = context.store.artifact_path(finished_job, "plan")
    assert path is not None
    original = story()
    original.save(path)
    endpoint = f"/api/jobs/{finished_job}/shorts"
    data = client.get(endpoint).json()
    candidate = data["suggestions"][0]
    choice = {"revision": data["revision"], "first_word": candidate["first_word"],
              "last_word": candidate["last_word"], "vertical": True}
    assert client.put(endpoint, json={**choice, "last_word": -1}).status_code == 422
    assert ScenePlan.load(path) == original
    result = client.put(endpoint, json=choice)
    assert result.status_code == 200, result.text
    shorter = ScenePlan.load(path)
    assert shorter.total_frames < original.total_frames and shorter.aspect.value == "9:16"
    assert result.json()["pending_edits"] == 1
    assert client.put(endpoint, json=choice).status_code == 409
    assert client.post(endpoint + "/preview", json=choice).status_code == 409
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert ScenePlan.load(path) == original
    assert client.post(f"/api/jobs/{finished_job}/plan/redo").status_code == 200
    assert ScenePlan.load(path) == shorter


def test_preview_files_require_authentication_and_cannot_escape_job(client: TestClient, context: ApiContext, finished_job: str) -> None:
    key = "a" * 24
    directory = context.store.job_directory(finished_job) / "short-previews"
    directory.mkdir()
    (directory / f"{key}.mp4").write_bytes(b"test video content")
    endpoint = f"/api/jobs/{finished_job}/short-previews/"
    result = client.get(endpoint + key, headers={"range": "bytes=0-3"})
    assert result.status_code == 206 and result.content == b"test"
    assert client.get(endpoint + "bad-key").status_code == 404
    assert client.get(f"/api/jobs/unknown/short-previews/{key}").status_code == 404
    client.headers.pop("x-voxframe-token")
    assert client.get(endpoint + key).status_code == 401
