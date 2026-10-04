"""Pause selections persist through normal plan history and reject stale edits."""
from fastapi.testclient import TestClient

from tests.unit import test_api_editing as fixtures
from tests.unit.test_pacing import source
from voxframe.api.app import ApiContext
from voxframe.plan.scene_plan import ScenePlan

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


def test_cut_save_undo_redo_and_stale_selection(client: TestClient, context: ApiContext, finished_job: str) -> None:
    path = context.store.artifact_path(finished_job, "plan")
    assert path is not None
    original = source()
    original.save(path)
    endpoint = f"/api/jobs/{finished_job}/pacing"
    suggestions = client.get(endpoint)
    assert suggestions.status_code == 200
    ids = [suggestions.json()["cuts"][0]["id"]]
    response = client.put(endpoint, json={"cuts": ids})
    assert response.status_code == 200, response.text
    edited = ScenePlan.load(path)
    assert edited.total_frames < original.total_frames
    assert response.json()["pending_edits"] == 1
    assert client.put(endpoint, json={"cuts": ids}).status_code == 422
    assert ScenePlan.load(path) == edited
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert ScenePlan.load(path) == original
    assert client.post(f"/api/jobs/{finished_job}/plan/redo").status_code == 200
    assert ScenePlan.load(path) == edited
    assert client.put(endpoint, json={"cuts": []}).status_code == 422
    client.headers.pop("x-voxframe-token")
    assert client.put(endpoint, json={"cuts": ids}).status_code == 401
