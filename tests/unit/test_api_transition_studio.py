"""Transition routes save validated, reversible choices and authenticated previews."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.unit import test_api_editing as fixtures
from voxframe.api.app import ApiContext
from voxframe.config.transitions import TRANSITION_PRESETS

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


def endpoint(job: str, index: int = 1) -> str:
    return f"/api/jobs/{job}/scenes/{index}/transition"


def test_controls_explain_template_choice(client: TestClient, finished_job: str) -> None:
    result = client.get(endpoint(finished_job)).json()
    assert result["source"] == "template" and len(result["presets"]) == 7
    assert result["max_frames"] == 25
    assert client.get(endpoint(finished_job, 2)).status_code == 422


def test_choice_is_undoable_and_whole_video_reset_restores_template(client: TestClient, finished_job: str) -> None:
    payload = {"treatment": TRANSITION_PRESETS["slide"].model_dump(mode="json")}
    saved = client.put(endpoint(finished_job), json=payload)
    assert saved.status_code == 200
    assert saved.json()["pending_edits"] == 1
    assert client.get(endpoint(finished_job)).json()["source"] == "scene"
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert client.get(endpoint(finished_job)).json()["source"] == "template"
    assert client.post(f"/api/jobs/{finished_job}/plan/redo").status_code == 200
    assert client.get(endpoint(finished_job)).json()["treatment"]["kind"] == "slide"
    client.put(endpoint(finished_job), json={**payload, "all_joins": True})
    assert client.get(endpoint(finished_job, 0)).json()["source"] == "video"
    client.put(endpoint(finished_job), json={"treatment": None, "all_joins": True})
    assert client.get(endpoint(finished_job, 0)).json()["source"] == "template"


@pytest.mark.parametrize("payload", [{"treatment": {"seconds": -1}},
    {"treatment": {"kind": "injection"}}, {"treatment": {"direction": "left;"}}])
def test_invalid_edits_never_change_plan(client: TestClient, finished_job: str, payload: dict) -> None:
    before = client.get(f"/api/jobs/{finished_job}/plan").json()
    assert client.put(endpoint(finished_job), json=payload).status_code == 422
    assert client.get(f"/api/jobs/{finished_job}/plan").json() == before


@pytest.mark.needs_ffmpeg
def test_real_preview_uses_scene_pictures_without_saving_draft(client: TestClient, finished_job: str, library: Path) -> None:
    for path in library.glob("*.jpg"):
        Image.new("RGB", (640, 360), (220, 45, 20)).save(path)
    before = client.get(f"/api/jobs/{finished_job}/plan").json()
    payload = {"treatment": TRANSITION_PRESETS["zoom"].model_dump(mode="json")}
    result = client.post(endpoint(finished_job) + "/preview", json=payload)
    assert result.status_code == 200, result.text
    url = result.json()["url"]
    video = client.get(url)
    assert video.status_code == 200 and b"ftyp" in video.content[:32]
    assert client.post(endpoint(finished_job) + "/preview", json=payload).json()["url"] == url
    assert client.get(f"/api/jobs/{finished_job}/plan").json() == before
    client.headers.clear()
    assert client.get(url).status_code == 401


def test_transition_marks_changes_without_requesting_new_pictures(client: TestClient, finished_job: str, context: ApiContext) -> None:
    from voxframe.api.app import _changed_scenes
    from voxframe.api.plan_history import PlanHistory

    path = context.store.artifact_path(finished_job, "plan")
    history = PlanHistory(path)
    history.begin()
    history.rendered()
    client.put(endpoint(finished_job), json={"treatment": TRANSITION_PRESETS["soft"].model_dump(mode="json")})
    changed, pictures = _changed_scenes(path)
    assert 1 in changed and pictures == []
