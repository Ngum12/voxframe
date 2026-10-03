"""The routes for the hook and the pace (D-199)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient

from tests.unit.test_api_captions import client, context
from voxframe.api.app import ApiContext
from voxframe.plan.pace import CutKind
from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan

__all__ = ["client", "context"]  # the fixtures, shared

#: "So ... the river rose. Did you know three towns flooded?" with a long
#: pause after "So", and an "um".
WORDS = [
    ("So", 0.2, 0.4), ("um", 1.6, 1.8), ("the", 1.9, 2.0), ("river", 2.05, 2.4),
    ("rose.", 2.45, 2.8), ("Did", 3.0, 3.2), ("you", 3.25, 3.4), ("know", 3.45, 3.7),
    ("three", 3.75, 4.0), ("towns", 4.05, 4.4), ("flooded?", 4.45, 5.0),
]


def _job(context: ApiContext) -> str:
    words = tuple(PlanWord(text=t, start=s, end=e) for t, s, e in WORDS)
    plan = ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64, audio_duration=6.0, fps=30.0,
        total_frames=180,
        scenes=(PlannedScene(index=0, start_frame=0, end_frame=180, text="x", words=words),),
    )
    job = context.store.create(audio_name="talk.mp4", options={"height": 1920})
    directory = context.store.job_directory(job.id)
    plan_path = plan.save(directory / "source.plan.json")
    (directory / "source.mp4").write_bytes(b"video")
    context.store.submit(job, lambda _job: None)
    assert job.future is not None
    job.future.result(timeout=5)
    context.store.record_result(
        job, artifacts={"plan": plan_path, "video": directory / "source.mp4"}, warnings=(),
        summary={"width": 1080, "height": 1920},
    )
    return job.id


def _saved(context: ApiContext, job: str) -> ScenePlan:
    path = context.store.artifact_path(job, "plan")
    assert path is not None
    return ScenePlan.load(path)


def test_cuts_are_found_listed_and_made(client: TestClient, context: ApiContext) -> None:
    job = _job(context)
    body = client.post(f"/api/jobs/{job}/pace/cuts/find", json={}).json()
    kinds = [cut["kind"] for cut in body["cuts"]]
    assert "silence" in kinds and "filler" in kinds and "edge" in kinds
    assert body["video_seconds"] < body["recording_seconds"] - 1.0
    assert body["undo_label"] == "the jump cuts"
    timeline = client.get(f"/api/jobs/{job}/timeline").json()
    assert "um" not in [w["text"] for w in timeline["scenes"][0]["words"]]
    assert timeline["scenes"][0]["story_index"] == 0
    # "So" is word 0 and "the" word 2 in the plan; "um" is gone from the video.
    assert timeline["scenes"][0]["story_words"][:2] == [0, 2]


def test_a_cut_is_undone_on_its_own(client: TestClient, context: ApiContext) -> None:
    job = _job(context)
    body = client.post(f"/api/jobs/{job}/pace/cuts/find", json={}).json()
    filler = next(c for c in body["cuts"] if c["kind"] == "filler")
    shortened = body["video_seconds"]
    body = client.put(f"/api/jobs/{job}/pace/cuts/{filler['index']}", json={"on": False}).json()
    assert body["video_seconds"] > shortened
    assert not _saved(context, job).pace.cuts[filler["index"]].on
    # Finding the cuts again keeps it off.
    client.post(f"/api/jobs/{job}/pace/cuts/find", json={})
    again = [c for c in _saved(context, job).pace.cuts if c.kind is CutKind.FILLER]
    assert again and not again[0].on


def test_the_best_hook_is_offered_and_opens_the_video(
    client: TestClient, context: ApiContext
) -> None:
    job = _job(context)
    hooks = client.get(f"/api/jobs/{job}/pace").json()["hooks"]
    best = hooks[0]
    assert best["text"] == "Did you know three towns flooded?"
    body = client.put(
        f"/api/jobs/{job}/pace/cold-open", json={"span": {"start": best["start"], "end": best["end"]}}
    ).json()
    assert body["cold_open"] == [best["start"], best["end"]]
    timeline = client.get(f"/api/jobs/{job}/timeline").json()
    assert timeline["scenes"][0]["teaser"] is True
    assert timeline["scenes"][0]["words"][0]["text"] == "Did"
    assert client.put(f"/api/jobs/{job}/pace/cold-open", json={"span": None}).status_code == 200
    assert _saved(context, job).pace.cold_open is None


def test_a_cold_open_too_long_is_refused(client: TestClient, context: ApiContext) -> None:
    job = _job(context)
    response = client.put(
        f"/api/jobs/{job}/pace/cold-open", json={"span": {"start": 0.0, "end": 5.9}}
    )
    assert response.status_code == 200  # 5.9 s is a long line, not too long
    response = client.put(
        f"/api/jobs/{job}/pace/cold-open", json={"span": {"start": 0.0, "end": 0.3}}
    )
    assert response.status_code == 422


def test_a_stretch_chosen_in_the_video_is_cut(client: TestClient, context: ApiContext) -> None:
    job = _job(context)
    client.post(f"/api/jobs/{job}/pace/cuts/find", json={})
    timeline = client.get(f"/api/jobs/{job}/timeline").json()
    words = timeline["scenes"][0]["words"]
    river = next(w for w in words if w["text"] == "river")
    response = client.post(
        f"/api/jobs/{job}/pace/cuts", json={"start": river["start"], "end": river["end"]}
    )
    assert response.status_code == 200
    manual = [c for c in _saved(context, job).pace.cuts if c.kind is CutKind.MANUAL]
    assert len(manual) == 1 and manual[0].start == pytest.approx(2.05, abs=0.02)


def test_the_hook_title_is_drawn(client: TestClient, context: ApiContext) -> None:
    job = _job(context)
    assert client.put(f"/api/jobs/{job}/hook-title", json={"text": "Floods  are coming"}).status_code == 200
    assert _saved(context, job).hook_title == "Floods are coming"
    assert "Floods are" in client.get(f"/api/jobs/{job}/captions").text


def test_stillness_is_reported(client: TestClient, context: ApiContext) -> None:
    job = _job(context)
    stillness = client.get(f"/api/jobs/{job}/pace").json()["stillness"]
    assert stillness == [{"start": 0.0, "end": 6.0}]
