"""A job must never stay "running" with nothing running it (D-129).

The failure this exists for: a render's worker stopped without recording
anything, and the page said "Rendering" for half an hour (D-124). That specific
cause is fixed, but the class of failure is not -- a process can be killed, a
machine can sleep, a future bug can kill a handler. So the store now checks,
on startup and periodically while running, for jobs that claim to be in
progress with no live worker, and marks them **interrupted**, with a resume
option.

The central test kills a real worker process mid-render with no chance to clean
up, then checks what a fresh server sees.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from voxframe.jobs.pipeline import Stage
from voxframe.jobs.store import (
    INTERRUPTED_BY_RESTART,
    INTERRUPTED_BY_WORKER,
    Job,
    JobState,
    JobStore,
)

SRC = Path(__file__).resolve().parents[2] / "src"

#: Runs a render in its own process and blocks mid-render until killed.
WORKER_SCRIPT = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    sys.path.insert(0, {src!r})
    from voxframe.jobs.pipeline import Stage
    from voxframe.jobs.store import JobStore

    store = JobStore(Path({root!r}), reap_interval=None)
    job = store.create(audio_name="lecture.wav", options={{}})

    def work(job):
        store.record_progress(job, Stage.RENDERING, "Rendering scene 3 of 9", None)
        print("MID-RENDER " + job.id, flush=True)
        time.sleep(300)  # killed long before this returns

    store.submit(job, work)
    time.sleep(300)
    """
)


def _read_job_id(process: subprocess.Popen[str]) -> str:
    """Read the worker's output until it reports it is mid-render.

    Log lines share stdout with the marker, so the first line is not
    necessarily the one wanted.
    """
    assert process.stdout is not None
    for _ in range(200):
        line = process.stdout.readline()
        if not line:
            break
        if line.startswith("MID-RENDER"):
            return line.split()[1]
    raise AssertionError("the worker never reported that it was mid-render")


