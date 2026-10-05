"""Delete only a stopped project's private files, with durable state first."""
from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path

import pytest

from voxframe.jobs.store import JobState, JobStore


@pytest.fixture
def store(tmp_path):
    value = JobStore(tmp_path / "jobs", reap_interval=None)
    yield value
    value.shutdown(wait=True)


def _finished(store):
    job = store.create(audio_name="talk.mp4", options={})
    store.submit(job, lambda _: None)
    job.future.result(timeout=10)
    return job


def test_delete_keeps_external_files_other_projects_and_survives_restart(store, tmp_path):
    job, other = _finished(store), _finished(store)
    directory = store.job_directory(job.id)
    (directory / "preview.mp4").write_bytes(b"private preview")
    external = tmp_path / "original.mp4"
    external.write_bytes(b"original recording")
    library = tmp_path / "library.wav"
    library.write_bytes(b"shared music")
    saved = tmp_path / "saved-video.mp4"
    saved.write_bytes(b"saved export")
    store.record_result(job, artifacts={"video": saved, "plan": external}, warnings=(),
                        summary={"saved_to": str(saved), "music": str(library)})
    assert store.delete(job.id)
    assert not directory.exists() and store.get(job.id) is None
    assert external.read_bytes() == b"original recording"
    assert library.read_bytes() == b"shared music"
    assert saved.read_bytes() == b"saved export"
    restored = JobStore(store.root, reap_interval=None)
    try:
        assert restored.get(job.id) is None
        assert restored.get(other.id) is not None
        assert restored.job_directory(other.id).is_dir()
    finally:
        restored.shutdown()


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING])
def test_busy_projects_cannot_be_deleted(store, state):
    job = store.create(audio_name="talk.mp4", options={})
    job.state = state
    with pytest.raises(ValueError, match="render to stop"):
        store.delete(job.id)
    assert store.job_directory(job.id).exists()


def test_even_a_terminal_state_waits_for_its_worker(store):
    job = _finished(store)
    job.future = Future()
    with pytest.raises(ValueError, match="render to stop"):
        store.delete(job.id)


def test_persist_failure_preserves_the_project_and_files(store, monkeypatch):
    job = _finished(store)
    directory = store.job_directory(job.id)
    data = directory / "video.mp4"
    data.write_bytes(b"keep me")
    old_state = (store.root / "jobs.json").read_bytes()

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", fail)
    with pytest.raises(OSError):
        store.delete(job.id)
    assert store.get(job.id) is job
    assert data.read_bytes() == b"keep me"
    assert (store.root / "jobs.json").read_bytes() == old_state


def test_locked_working_files_are_reported_without_resurrecting_the_project(store, monkeypatch):
    job = _finished(store)

    def locked(*args, **kwargs):
        raise PermissionError("file in use")

    monkeypatch.setattr("voxframe.jobs.store.shutil.rmtree", locked)
    assert not store.delete(job.id)
    assert store.get(job.id) is None
    assert store.job_directory(job.id).exists()


def test_symlinked_project_folder_cannot_delete_a_shared_folder(store, tmp_path):
    job = _finished(store)
    directory = store.job_directory(job.id)
    directory.rmdir()
    shared = tmp_path / "library"
    shared.mkdir()
    (shared / "asset.jpg").write_bytes(b"keep me")
    try:
        directory.symlink_to(shared, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="safely removed"):
        store.delete(job.id)
    assert store.get(job.id) is job
    assert (shared / "asset.jpg").read_bytes() == b"keep me"


def test_missing_job_never_removes_an_arbitrary_path(store):
    with pytest.raises(KeyError):
        store.delete("../library")
