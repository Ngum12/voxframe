"""Collection approvals bind acknowledgements to finished outputs and current edits."""
import io
import json
import zipfile
from dataclasses import replace

import pytest

from tests.unit import test_api_editing as fixtures
from tests.unit.test_pacing import source
from tests.unit.test_short_collection import child
from voxframe.jobs.collection_review import ReviewClip, approve, approved, report
from voxframe.plan.shorts import revision

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


def item(tmp_path, job_id="a" * 32):
    folder = tmp_path / job_id
    folder.mkdir()
    plan = source()
    path = plan.save(folder / "plan.json")
    video = folder / "video.mp4"
    video.write_bytes(b"finished video")
    return ReviewClip(job_id, "Story", plan, path, (("video", video),), 720,
                      {"rendered_revision": revision(plan), "sound": {"passed": True, "problems": [], "integrated_lufs": -16, "true_peak": -1}})


def acknowledgements(current):
    return [cue["id"] for row in current["clips"] for cue in row["issues"]], [row["job_id"] for row in current["clips"]]


def test_review_is_readonly_scoped_and_private(tmp_path):
    clips = [item(tmp_path), item(tmp_path, "b" * 32)]
    before = [clip.plan_path.read_bytes() for clip in clips]
    current = report(clips)
    ids, watched = acknowledgements(current)
    assert len(ids) == len(set(ids)) == 2
    assert str(tmp_path) not in json.dumps(current)
    assert "pixels" in current["note"]
    assert before == [clip.plan_path.read_bytes() for clip in clips]
    approve(tmp_path / "reviews", current, current["fingerprint"], ids, watched)
    receipt = approved(tmp_path / "reviews", current["fingerprint"], current)
    assert receipt["watched_clips"] == watched


@pytest.mark.parametrize("change", ["video", "plan", "order", "sound"])
def test_changed_outputs_edits_order_or_sound_expire_approval(tmp_path, change):
    clips = [item(tmp_path), item(tmp_path, "b" * 32)]
    current = report(clips)
    checked, watched = acknowledgements(current)
    approve(tmp_path / "reviews", current, current["fingerprint"], checked, watched)
    if change == "video":
        clips[0].files[0][1].write_bytes(b"new finished output")
    elif change == "plan":
        clips[0].plan_path.write_text(clips[0].plan.model_dump_json() + " ")
    elif change == "order":
        clips.reverse()
    else:
        clips[0].summary["sound"]["true_peak"] = 0
    with pytest.raises(ValueError, match="expired"):
        approved(tmp_path / "reviews", current["fingerprint"], report(clips))


@pytest.mark.parametrize("checked,watched", [([], ["a" * 32]), (["unknown"], ["a" * 32]), (None, []), (None, ["a" * 32, "a" * 32])])
def test_every_cue_and_finished_clip_requires_explicit_confirmation(tmp_path, checked, watched):
    current = report([item(tmp_path)])
    ids, _ = acknowledgements(current)
    with pytest.raises(ValueError):
        approve(tmp_path / "reviews", current, current["fingerprint"], ids if checked is None else checked, watched)


def test_pending_changes_cannot_be_acknowledged_away(tmp_path):
    clip = item(tmp_path)
    clip.summary["pending_edits"] = 1
    current = report([clip])
    checked, watched = acknowledgements(current)
    with pytest.raises(ValueError, match="blocked"):
        approve(tmp_path / "reviews", current, current["fingerprint"], checked, watched)
    clip.summary["pending_edits"] = 0
    clip = replace(clip, plan=clip.plan.model_copy(update={"scenes": (clip.plan.scenes[0].model_copy(update={"caption_text": "New words"}), *clip.plan.scenes[1:])}))
    assert report([clip])["clips"][0]["blockers"]


