"""Authenticated direction edits are reversible and reject stale scene indices."""
from fastapi.testclient import TestClient

from tests.unit import test_api_editing as fixtures
from tests.unit.test_visual_director import passage
from voxframe.api.app import ApiContext
from voxframe.plan.scene_plan import ScenePlan

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


def test_direction_save_pin_stale_guard_undo(client: TestClient, context: ApiContext, finished_job: str) -> None:
    path = context.store.artifact_path(finished_job, "plan")
    original = passage()
    original.save(path)
    endpoint = f"/api/jobs/{finished_job}/direction"
    data = client.get(endpoint).json()
    assert len(data["looks"]) == 3
    choice = {"revision": data["revision"], "look": "energy"}
    result = client.put(endpoint, json=choice)
    assert result.status_code == 200, result.text
    directed = ScenePlan.load(path)
    assert len(directed.scenes) > len(original.scenes)
    assert result.json()["pending_edits"] == 1
    assert client.put(endpoint, json=choice).status_code == 409
    assert client.post(endpoint + "/preview", json=choice).status_code == 409
    revision = client.get(endpoint).json()["revision"]
    beat_url = f"/api/jobs/{finished_job}/scenes/1/visual"
    assert client.put(beat_url, json={"revision": revision, "beat": {"zoom": 5}}).status_code == 422
    response = client.put(beat_url, json={"revision": revision, "beat": {"text": "My headline", "zoom": 1.2}})
    assert response.status_code == 200, response.text
    assert ScenePlan.load(path).scenes[1].visual_beat.source == "user"
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert ScenePlan.load(path) == directed
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert ScenePlan.load(path) == original