def _wait_until(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


class TestKilledWorker:
    """The owner's test: kill a worker mid-render; the job must not stay running."""

    def test_a_job_whose_process_was_killed_is_interrupted(self, tmp_path: Path) -> None:
        root = tmp_path / "jobs"
        process = subprocess.Popen(
            [sys.executable, "-c", WORKER_SCRIPT.format(src=str(SRC), root=str(root))],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            job_id = _read_job_id(process)

            # Precondition: on disk, the job really is recorded as running. Without
            # this the test could pass by never having been running at all.
            on_disk = json.loads((root / "jobs.json").read_text(encoding="utf-8"))
            states = {entry["id"]: entry["state"] for entry in on_disk["jobs"]}
            assert states[job_id] == "running"
        finally:
            # A hard kill: no handler, no finally, no chance to record anything.
            process.kill()
            process.wait(timeout=10)

        restarted = JobStore(root, reap_interval=None)
        job = restarted.get(job_id)

        assert job is not None
        assert job.state is JobState.INTERRUPTED
        assert job.state is not JobState.RUNNING
        assert job.state.is_resumable
        assert job.message == INTERRUPTED_BY_RESTART

    def test_the_interruption_is_persisted(self, tmp_path: Path) -> None:
        """Reopened twice, it must still say interrupted, not revert to running."""
        root = tmp_path / "jobs"
        process = subprocess.Popen(
            [sys.executable, "-c", WORKER_SCRIPT.format(src=str(SRC), root=str(root))],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            job_id = _read_job_id(process)
        finally:
            process.kill()
            process.wait(timeout=10)

        JobStore(root, reap_interval=None).get(job_id)
        again = JobStore(root, reap_interval=None).get(job_id)

        assert again is not None
        assert again.state is JobState.INTERRUPTED


class TestWorkerDiedInProcess:
    """A worker that finished without recording anything, inside a live server."""

    @staticmethod
    def _silent_death(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> Job:
        """Run a job whose outcome is never recorded, as in D-124."""
        monkeypatch.setattr(store, "_finish", lambda *args, **kwargs: None)
        job = store.create(audio_name="a.wav", options={})
        store.submit(job, lambda _job: None)
        assert job.future is not None
        job.future.result(timeout=5)
        return job

    def test_reading_the_job_reaps_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = JobStore(tmp_path / "jobs", reap_interval=None)
        job = self._silent_death(store, monkeypatch)
        assert job.state is JobState.RUNNING  # the stuck state, reproduced

        seen = store.get(job.id)

        assert seen is not None
        assert seen.state is JobState.INTERRUPTED
        assert seen.message == INTERRUPTED_BY_WORKER

    def test_the_periodic_check_reaps_it_without_anyone_asking(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nobody has to open the page for the state to become honest."""
        store = JobStore(tmp_path / "jobs", reap_interval=0.05)
        try:
            job = self._silent_death(store, monkeypatch)

            # Read the attribute directly, not through get(), which also reaps.
            assert _wait_until(lambda: job.state is JobState.INTERRUPTED)
        finally:
            store.shutdown()

    def test_a_progress_stream_is_told(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A browser waiting on the stream must hear about it, not keep waiting."""
        store = JobStore(tmp_path / "jobs", reap_interval=None)
        job = self._silent_death(store, monkeypatch)
        heard = []
        store.subscribe(job.id, heard.append)

        store.reap_orphans()

        assert heard, "the stream was not notified"
        assert heard[-1].stage is Stage.DONE


class TestNothingHealthyIsReaped:
    """The check must never interrupt a job that is actually fine."""

    def test_a_job_being_rendered_is_left_alone(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs", reap_interval=None)
        release = threading.Event()
        started = threading.Event()
        job = store.create(audio_name="a.wav", options={})

        def work(_job: Job) -> None:
            started.set()
            release.wait(5)

        store.submit(job, work)
        try:
            assert started.wait(5)
            assert store.reap_orphans() == []
            assert job.state is JobState.RUNNING
        finally:
            release.set()
            assert job.future is not None
            job.future.result(timeout=5)

        assert job.state is JobState.SUCCEEDED

    def test_a_job_created_but_not_yet_submitted_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """The API creates, then submits. A check landing between must not fire."""
        store = JobStore(tmp_path / "jobs", reap_interval=None)
        job = store.create(audio_name="a.wav", options={})

        assert store.reap_orphans() == []
        assert job.state is JobState.QUEUED

    def test_a_job_waiting_behind_another_is_left_alone(self, tmp_path: Path) -> None:
        """One worker, two jobs: the second is queued, not orphaned."""
        store = JobStore(tmp_path / "jobs", reap_interval=None)
        release = threading.Event()
        first = store.create(audio_name="a.wav", options={})
        second = store.create(audio_name="b.wav", options={})

        store.submit(first, lambda _job: release.wait(5))
        store.submit(second, lambda _job: None)
        try:
            assert _wait_until(lambda: first.state is JobState.RUNNING)
            assert store.reap_orphans() == []
            assert second.state is JobState.QUEUED
        finally:
            release.set()
            for job in (first, second):
                assert job.future is not None
                job.future.result(timeout=5)

    @pytest.mark.parametrize(
        "state", [JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED]
    )
    def test_a_finished_job_is_left_alone(
        self, tmp_path: Path, state: JobState
    ) -> None:
        store = JobStore(tmp_path / "jobs", reap_interval=None)
        job = store.create(audio_name="a.wav", options={})
        job.state = state

        assert store.reap_orphans() == []
        assert job.state is state


class TestResume:
    def test_an_interrupted_job_resumes_and_finishes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = JobStore(tmp_path / "jobs", reap_interval=None)
        job = TestWorkerDiedInProcess._silent_death(store, monkeypatch)
        store.reap_orphans()
        monkeypatch.undo()  # the next run records normally

        store.resume(job.id, lambda _job: None)
        assert job.future is not None
        job.future.result(timeout=5)

        assert job.state is JobState.SUCCEEDED

    def test_resuming_keeps_the_same_job(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same id, so the page following it keeps following it."""
        store = JobStore(tmp_path / "jobs", reap_interval=None)
        job = TestWorkerDiedInProcess._silent_death(store, monkeypatch)
        store.reap_orphans()
        monkeypatch.undo()

        resumed = store.resume(job.id, lambda _job: None)

        assert resumed.id == job.id
        assert len(store.all_jobs()) == 1

    def test_a_succeeded_job_cannot_be_resumed(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs", reap_interval=None)
        job = store.create(audio_name="a.wav", options={})
        store.submit(job, lambda _job: None)
        assert job.future is not None
        job.future.result(timeout=5)

        with pytest.raises(ValueError, match="cannot be resumed"):
            store.resume(job.id, lambda _job: None)

    def test_a_failed_job_can_be_retried(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path / "jobs", reap_interval=None)
        job = store.create(audio_name="a.wav", options={})

        def fail(_job: Job) -> None:
            raise RuntimeError("encoder exploded")

        store.submit(job, fail)
        assert job.future is not None
        job.future.result(timeout=5)
        assert job.state is JobState.FAILED

        store.resume(job.id, lambda _job: None)
        assert job.future is not None
        job.future.result(timeout=5)

        assert job.state is JobState.SUCCEEDED
        assert not job.error
