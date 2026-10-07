"""A reviewed shortlist exports frozen clips without replacing its source."""
import json
from pathlib import Path

import pytest

from tests.unit import test_api_editing as fixtures
from tests.unit import test_complete_audition as previews
from tests.unit.test_shorts import story
from voxframe.api.app import _plan_renderer
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import revision
from voxframe.render.compose.complete_preview import engine, stamps

context = fixtures.context
client = fixtures.client
library = fixtures.library
finished_job = fixtures.finished_job
render = previews.render


def source(context, finished_job):  # type: ignore[no-untyped-def]
    folder = context.store.job_directory(finished_job)
    audio = folder / "voice.wav"
    audio.write_bytes(b"voice")
    plan = story().model_copy(update={"audio_path": str(audio)})
    path = context.store.artifact_path(finished_job, "plan")
    plan.save(path)
    return plan, path


def preview(client, endpoint, plan, first=0, last=8, **changes):  # type: ignore[no-untyped-def]
    response = client.post(endpoint + "/preview", json={"revision": revision(plan),
        "first_word": first, "last_word": last, "vertical": True, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def test_preview_queue_distinct_exact_plans_and_idempotent_jobs(
    client, context, finished_job, render, monkeypatch,
):  # type: ignore[no-untyped-def]
    plan, path = source(context, finished_job)
    endpoint = f"/api/jobs/{finished_job}/shorts/batch"
    first = preview(client, endpoint, plan, look="energy", match_captions=True)
    second = preview(client, endpoint, plan, first=20, last=28)
    assert "voice" in first["note"] and first["context_after"]
    assert "plan" not in first and "stamps" not in first
    assert ScenePlan.load(path) == plan
    assert client.get(first["url"], headers={"range": "bytes=0-2"}).status_code == 206
    rendered = []

    def worker(_context):  # type: ignore[no-untyped-def]
        def run(job):  # type: ignore[no-untyped-def]
            p = _context.store.artifact_path(job.id, "plan")
            rendered.append(ScenePlan.load(p))
            video = p.parent / "clip.mp4"
            video.write_bytes(b"export")
            _context.store.record_result(job, artifacts={"plan": p, "video": video},
                                        warnings=(), summary={})
        return run

    monkeypatch.setattr("voxframe.api.app._plan_renderer", worker)
    body = {"revision": revision(plan), "clips": [first["clip_id"], second["clip_id"]], "height": 1280}
    response = client.post(endpoint, json=body)
    assert response.status_code == 202, response.text
    ids = [j["id"] for j in response.json()["jobs"]]
    for job_id in ids:
        context.store.get(job_id).future.result(timeout=5)
    assert len(rendered) == 2
    assert rendered[0] == render[0][0] and rendered[1] == render[1][0]
    assert ScenePlan.load(path) == plan
    assert [j["id"] for j in client.post(endpoint, json=body).json()["jobs"]] == ids
    assert len(rendered) == 2
    assert len(client.get(endpoint).json()["jobs"]) == 2
    assert client.get(f"/api/jobs/{ids[0]}/artifacts/video").status_code == 200
    client.headers.pop("x-voxframe-token")
    assert client.get(endpoint).status_code == 401
    assert client.post(endpoint, json=body).status_code == 401


@pytest.mark.parametrize("problem", ["duplicate", "overlap", "missing", "stale", "changed_media"])
def test_invalid_batch_never_creates_jobs(client, context, finished_job, render, problem):  # type: ignore[no-untyped-def]
    plan, path = source(context, finished_job)
    endpoint = f"/api/jobs/{finished_job}/shorts/batch"
    first = preview(client, endpoint, plan)
    keys = [first["clip_id"]]
    expected = 409
    if problem == "duplicate":
        keys *= 2
        expected = 422
    if problem == "overlap":
        keys.append(preview(client, endpoint, plan, first=5, last=15)["clip_id"])
        expected = 422
    if problem == "missing":
        keys = ["f" * 24]
    if problem == "stale":
        plan.model_copy(update={"music_credit": "changed"}).save(path)
    if problem == "changed_media":
        Path(plan.audio_path).write_bytes(b"changed voice")
    count = len(context.store.all_jobs())
    response = client.post(endpoint, json={"revision": revision(plan), "clips": keys})
    assert response.status_code == expected, response.text
    assert len(context.store.all_jobs()) == count


def test_outside_sources_and_unreviewed_paths_are_refused(client, context, finished_job):  # type: ignore[no-untyped-def]
    plan, path = source(context, finished_job)
    plan = plan.model_copy(update={"audio_path": "/outside/private.wav"})
    plan.save(path)
    endpoint = f"/api/jobs/{finished_job}/shorts/batch"
    response = client.post(endpoint + "/preview", json={"revision": revision(plan),
        "first_word": 0, "last_word": 8})
    assert response.status_code == 403 and "/outside" not in response.text
    assert client.post(endpoint, json={"revision": revision(plan), "clips": ["../private"]}).status_code == 422
    assert client.post(endpoint, json={"revision": revision(plan), "clips": ["a" * 24] * 7}).status_code == 422


@pytest.mark.parametrize("change", ["media", "plan", "engine"])
def test_first_queued_export_checks_approval_again_before_rendering(
    context, finished_job, monkeypatch, change,
):  # type: ignore[no-untyped-def]
    plan, path = source(context, finished_job)
    job = context.store.get(finished_job)
    job.options["batch_parent"] = "source"
    context.store.record_result(job, artifacts={"plan": path}, warnings=(), summary={})
    (path.parent / "approved.json").write_text(json.dumps({"plan": plan.model_dump(mode="json"),
        "stamps": stamps(plan), "engine": engine(plan)}))
    if change == "media":
        Path(plan.audio_path).write_bytes(b"new voice")
    elif change == "plan":
        plan.model_copy(update={"music_credit": "unreviewed edit"}).save(path)
    else:
        monkeypatch.setattr("voxframe.render.compose.complete_preview.RENDERER_VERSION", 999)
    with pytest.raises(ValueError, match="changed"):
        _plan_renderer(context)(job)
    assert context.store.artifact_path(finished_job, "video") is None


def test_failed_clip_does_not_block_the_other_export(client, context, finished_job, render, monkeypatch):  # type: ignore[no-untyped-def]
    plan, _ = source(context, finished_job)
    endpoint = f"/api/jobs/{finished_job}/shorts/batch"
    first = preview(client, endpoint, plan)
    second = preview(client, endpoint, plan, first=20, last=28)

    def worker(_context):  # type: ignore[no-untyped-def]
        def run(job):  # type: ignore[no-untyped-def]
            if job.options["batch_clip"] == first["clip_id"]:
                raise ValueError("One clip's source changed")
        return run

    monkeypatch.setattr("voxframe.api.app._plan_renderer", worker)
    response = client.post(endpoint, json={"revision": revision(plan),
                                           "clips": [first["clip_id"], second["clip_id"]]})
    assert response.status_code == 202
    ids = [j["id"] for j in response.json()["jobs"]]
    for job_id in ids:
        context.store.get(job_id).future.result(timeout=5)
    assert context.store.get(ids[0]).state.value == "failed"
    assert context.store.get(ids[1]).state.value == "succeeded"
    assert context.store.get(ids[0]).snapshot()["resumable"]


def test_auto_quotes_outside_passage_are_removed_but_user_text_is_kept():
    from voxframe.config.visuals import VisualBeat
    from voxframe.plan.short_batch import clean_quotes
    from voxframe.plan.shorts import build_short

    original = story()
    original = original.model_copy(update={"scenes": tuple(s.model_copy(update={
        "visual_beat": VisualBeat(text="simplest solution", source="director")})
        for s in original.scenes)})
    passage = build_short(original, 0, 8)
    assert clean_quotes(passage).scenes[0].visual_beat.text == ""
    user = passage.model_copy(update={"scenes": tuple(s.model_copy(update={
        "visual_beat": s.visual_beat.model_copy(update={"source": "user"})}) for s in passage.scenes)})
    assert clean_quotes(user).scenes[0].visual_beat.text == "simplest solution"


def test_changed_source_during_first_export_is_not_published(context, finished_job, monkeypatch):  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    plan, path = source(context, finished_job)
    job = context.store.get(finished_job)
    job.options["batch_parent"] = "source"
    context.store.record_result(job, artifacts={"plan": path}, warnings=(), summary={})
    (path.parent / "approved.json").write_text(json.dumps({"plan": plan.model_dump(mode="json"),
        "stamps": stamps(plan), "engine": engine(plan)}))

    def changed(*args, **kwargs):  # type: ignore[no-untyped-def]
        Path(plan.audio_path).write_bytes(b"changed during render")
        return SimpleNamespace()

    monkeypatch.setattr("voxframe.render.encode.probe.probe_capabilities", lambda: None)
    monkeypatch.setattr("voxframe.jobs.pipeline.render_plan", changed)
    with pytest.raises(ValueError, match="during export"):
        _plan_renderer(context)(job)
    assert context.store.artifact_path(finished_job, "video") is None
