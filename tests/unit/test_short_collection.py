"""Collections preserve finished bytes, render credits, ordering and privacy."""
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from tests.unit import test_api_editing as fixtures
from voxframe.jobs.short_collection import CollectionClip, package, slug
from voxframe.jobs.store import JobState

context = fixtures.context
client = fixtures.client
library = fixtures.library
finished_job = fixtures.finished_job


def clip(folder: Path, job_id: str = "a" * 32, title: str = "My hook") -> CollectionClip:
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for kind, body in (("mp4", b"exact video bytes"), ("srt", b"1\n00:00:00,000 --> 00:00:01,000\nHello\n"),
                       ("vtt", b"WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n")):
        path = folder / f"output.{kind}"
        path.write_bytes(body)
        files.append((kind, path))
    return CollectionClip(job_id=job_id, title=title, files=tuple(files),
                          credits=("Picture: Test author (CC0)", "Music: Original artist"),
                          width=720, height=1280)


def test_finished_bytes_subtitles_and_credits_are_complete_and_cache_is_immutable(tmp_path):  # type: ignore[no-untyped-def]
    first = clip(tmp_path / "private-source")
    folder = tmp_path / "collections"
    result = package("Launch collection", [first], folder, lambda: None)
    path = folder / f"{result['key']}.zip"
    timestamp = path.stat().st_mtime_ns
    assert result["name"] == "launch-collection.zip"
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("launch-collection/manifest.json"))
        item = manifest["clips"][0]
        assert item["title"] == first.title and item["width"] == 720
        for entry, (kind, source) in zip(item["files"], first.files, strict=True):
            assert archive.read(f"launch-collection/{entry['name']}") == source.read_bytes()
            assert entry["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
            assert entry["name"] == f"01-my-hook.{kind}"
        assert archive.getinfo("launch-collection/01-my-hook.mp4").compress_type == zipfile.ZIP_STORED
        assert "Music: Original artist" in archive.read("launch-collection/CREDITS.txt").decode()
        assert not any("plan" in name or "source" in name for name in archive.namelist())
        assert str(tmp_path) not in json.dumps(manifest)
        assert "private-source" not in json.dumps(manifest)
    assert package("Launch collection", [first], folder, lambda: None) == result
    assert path.stat().st_mtime_ns == timestamp


@pytest.mark.parametrize("name", ["../../secret", "CON", "aux.", "My: <short> / cool?", "视频", "NUL"])
def test_filenames_are_portable_without_directories_or_windows_reserved_names(name, tmp_path):  # type: ignore[no-untyped-def]
    result = package(name, [clip(tmp_path / "sources", title=name)], tmp_path / "out", lambda: None)
    assert "/" not in slug(name) and "\\" not in slug(name) and "." not in slug(name)
    assert slug(name) not in {"con", "aux", "nul"}
    with zipfile.ZipFile(tmp_path / "out" / f"{result['key']}.zip") as archive:
        assert all(not Path(member).is_absolute() and ".." not in Path(member).parts
                   for member in archive.namelist())


def test_reordering_duplicate_names_and_changed_output_have_distinct_snapshots(tmp_path):  # type: ignore[no-untyped-def]
    first, second = clip(tmp_path / "a"), clip(tmp_path / "b", "b" * 32)
    folder = tmp_path / "out"
    a = package("Set", [first, second], folder, lambda: None)
    b = package("Set", [second, first], folder, lambda: None)
    assert a["key"] != b["key"]
    with zipfile.ZipFile(folder / f"{b['key']}.zip") as archive:
        manifest = json.loads(archive.read("set/manifest.json"))
        assert [item["project_id"] for item in manifest["clips"]] == [second.job_id, first.job_id]
        assert len(archive.namelist()) == len(set(archive.namelist()))
    first.files[0][1].write_bytes(b"replacement rendered video")
    c = package("Set", [first, second], folder, lambda: None)
    assert c["key"] not in {a["key"], b["key"]}
    with zipfile.ZipFile(folder / f"{a['key']}.zip") as archive:
        assert archive.read("set/01-my-hook.mp4") == b"exact video bytes"


def test_source_changes_during_packaging_do_not_publish_partial_zip(tmp_path):  # type: ignore[no-untyped-def]
    first = clip(tmp_path / "a")
    calls = []

    def changed():  # type: ignore[no-untyped-def]
        calls.append(True)
        if len(calls) == 2:
            first.files[0][1].write_bytes(b"changed while packaging")

    with pytest.raises(ValueError, match="changed while packaging"):
        package("Set", [first], tmp_path / "out", changed)
    assert list((tmp_path / "out").iterdir()) == []


def child(context, parent_id, name="Hook"):  # type: ignore[no-untyped-def]
    job = context.store.create(audio_name=name, options={"batch_parent": parent_id})
    exported = clip(context.store.job_directory(job.id), job.id, name)
    context.store.submit(job, lambda _: None)
    job.future.result(timeout=5)
    plan = context.store.job_directory(job.id) / "private.plan.json"
    plan.write_text('new unrendered settings with private paths')
    context.store.record_result(job, artifacts={"plan": plan, "video": exported.files[0][1],
        "srt": exported.files[1][1], "vtt": exported.files[2][1]}, warnings=(),
        summary={"credits": list(exported.credits), "width": 720, "height": 1280, "pending_edits": 2})
    return job


def test_api_uses_actual_render_credits_with_pending_edits_and_authenticated_download(
    client, context, finished_job,
):  # type: ignore[no-untyped-def]
    job = child(context, finished_job)
    endpoint = f"/api/jobs/{finished_job}/shorts/batch/collections"
    response = client.post(endpoint, json={"title": "My set", "clips": [{"job_id": job.id, "title": "Opening"}]})
    assert response.status_code == 200, response.text
    data = response.json()
    download = client.get(data["url"])
    assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
    assert "my-set.zip" in download.headers["content-disposition"]
    assert len(download.content) == data["bytes"]
    partial = client.get(data["url"], headers={"range": "bytes=0-31"})
    assert partial.status_code == 206 and partial.content == download.content[:32]
    assert "my-set.zip" in partial.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        manifest = json.loads(archive.read("my-set/manifest.json"))
        assert manifest["clips"][0]["pending_edits"] == 2
        assert manifest["clips"][0]["credits"] == ["Picture: Test author (CC0)", "Music: Original artist"]
        assert "private" not in json.dumps(manifest)
        assert "new unrendered" not in archive.read("my-set/CREDITS.txt").decode()
    client.headers.pop("x-voxframe-token")
    assert client.get(data["url"]).status_code == 401
    assert client.post(endpoint, json={"title": "Set", "clips": [{"job_id": job.id, "title": "One"}]}).status_code == 401


@pytest.mark.parametrize("problem", ["other_parent", "unfinished", "missing", "duplicate", "outside", "blank", "limit"])
def test_invalid_selections_are_refused_without_packaging(client, context, finished_job, problem):  # type: ignore[no-untyped-def]
    job = child(context, finished_job)
    body = {"title": "Set", "clips": [{"job_id": job.id, "title": "One"}]}
    expected = 422
    if problem == "other_parent":
        job.options["batch_parent"] = "another-project"
        expected = 404
    if problem == "unfinished":
        job.state = JobState.RUNNING
        expected = 409
    if problem == "missing":
        context.store.artifact_path(job.id, "srt").unlink()
        expected = 409
    if problem == "duplicate":
        body["clips"] *= 2
    if problem == "outside":
        job.artifacts["video"] = "/outside/private.mp4"
        expected = 403
    if problem == "blank":
        body["clips"][0]["title"] = "  "
    if problem == "limit":
        body["clips"] *= 7
    response = client.post(f"/api/jobs/{finished_job}/shorts/batch/collections", json=body)
    assert response.status_code == expected, response.text
    assert not (context.store.job_directory(finished_job) / "collections").exists()
    assert "/outside" not in response.text


def test_missing_subtitle_outputs_are_explicit_not_fabricated(client, context, finished_job):  # type: ignore[no-untyped-def]
    job = child(context, finished_job)
    del job.artifacts["srt"]
    del job.artifacts["vtt"]
    response = client.post(f"/api/jobs/{finished_job}/shorts/batch/collections", json={"title": "Set",
        "clips": [{"job_id": job.id, "title": "One"}]})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(client.get(response.json()["url"]).content)) as archive:
        manifest = json.loads(archive.read("set/manifest.json"))
        assert [f["name"] for f in manifest["clips"][0]["files"]] == ["01-one.mp4"]