def test_api_requires_review_and_exports_receipt_then_rejects_stale_video(client, context, finished_job):
    job = child(context, finished_job)
    job.options["bookend_source"] = finished_job
    plan = source()
    plan.save(context.store.artifact_path(job.id, "plan"))
    job.summary.update(pending_edits=0, rendered_revision=revision(plan))
    endpoint = f"/api/jobs/{finished_job}/shorts/batch/collections"
    body = {"title": "Reviewed", "clips": [{"job_id": job.id, "title": "Story"}]}
    assert client.post(endpoint, json=body).status_code == 409
    current = client.post(endpoint + "/review", json={"clips": [job.id]}).json()
    checked, watched = acknowledgements(current)
    response = client.post(endpoint + "/review/approve", json={"clips": [job.id], "fingerprint": current["fingerprint"], "checked": checked, "watched": watched})
    assert response.status_code == 200, response.text
    body.update(response.json())
    result = client.post(endpoint, json=body)
    assert result.status_code == 200, result.text
    with zipfile.ZipFile(io.BytesIO(client.get(result.json()["url"]).content)) as archive:
        manifest = json.loads(archive.read("reviewed/manifest.json"))
        assert manifest["finishing_review"]["watched_clips"] == [job.id]
        assert str(context.store.job_directory(job.id)) not in json.dumps(manifest)
    context.store.artifact_path(job.id, "video").write_bytes(b"changed")
    assert client.post(endpoint, json=body).status_code == 409


def test_report_records_render_sound_problems_without_claiming_a_pass(tmp_path):
    clip = item(tmp_path)
    clip.summary["sound"] = {"passed": False, "problems": ["Peak above destination target"], "integrated_lufs": float("nan"), "true_peak": 0.5}
    current = report([clip])
    cues = current["clips"][0]["issues"]
    assert any(cue["detail"] == "Peak above destination target" for cue in cues)
    assert any(cue["id"].endswith("sound-unmeasured") for cue in cues)
    assert current["clips"][0]["sound"]["integrated_lufs"] is None
    json.dumps(current, allow_nan=False)


def test_review_api_checks_parent_duplicates_limits_and_pending_edits(client, context, finished_job):
    job = child(context, finished_job)
    plan = source()
    plan.save(context.store.artifact_path(job.id, "plan"))
    job.summary.update(rendered_revision=revision(plan))
    endpoint = f"/api/jobs/{finished_job}/shorts/batch/collections/review"
    assert client.post(endpoint, json={"clips": [job.id, job.id]}).status_code == 422
    assert client.post(endpoint, json={"clips": [job.id] * 7}).status_code == 422
    assert client.post(endpoint, json={"clips": [finished_job]}).status_code == 404
    current = client.post(endpoint, json={"clips": [job.id]}).json()
    assert current["clips"][0]["blockers"]
    checked, watched = acknowledgements(current)
    assert client.post(endpoint + "/approve", json={"clips": [job.id], "fingerprint": current["fingerprint"], "checked": checked, "watched": watched}).status_code == 409
    from fastapi.testclient import TestClient
    with TestClient(client.app, base_url=fixtures.LOOPBACK) as anonymous:
        assert anonymous.post(endpoint, json={"clips": [job.id]}).status_code == 401


@pytest.mark.parametrize("change", ["plan", "receipt"])
def test_changes_during_packaging_do_not_publish_a_reviewed_zip(client, context, finished_job, monkeypatch, change):
    from voxframe.jobs import short_collection
    job = child(context, finished_job)
    job.options["bookend_source"] = finished_job
    plan = source()
    path = context.store.artifact_path(job.id, "plan")
    plan.save(path)
    job.summary.update(pending_edits=0, rendered_revision=revision(plan))
    endpoint = f"/api/jobs/{finished_job}/shorts/batch/collections"
    current = client.post(endpoint + "/review", json={"clips": [job.id]}).json()
    checked, watched = acknowledgements(current)
    receipt = client.post(endpoint + "/review/approve", json={"clips": [job.id], "fingerprint": current["fingerprint"], "checked": checked, "watched": watched}).json()
    original = short_collection.package

    def changed(*args, **kwargs):
        if change == "plan":
            path.write_text(plan.model_dump_json() + " ")
        else:
            (context.store.job_directory(finished_job) / "collection-reviews" / f"{receipt['review_id']}.json").write_text("{}")
        return original(*args, **kwargs)

    monkeypatch.setattr(short_collection, "package", changed)
    response = client.post(endpoint, json={"title": "Stale", "clips": [{"job_id": job.id, "title": "Story"}], **receipt})
    assert response.status_code == 409, response.text
    assert not list(context.store.job_directory(finished_job).glob("collections/*.zip"))
