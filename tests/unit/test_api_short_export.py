"""Export controls use authenticated full-plan history and revision checks."""
from fastapi.testclient import TestClient

from tests.unit import test_api_editing as fixtures
from tests.unit.test_visual_director import passage
from voxframe.api.app import ApiContext
from voxframe.plan.scene_plan import ScenePlan

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


def test_export_presets_save_and_undo_sound_and_picture(client: TestClient, context: ApiContext, finished_job: str) -> None:
    path = context.store.artifact_path(finished_job, "plan")
    original = passage()
    original.save(path)
    endpoint = f"/api/jobs/{finished_job}/short-export"
    data = client.get(endpoint).json()
    assert set(data["presets"]) == {"youtube", "tiktok", "reels", "whatsapp"}
    edit = {"revision": data["revision"], "settings": data["presets"]["whatsapp"]["export"]}
    bad = {**edit, "settings": {**edit["settings"], "safe_area": {"right": .99}}}
    assert client.put(endpoint, json=bad).status_code == 422
    assert ScenePlan.load(path) == original
    response = client.put(endpoint, json=edit)
    assert response.status_code == 200, response.text
    saved = ScenePlan.load(path)
    assert saved.short_export.platform == "whatsapp" and saved.short_export.height == 1280
    assert saved.audio_mix.destination.value == "whatsapp"
    assert response.json()["pending_edits"] == 1
    assert client.put(endpoint, json=edit).status_code == 409
    assert client.post(endpoint + "/preview", json=edit).status_code == 409
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert ScenePlan.load(path) == original
    assert client.post(f"/api/jobs/{finished_job}/plan/redo").status_code == 200
    assert ScenePlan.load(path) == saved
    revision = client.get(endpoint).json()["revision"]
    assert client.put(endpoint, json={"revision": revision, "settings": None}).status_code == 200
    assert ScenePlan.load(path).short_export is None
    assert ScenePlan.load(path).audio_mix == saved.audio_mix
