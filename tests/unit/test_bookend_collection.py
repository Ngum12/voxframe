"""Source-specific paired approvals export exactly once without editing recordings."""
import json
from pathlib import Path

import pytest

from tests.unit import test_bookend_signatures as signatures
from tests.unit import test_opening_audition as opening
from voxframe.config.bookend_signatures import BookendStyle, delete, save
from voxframe.plan.bookend_collection import metadata
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import revision

client = opening.client
context = opening.context
finished_job = opening.finished_job
library = opening.library
render = opening.render
saved = opening.saved
isolated_config = signatures.isolated_config


@pytest.fixture
def recipe():
    return save("The pair", BookendStyle(opening_look="energy", closing_look="cinema",
        opening_words=4, closing_words=3))


def preview(client, parent, source, recipe, **changes):  # type: ignore[no-untyped-def]
    job, plan, _ = source
    response = client.post(f"/api/jobs/{parent}/bookend-collections/preview", json={
        "source_job": job.id, "signature_id": recipe.id, "revision": revision(plan),
        "last_word": 3, "first_word": 8, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def test_controls_full_sound_private_fields_and_source_specific_approvals(client, context, finished_job, saved, render, recipe):  # type: ignore[no-untyped-def]
    original, path = saved
    source = signatures.new_project(context, original)
    before = source[2].read_bytes()
    controls = client.get(f"/api/jobs/{finished_job}/bookend-collections").json()
    assert any(row["job"]["id"] == source[0].id and row["controls"]["word_count"] == 11 for row in controls["sources"])
    result = preview(client, finished_job, source, recipe)
    assert result["has_music"] and result["source_job"] == source[0].id
    assert result["opening"]["quote"] == "Voici trois idées pour"
    assert result["closing"]["quote"] == "propre voix maintenant."
    assert not {"plan", "stamps", "engine"}.intersection(result)
    assert client.get(result["url"], headers={"range": "bytes=0-2"}).status_code == 206
    info = metadata(context.store.job_directory(finished_job) / "bookend-collection-previews", result["clip_id"])
    assert info.signature_name == recipe.name and info.source_job == source[0].id
    assert render[0][0].music_path == source[1].music_path
    assert source[2].read_bytes() == before and ScenePlan.load(path) == original


def test_two_recordings_export_exact_plans_idempotently_even_after_recipe_removal(client, context, finished_job, saved, render, recipe, monkeypatch):  # type: ignore[no-untyped-def]
    captured = []
    def renderer(_):  # type: ignore[no-untyped-def]
        def run(job):  # type: ignore[no-untyped-def]
            captured.append(ScenePlan.load(context.store.artifact_path(job.id, "plan")))
        return run
    monkeypatch.setattr("voxframe.api.app._plan_renderer", renderer)
    sources = [signatures.new_project(context, saved[0]), signatures.new_project(context, saved[0],
        "Build a bright opening let the middle breathe finish with purpose.")]
    previews = [preview(client, finished_job, source, recipe) for source in sources]
    expected = [item[0] for item in render]
    before = [source[2].read_bytes() for source in sources]
    delete(recipe.id)
    url = f"/api/jobs/{finished_job}/bookend-collections"
    response = client.post(url, json={"clips": [item["clip_id"] for item in previews]})
    assert response.status_code == 202, response.text
    ids = [job["id"] for job in response.json()["jobs"]]
    for job_id, source, draft in zip(ids, sources, expected, strict=True):
        job = context.store.get(job_id)
        job.future.result(timeout=5)
        assert job.options["bookend_source"] == source[0].id
        assert job.options["bookend_signature"] == recipe.name
        approval = json.loads((context.store.job_directory(job_id) / "approved.json").read_text())
        assert ScenePlan.model_validate(approval["plan"]) == draft
        assert ScenePlan.load(context.store.artifact_path(job_id, "plan")) == draft
    assert {p.model_dump_json() for p in captured} == {p.model_dump_json() for p in expected}
    assert [job["id"] for job in client.post(url, json={"clips": [p["clip_id"] for p in previews]}).json()["jobs"]] == ids
    assert len(captured) == 2 and [s[2].read_bytes() for s in sources] == before
    controls = client.get(url).json()
    assert not set(ids).intersection(row["job"]["id"] for row in controls["sources"])
    exports = client.get(f"/api/jobs/{finished_job}/shorts/batch").json()["jobs"]
    assert all(item["signature"] == recipe.name and item["bookend_source"] for item in exports)


@pytest.mark.parametrize("change", ["plan", "voice", "music", "missing-preview", "metadata"])
def test_one_stale_recording_blocks_entire_queue_before_any_job_exists(client, context, finished_job, saved, render, recipe, change):  # type: ignore[no-untyped-def]
    sources = [signatures.new_project(context, saved[0]), signatures.new_project(context, saved[0])]
    previews = [preview(client, finished_job, source, recipe) for source in sources]
    _job, plan, path = sources[1]
    if change == "plan":
        plan.model_copy(update={"music_credit": "Changed credit"}).save(path)
    elif change in {"voice", "music"}:
        Path(plan.audio_path if change == "voice" else plan.music_path).write_bytes(b"changed")
    elif change == "missing-preview":
        (context.store.job_directory(finished_job) / "complete-previews" / f"{previews[1]['preview_id']}.mp4").unlink()
    else:
        file = context.store.job_directory(finished_job) / "bookend-collection-previews" / f"{previews[1]['clip_id']}.json"
        info = json.loads(file.read_text())
        info["source_job"] = "../../private"
        file.write_text(json.dumps(info))
    count = len(context.store.all_jobs())
    response = client.post(f"/api/jobs/{finished_job}/bookend-collections", json={"clips": [p["clip_id"] for p in previews]})
    assert response.status_code == 409 and len(context.store.all_jobs()) == count


def test_duplicate_recordings_limits_and_approval_scoping(client, context, finished_job, saved, render, recipe):  # type: ignore[no-untyped-def]
    source = signatures.new_project(context, saved[0])
    a = preview(client, finished_job, source, recipe)
    b = preview(client, finished_job, source, recipe, first_word=9)
    url = f"/api/jobs/{finished_job}/bookend-collections"
    before = len(context.store.all_jobs())
    for clips in ([], [a["clip_id"]] * 7, [a["clip_id"]] * 2, [a["clip_id"], b["clip_id"]]):
        assert client.post(url, json={"clips": clips}).status_code == 422
    other = context.store.create(audio_name="Other collection", options={})
    assert client.post(f"/api/jobs/{other.id}/bookend-collections", json={"clips": [a["clip_id"]]}).status_code == 409
    assert len(context.store.all_jobs()) == before + 1


def test_stale_controls_pinned_permissions_corrupt_recipe_and_unsafe_media(client, context, finished_job, saved, render, recipe, isolated_config):  # type: ignore[no-untyped-def]
    from voxframe.config.visuals import VisualBeat

    source = signatures.new_project(context, saved[0])
    job, plan, path = source
    url = f"/api/jobs/{finished_job}/bookend-collections/preview"
    request = {"source_job": job.id, "signature_id": recipe.id, "revision": "f" * 24, "last_word": 3, "first_word": 8}
    assert client.post(url, json=request).status_code == 409
    pinned = plan.model_copy(update={"scenes": (plan.scenes[0].model_copy(update={"visual_beat": VisualBeat(text="Pinned", source="user")}),)})
    pinned.save(path)
    request["revision"] = revision(pinned)
    for permits in ({}, {"replace_opening": True}, {"replace_closing": True}):
        assert client.post(url, json={**request, **permits}).status_code == 422
    unsafe = plan.model_copy(update={"audio_path": "/outside/private.wav"})
    unsafe.save(path)
    request["revision"] = revision(unsafe)
    response = client.post(url, json=request)
    assert response.status_code == 403 and "/outside" not in response.text and not render
    isolated_config.write_text("broken")
    assert client.post(url, json=request).status_code == 422
    assert isolated_config.read_text() == "broken"


def test_collection_routes_and_playback_require_authentication(client, context, finished_job, saved, render, recipe):  # type: ignore[no-untyped-def]
    result = preview(client, finished_job, signatures.new_project(context, saved[0]), recipe)
    client.headers.clear()
    url = f"/api/jobs/{finished_job}/bookend-collections"
    assert client.get(url).status_code == 401
    assert client.post(url, json={"clips": [result["clip_id"]]}).status_code == 401
    assert client.post(url + "/preview", json={}).status_code == 401
    assert client.get(result["url"]).status_code == 401


def test_collection_manifest_records_recipe_and_source_without_paths(tmp_path):  # type: ignore[no-untyped-def]
    import io
    import zipfile

    from voxframe.jobs.short_collection import CollectionClip, package

    video = tmp_path / "video.mp4"
    video.write_bytes(b"rendered video")
    clip = CollectionClip(job_id="a" * 32, title="My story", files=(("mp4", video),),
        credits=("Artist credit",), bookend_signature="My pair", source_job="b" * 32)
    result = package("Collection", [clip], tmp_path / "collections", lambda: None)
    payload = (tmp_path / "collections" / f"{result['key']}.zip").read_bytes()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read("collection/manifest.json"))
        item = manifest["clips"][0]
        assert item["bookend_signature"] == "My pair" and item["source_project_id"] == "b" * 32
        assert str(tmp_path) not in json.dumps(manifest)
        assert archive.read("collection/01-my-story.mp4") == video.read_bytes()
