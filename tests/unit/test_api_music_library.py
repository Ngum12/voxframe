"""Saved music uses opaque ids, owned bytes, credit snapshots and plan history."""
from __future__ import annotations

# Fixtures intentionally imported for pytest.
# ruff: noqa: F811
from pathlib import Path

from tests.unit.test_api_editing import (  # noqa: F401
    _saved_plan,
    client,
    context,
    finished_job,
    library,
)
from tests.unit.test_music_library import audio


def _import(client, tmp_path: Path):  # type: ignore[no-untyped-def]
    source = audio(tmp_path / "track.wav")
    uploaded = client.post("/api/uploads", files={"file": ("track.wav", source.read_bytes())})
    assert uploaded.status_code == 200
    saved = client.post("/api/music-library", json={"upload_id": uploaded.json()["upload_id"],
                        "title": "Evening", "credit": "Composer, CC0", "mood": "calm"})
    assert saved.status_code == 200
    return saved.json()["track"]


def test_import_select_credit_history_and_hide(client, context, finished_job, tmp_path):  # type: ignore[no-untyped-def]
    track = _import(client, tmp_path)
    assert "path" not in track
    url = f"/api/music-library/{track['id']}"
    heard = client.get(url + "/audio", headers={"range": "bytes=0-43"})
    assert heard.status_code == 206 and heard.content[:4] == b"RIFF"
    assert "audio" in heard.headers["content-type"]
    endpoint = f"/api/jobs/{finished_job}/music"
    selected = client.put(endpoint, json={"choice": "own", "library_id": track["id"]})
    assert selected.status_code == 200
    assert selected.json()["music"]["track_name"] == "Evening"
    plan = _saved_plan(context, finished_job)
    owned = Path(plan.music_path)
    assert owned.is_relative_to(context.settings.library_path / "music")
    assert plan.music_credit == "Composer, CC0"
    assert client.put(url, json={"title": "Retitled", "credit": "Different", "mood": "other"}).status_code == 200
    assert _saved_plan(context, finished_job).music_credit == "Composer, CC0"
    assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 200
    assert not _saved_plan(context, finished_job).music_path
    assert client.post(f"/api/jobs/{finished_job}/plan/redo").status_code == 200
    assert _saved_plan(context, finished_job).music_path == str(owned)
    assert client.delete(url).status_code == 200
    assert client.get(url + "/audio").status_code == 404
    assert client.put(endpoint, json={"choice": "own", "library_id": track["id"]}).status_code == 404
    assert owned.is_file() and _saved_plan(context, finished_job).music_path == str(owned)


def test_invalid_source_and_auth(client, finished_job, tmp_path):  # type: ignore[no-untyped-def]
    track = _import(client, tmp_path)
    endpoint = f"/api/jobs/{finished_job}/music"
    for body in ({"choice": "score", "library_id": track["id"]},
                 {"choice": "own", "library_id": track["id"], "upload_id": "other"},
                 {"choice": "own", "library_id": "../../private.wav"}):
        assert client.put(endpoint, json=body).status_code == 422
    assert client.get("/api/music-library?limit=61").status_code == 422
    assert client.get("/api/music-library?q=Evening&mood=calm").json()["total"] == 1
    client.headers.pop("x-voxframe-token")
    assert client.get("/api/music-library").status_code in (401, 403)


def test_audition_rejects_stale_timing_but_allows_music_edits(
    client, context, finished_job, tmp_path  # type: ignore[no-untyped-def]
):
    from voxframe.api.plan_history import PlanHistory
    from voxframe.render.audio.mixdown import Stems
    from voxframe.render.compose.from_plan import stems_path

    track = _import(client, tmp_path)
    video = context.store.artifact_path(finished_job, "video")
    plan_path = context.store.artifact_path(finished_job, "plan")
    voice = audio(video.parent / "voice.wav")
    Stems(voice=voice, music=None, spans=(), landing=2, video_end=2, fps=30).save(
        stems_path(video))
    history = PlanHistory(plan_path)
    history.begin()
    endpoint = f"/api/jobs/{finished_job}/mix/preview"
    body = {"mix": {}, "music_library_id": track["id"], "seconds": 2}
    response = client.post(endpoint, json=body)
    assert response.status_code == 200 and response.content[:4] == b"RIFF"
    assert client.put(f"/api/jobs/{finished_job}/music",
                      json={"choice": "own", "library_id": track["id"]}).status_code == 200
    assert client.post(endpoint, json=body).status_code == 200
    plan = _saved_plan(context, finished_job)
    changed = plan.scenes[1].model_copy(update={"audio_start": 1.0})
    plan.model_copy(update={"scenes": (plan.scenes[0], changed,
                                       plan.scenes[2].model_copy(update={"audio_start": 4.0}))}).save(plan_path)
    response = client.post(endpoint, json=body)
    assert response.status_code == 409 and "timing edits" in response.json()["detail"]