def test_cached_package_rechecks_exports_and_recovers_an_unreadable_zip(tmp_path):  # type: ignore[no-untyped-def]
    first = clip(tmp_path / "a")
    folder = tmp_path / "out"
    original = package("Set", [first], folder, lambda: None)
    cached = folder / f"{original['key']}.zip"
    cached.write_bytes(b"damaged generated cache")
    repaired = package("Set", [first], folder, lambda: None)
    assert repaired["key"] == original["key"]
    assert zipfile.is_zipfile(cached)
    calls = []

    def changed():  # type: ignore[no-untyped-def]
        calls.append(True)
        if len(calls) == 2:
            first.files[0][1].write_bytes(b"replacement during cache lookup")

    with pytest.raises(ValueError, match="export changed"):
        package("Set", [first], folder, changed)


def test_child_rerender_started_during_packaging_is_refused(client, context, finished_job, monkeypatch):  # type: ignore[no-untyped-def]
    from voxframe.jobs import short_collection

    job = child(context, finished_job)
    actual = short_collection.package

    def changed(title, clips, directory, check_current):  # type: ignore[no-untyped-def]
        def starts_render():  # type: ignore[no-untyped-def]
            job.state = JobState.RUNNING
            check_current()
        return actual(title, clips, directory, starts_render)

    monkeypatch.setattr(short_collection, "package", changed)
    response = client.post(f"/api/jobs/{finished_job}/shorts/batch/collections", json={"title": "Set",
        "clips": [{"job_id": job.id, "title": "One"}]})
    assert response.status_code == 409
    assert not (context.store.job_directory(finished_job) / "collections").exists()
