"""Authenticated saved-plan review is read-only and refreshes after an edit."""
from tests.unit import test_api_editing as fixtures
from tests.unit.test_pacing import source
from voxframe.plan.scene_plan import ScenePlan

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


def test_report_is_read_only_and_tracks_the_saved_revision(client, context, finished_job):
    path = context.store.artifact_path(finished_job, "plan")
    plan = source()
    plan.save(path)
    endpoint = f"/api/jobs/{finished_job}/finish-review"
    original = path.read_bytes()
    first = client.get(endpoint)
    assert first.status_code == 200
    assert first.json()["issues"] == []
    assert path.read_bytes() == original
    scene = plan.scenes[0].model_copy(update={"words": ()})
    plan.model_copy(update={"scenes": (scene,)}).save(path)
    second = client.get(endpoint).json()
    assert second["revision"] != first.json()["revision"]
    assert second["issues"][0]["action"] == "captions"
    assert ScenePlan.load(path).scenes[0].words == ()
    client.headers.pop("x-voxframe-token")
    assert client.get(endpoint).status_code == 401
