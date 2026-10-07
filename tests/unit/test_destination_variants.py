"""Destination exports are immutable, music-inclusive auditions of saved clips."""
import json
from pathlib import Path

import pytest

from tests.unit import test_api_editing as fixtures
from tests.unit import test_complete_audition as complete_fixtures
from tests.unit.test_shorts import story
from voxframe.config.short_export import ShortExport
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.destination_variants import metadata, profiles, record
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import build_short, revision

render = complete_fixtures.render
client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


@pytest.fixture
def source(context, finished_job):  # type: ignore[no-untyped-def]
    child = context.store.create(audio_name="Opening", options={"batch_parent": finished_job,
        "batch_clip": "a" * 24, "height": 1280})
    folder = context.store.job_directory(child.id)
    voice, music = folder / "voice.wav", folder / "music.wav"
    voice.write_bytes(b"voice")
    music.write_bytes(b"soundtrack")
    plan = build_short(story(), 0, 8).model_copy(update={"audio_path": str(voice),
        "music_path": str(music), "music_credit": "Artist CC0",
        "audio_mix": AudioMix(music_db=-9, voice_db=2, music_arc="rise")})
    path = plan.save(folder / "clip.plan.json")
    video = folder / "clip.mp4"
    video.write_bytes(b"finished")
    context.store.submit(child, lambda _: None)
    child.future.result(timeout=5)
    context.store.record_result(child, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    return child, plan, path


def preview(client, parent, source, platform="youtube", **settings):  # type: ignore[no-untyped-def]
    child, plan, _ = source
    response = client.post(f"/api/jobs/{parent}/shorts/batch/variants/preview", json={
        "source_job": child.id, "revision": revision(plan),
        "settings": {**profiles()[platform]["settings"], **settings}})
    assert response.status_code == 200, response.text
    return response.json()


def test_controls_and_preview_preserve_saved_edits_music_and_source_clocks(client, context, finished_job, source, render):  # type: ignore[no-untyped-def]
    controls = client.get(f"/api/jobs/{finished_job}/shorts/batch/variants").json()
    assert [s["job"]["id"] for s in controls["sources"]] == [source[0].id]
    assert controls["profiles"]["whatsapp"]["target_lufs"] == -15
    before = source[2].read_bytes()
    result = preview(client, finished_job, source, "whatsapp", height=1280,
                     safe_area={"top": .15, "bottom": .17, "left": .08, "right": .1})
    draft = render[0][0]
    assert draft.scenes == source[1].scenes and draft.total_frames == source[1].total_frames
    assert draft.music_path == source[1].music_path and draft.music_credit == "Artist CC0"
    assert draft.audio_mix.model_dump(exclude={"destination"}) == source[1].audio_mix.model_dump(exclude={"destination"})
    assert result["destination"] == "whatsapp" and result["settings"]["safe_area"]["top"] == .15
    assert render[0][1]["music"].path == Path(source[1].music_path)
    assert source[2].read_bytes() == before
    assert client.get(result["url"]).status_code == 200
    assert not {"plan", "stamps", "engine"}.intersection(result)


def test_queue_exact_previewed_plans_idempotently_without_mutating_sources(client, context, finished_job, source, render, monkeypatch):  # type: ignore[no-untyped-def]
    captured = []
    def renderer(_):  # type: ignore[no-untyped-def]
        def run(job):  # type: ignore[no-untyped-def]
            captured.append(ScenePlan.load(context.store.artifact_path(job.id, "plan")))
        return run
    monkeypatch.setattr("voxframe.api.app._plan_renderer", renderer)
    a = preview(client, finished_job, source, "youtube", height=1280)
    b = preview(client, finished_job, source, "whatsapp")
    before = source[2].read_bytes()
    url = f"/api/jobs/{finished_job}/shorts/batch/variants"
    response = client.post(url, json={"variants": [a["variant_id"], b["variant_id"]]})
    assert response.status_code == 202, response.text
    ids = [job["id"] for job in response.json()["jobs"]]
    for job_id in ids:
        job = context.store.get(job_id)
        job.future.result(timeout=5)
        approval = json.loads((context.store.job_directory(job_id) / "approved.json").read_text())
        assert ScenePlan.model_validate(approval["plan"]) == ScenePlan.load(context.store.artifact_path(job_id, "plan"))
    assert {p.short_export.platform for p in captured} == {"youtube", "whatsapp"}
    assert all(p.short_export.height == 1280 and p.scenes == source[1].scenes for p in captured)
    assert [job["id"] for job in client.post(url, json={"variants": [a["variant_id"], b["variant_id"]]}).json()["jobs"]] == ids
    assert len(captured) == 2 and source[2].read_bytes() == before
    controls = client.get(url).json()
    assert [s["job"]["id"] for s in controls["sources"]] == [source[0].id]


@pytest.mark.parametrize("change", ["plan", "music", "voice", "missing-preview", "metadata"])
def test_stale_sources_or_snapshots_block_entire_queue(client, context, finished_job, source, render, change):  # type: ignore[no-untyped-def]
    result = preview(client, finished_job, source)
    if change == "plan":
        source[1].model_copy(update={"audio_mix": AudioMix(music_db=-20)}).save(source[2])
    elif change in {"music", "voice"}:
        Path(source[1].music_path if change == "music" else source[1].audio_path).write_bytes(b"changed")
    elif change == "missing-preview":
        (context.store.job_directory(finished_job) / "complete-previews" / f"{result['preview_id']}.mp4").unlink()
    else:
        file = context.store.job_directory(finished_job) / "variant-previews" / f"{result['variant_id']}.json"
        file.write_text('{"source_job": "../../outside"}')
    before = len(context.store.all_jobs())
    response = client.post(f"/api/jobs/{finished_job}/shorts/batch/variants", json={"variants": [result["variant_id"]]})
    assert response.status_code == 409, response.text
    assert len(context.store.all_jobs()) == before


def test_duplicate_destinations_and_wrong_parent_are_rejected(client, context, finished_job, source, render):  # type: ignore[no-untyped-def]
    a = preview(client, finished_job, source, height=1280)
    b = preview(client, finished_job, source, height=1920)
    url = f"/api/jobs/{finished_job}/shorts/batch/variants"
    for variants in ([a["variant_id"], a["variant_id"]], [a["variant_id"], b["variant_id"]], [], [a["variant_id"]] * 25):
        assert client.post(url, json={"variants": variants}).status_code == 422
    other = context.store.create(audio_name="Other", options={})
    response = client.post(f"/api/jobs/{other.id}/shorts/batch/variants/preview", json={
        "source_job": source[0].id, "revision": revision(source[1]), "settings": ShortExport().model_dump(mode="json")})
    assert response.status_code == 404


def test_changed_revision_and_unsafe_sources_are_rejected(client, finished_job, source, render, tmp_path):  # type: ignore[no-untyped-def]
    url = f"/api/jobs/{finished_job}/shorts/batch/variants/preview"
    choice = {"source_job": source[0].id, "revision": "b" * 24, "settings": ShortExport().model_dump(mode="json")}
    assert client.post(url, json=choice).status_code == 409
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"private")
    plan = source[1].model_copy(update={"audio_path": str(outside)})
    plan.save(source[2])
    choice["revision"] = revision(plan)
    assert client.post(url, json=choice).status_code == 403
    assert render == []


