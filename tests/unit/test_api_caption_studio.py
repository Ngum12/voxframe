"""Caption studio routes: validated edits, reversible history and real previews."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.unit import test_api_editing as fixtures
from voxframe.api.app import ApiContext
from voxframe.config.captions import CAPTION_PRESETS
from voxframe.plan.scene_plan import ScenePlan

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


def endpoint(job: str) -> str:
    return f"/api/jobs/{job}/scenes/1/captions"


def test_controls_use_corrected_words(client: TestClient, finished_job: str) -> None:
    client.put(f"/api/jobs/{finished_job}/scenes/1/caption", json={"text": "a flowing river"})
    response = client.get(endpoint(finished_job))
    assert response.status_code == 200
    assert [w["text"] for w in response.json()["words"]] == ["a", "flowing", "river"]
    assert len(response.json()["presets"]) == 8


def test_scene_look_is_saved_and_undoable(client: TestClient, finished_job: str) -> None:
    response = client.put(endpoint(finished_job), json={
        "treatment": CAPTION_PRESETS["electric"].model_dump(mode="json"), "emphasis": [1],
    })
    assert response.status_code == 200
    scene = response.json()["plan"]["scenes"][1]
    assert scene["caption_treatment"]["animation"] == "pop" and scene["caption_emphasis"] == [1]
    assert response.json()["pending_edits"] == 1
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert client.get(endpoint(finished_job)).json()["emphasis"] == []
    assert client.post(f"/api/jobs/{finished_job}/plan/redo").status_code == 200
    assert client.get(endpoint(finished_job)).json()["emphasis"] == [1]


def test_whole_video_look_is_saved_without_rewriting_transcript(client: TestClient, finished_job: str) -> None:
    old = client.get(f"/api/jobs/{finished_job}/plan").json()
    result = client.put(endpoint(finished_job), json={
        "treatment": CAPTION_PRESETS["cinema"].model_dump(mode="json"), "all_scenes": True,
    }).json()["plan"]
    assert result["caption_treatment"]["animation"] == "typewriter"
    assert [s["text"] for s in result["scenes"]] == [s["text"] for s in old["scenes"]]
    assert all(s["caption_treatment"] is None for s in result["scenes"])


@pytest.mark.parametrize("payload", [{"emphasis": [100]}, {"treatment": {"accent": "{\\r}"}}, {"treatment": {"animation": "unknown"}}])
def test_invalid_edits_do_not_change_the_plan(client: TestClient, finished_job: str, payload: dict) -> None:
    before = client.get(f"/api/jobs/{finished_job}/plan").json()
    assert client.put(endpoint(finished_job), json=payload).status_code == 422
    assert client.get(f"/api/jobs/{finished_job}/plan").json() == before


def test_suggestion_failure_does_not_change_the_plan(client: TestClient, finished_job: str) -> None:
    before = client.get(f"/api/jobs/{finished_job}/plan").json()
    assert client.post(endpoint(finished_job) + "/suggest").status_code == 422
    assert client.get(f"/api/jobs/{finished_job}/plan").json() == before


@pytest.mark.needs_ffmpeg
def test_preview_is_a_playable_video_and_does_not_save_draft(client: TestClient, finished_job: str) -> None:
    before = client.get(f"/api/jobs/{finished_job}/plan").json()
    payload = {"treatment": CAPTION_PRESETS["karaoke"].model_dump(mode="json"), "emphasis": [1]}
    response = client.post(endpoint(finished_job) + "/preview", json=payload)
    assert response.status_code == 200, response.text
    video = client.get(response.json()["url"])
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert b"ftyp" in video.content[:32]
    assert client.post(endpoint(finished_job) + "/preview", json=payload).json()["url"] == response.json()["url"]
    assert client.get(f"/api/jobs/{finished_job}/plan").json() == before
    client.headers.clear()
    assert client.get(response.json()["url"]).status_code == 401


def test_global_caption_edit_is_marked_for_every_scene(client: TestClient, context: ApiContext, finished_job: str) -> None:
    from voxframe.api.app import _changed_scenes
    from voxframe.api.plan_history import PlanHistory

    path = context.store.artifact_path(finished_job, "plan")
    PlanHistory(path).begin()
    PlanHistory(path).rendered()
    client.put(endpoint(finished_job), json={
        "treatment": CAPTION_PRESETS["impact"].model_dump(mode="json"), "all_scenes": True,
    })
    changed, pictures = _changed_scenes(path)
    assert set(changed) == {0, 1, 2} and pictures == []
    assert ScenePlan.load(path).caption_treatment is not None