def test_routes_and_preview_playback_require_authentication(client, finished_job, source, render):  # type: ignore[no-untyped-def]
    result = preview(client, finished_job, source)
    client.headers.clear()
    url = f"/api/jobs/{finished_job}/shorts/batch/variants"
    assert client.get(url).status_code == 401
    assert client.post(url, json={"variants": [result["variant_id"]]}).status_code == 401
    assert client.post(url + "/preview", json={}).status_code == 401
    assert client.get(result["url"]).status_code == 401


def test_metadata_round_trip_and_profile_targets(tmp_path):  # type: ignore[no-untyped-def]
    snapshot = {"revision": "b" * 24, "key": "c" * 24, "plan": {"short_export": {"platform": "reels"}}}
    key = record(tmp_path, snapshot, "a" * 32)
    assert metadata(tmp_path, key)["platform"] == "reels"
    assert record(tmp_path, snapshot, "a" * 32) == key
    assert not list(tmp_path.glob("*.partial"))
    assert profiles()["reels"]["target_lufs"] == -14


def test_more_than_six_sources_is_rejected_before_any_export_is_created(client, context, finished_job, source, render):  # type: ignore[no-untyped-def]
    variants = [preview(client, finished_job, source)["variant_id"]]
    for number in range(6):
        job = context.store.create(audio_name=f"Source {number}", options={"batch_parent": finished_job})
        folder = context.store.job_directory(job.id)
        plan_path = source[1].save(folder / "clip.plan.json")
        video = folder / "clip.mp4"
        video.write_bytes(b"finished")
        context.store.submit(job, lambda _: None)
        job.future.result(timeout=5)
        context.store.record_result(job, artifacts={"plan": plan_path, "video": video}, warnings=(), summary={})
        variants.append(preview(client, finished_job, (job, source[1], plan_path))["variant_id"])
    count = len(context.store.all_jobs())
    response = client.post(f"/api/jobs/{finished_job}/shorts/batch/variants", json={"variants": variants})
    assert response.status_code == 422 and "six" in response.text
    assert len(context.store.all_jobs()) == count
