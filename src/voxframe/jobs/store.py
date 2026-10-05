"""Job tracking for the web app.

A render is a long CPU job — 10 minutes for a 14-minute recording, and that is
*after* Phase 6 made it 13x faster (D-108). An HTTP request cannot hold it, so
the API accepts the work, returns an id, and runs it in a worker thread.

**Why a thread pool and not Celery.** A local single-user app should not require
Redis and a second process to render a video. The work is one long call into
FFmpeg and Whisper, both of which release the GIL while they run, so a thread is
enough. The queue is bounded to one concurrent render by default because the
work is CPU-bound: running two renders at once on a laptop makes both slower and
neither finish sooner.

**Why jobs are persisted.** The browser tab is not the job. Someone who closes
it, or whose machine sleeps, must find their render where they left it — which
is also what makes Phase 6's segment cache visible to a user rather than only to
the CLI (D-101).
"""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog

from voxframe.jobs.pipeline import Stage

__all__ = [
    "Job",
    "JobState",
    "JobStore",
    "ProgressEvent",
]

log = structlog.get_logger(__name__)

#: How many progress events a job keeps. A client that reconnects replays these
#: rather than seeing an empty stream, which is what makes a reloaded tab show
#: history instead of appearing stuck.
PROGRESS_HISTORY = 200


class JobState(StrEnum):
    """Where a job is.

    ``CANCELLED`` is distinct from ``FAILED``: a user who stopped a render did
    not encounter a bug, and conflating them would put their own action in an
    error log.

    ``INTERRUPTED`` is distinct from both: the render did not fail and nobody
    stopped it -- the process running it went away (Voxframe closed, the machine
    slept, a worker died). Nothing is wrong with the job itself, so it is
    offered for resuming, which reuses every scene already rendered (D-101).
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        """No worker is, or will be, working on this job."""
        return self in {
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLED,
            JobState.INTERRUPTED,
        }

    @property
    def is_resumable(self) -> bool:
        """Whether rendering this job again makes sense.

        A succeeded job has nothing left to do; everything else that has
        stopped can be started again from where the cache left it.
        """
        return self in {JobState.FAILED, JobState.CANCELLED, JobState.INTERRUPTED}


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One progress update, as delivered to a client."""

    stage: Stage
    message: str
    fraction: float | None
    at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": str(self.stage),
            "message": self.message,
            "fraction": self.fraction,
            "at": self.at.isoformat(),
        }


@dataclass
class Job:
    """One render, and everything known about it.

    Mutated only under the store's lock. The dataclass is not frozen because a
    job's whole purpose is to change state; :meth:`snapshot` is what callers
    outside the lock read.
    """

    id: str
    state: JobState
    created_at: datetime
    audio_name: str
    options: dict[str, Any]
    stage: Stage = Stage.TRANSCRIBING
    message: str = "Queued"
    error: str = ""
    warnings: tuple[str, ...] = ()
    artifacts: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    progress: deque[ProgressEvent] = field(
        default_factory=lambda: deque(maxlen=PROGRESS_HISTORY)
    )
    cancel_requested: bool = False
    future: Future[None] | None = None

    def snapshot(self) -> dict[str, Any]:
        """A plain dict safe to serialise and hand to a client.

        Never includes ``future`` or any filesystem path: paths are exposed only
        as artifact *names*, which the download route resolves inside the
        sandbox. Handing out absolute paths would invite a client to ask for a
        different one.
        """
        return {
            "id": self.id,
            "state": str(self.state),
            "resumable": self.state.is_resumable,
            "stage": str(self.stage),
            "message": self.message,
            "created_at": self.created_at.isoformat(),
            "audio_name": self.audio_name,
            "error": self.error,
            "warnings": list(self.warnings),
            "artifacts": sorted(self.artifacts),
            "summary": dict(self.summary),
        }


#: How often the background check looks for jobs marked running with no live
#: worker. Short enough that a stuck job is caught long before anyone gives up
#: on it; the check is a dictionary scan, so it costs nothing.
REAP_INTERVAL_SECONDS = 10.0

#: What an interrupted job says, by cause. Written for the person reading the
#: progress screen, not for a log.
INTERRUPTED_BY_RESTART = (
    "Voxframe was closed while this was rendering. Resume to finish it — "
    "scenes already rendered are reused."
)
INTERRUPTED_BY_WORKER = (
    "The render stopped unexpectedly before it finished. Resume to try again — "
    "scenes already rendered are reused."
)


def _is_orphaned(job: Job) -> bool:
    """Whether a job claims to be in progress with nothing working on it.

    - RUNNING is only ever set by the runner, inside its future, so a RUNNING
      job whose future is missing or finished has lost its worker.
    - QUEUED is set by ``create`` *before* ``submit`` attaches a future, so a
      QUEUED job with no future is simply about to be submitted. Reaping it
      would interrupt any new job that a reaper pass happened to land between
      the two calls. Only a QUEUED job whose future has *finished* without
      starting is orphaned.
    """
    if job.state is JobState.RUNNING:
        return job.future is None or job.future.done()
    if job.state is JobState.QUEUED:
        return job.future is not None and job.future.done()
    return False


#: A failure message longer than this is truncated for display. FFmpeg errors
#: carry the whole command line, which is useful in a log and noise on screen.
MAX_FAILURE_MESSAGE = 600


def _describe_failure(exc: BaseException) -> str:
    """A failure message fit to show a user, which cannot itself raise.

    FFmpeg errors lead with a command line hundreds of characters long; the
    part a person can act on is the error text after it, so that is kept.
    """
    try:
        text = str(exc) or exc.__class__.__name__
    except Exception:
        text = exc.__class__.__name__

    if "\nError:\n" in text:
        text = text.split("\nError:\n", 1)[1].strip()

    if len(text) > MAX_FAILURE_MESSAGE:
        text = text[:MAX_FAILURE_MESSAGE].rstrip() + "…"
    return text


def _log_failure_safely(job_id: str, exc: BaseException) -> None:
    """Log a failed job, tolerating a logger that cannot render it.

    The state is already recorded by the time this runs; this is diagnostics
    only, and must never be the reason a failure goes unreported.
    """
    try:
        log.error(
            "job.failed",
            job=job_id,
            error=exc.__class__.__name__,
            exc_info=exc,
        )
    except Exception:
        try:
            log.error("job.failed", job=job_id, error=exc.__class__.__name__)
        except Exception:
            pass


class JobStore:
    """Creates, runs, tracks and persists jobs.

    Thread-safe: the API's request threads and the worker threads both touch it.
    Every mutation takes the lock, and callbacks fired *to* subscribers happen
    outside it so a slow client cannot stall a render.
    """

    def __init__(
        self,
        root: Path,
        *,
        max_workers: int = 1,
        reap_interval: float | None = REAP_INTERVAL_SECONDS,
    ) -> None:
        """
        Args:
            root: Directory for per-job working files and the state file.
            max_workers: Concurrent renders. One by default: the work is
                CPU-bound, so two at once finish later than two in sequence.
            reap_interval: Seconds between checks for jobs marked running with
                no live worker. ``None`` disables the background check; reads
                still reap, so the state a caller sees is always honest.
        """
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._subscribers: dict[str, list[Callable[[ProgressEvent], None]]] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="voxframe-render"
        )
        self._stopping = threading.Event()
        self._load()

        if reap_interval is not None:
            self._reaper = threading.Thread(
                target=self._reap_periodically,
                args=(reap_interval,),
                name="voxframe-reaper",
                daemon=True,
            )
            self._reaper.start()

    # --- paths -----------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    def job_directory(self, job_id: str) -> Path:
        """Where one job's files live.

        A directory per job, named by its id, so nothing a user uploads can
        collide with another job's files or escape into a shared namespace.
        """
        return self._root / job_id

    # --- lifecycle -------------------------------------------------------

    def create(self, *, audio_name: str, options: dict[str, Any]) -> Job:
        """Register a job without starting it.

        The id is a uuid4 rather than a counter: it appears in URLs, and a
        guessable id would let one browser page enumerate another's jobs.
        """
        job = Job(
            id=uuid.uuid4().hex,
            state=JobState.QUEUED,
            created_at=datetime.now(UTC),
            audio_name=audio_name,
            options=options,
        )
        with self._lock:
            self._jobs[job.id] = job
            self.job_directory(job.id).mkdir(parents=True, exist_ok=True)
            self._persist()
        log.info("job.created", job=job.id, audio=audio_name)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            self.reap_orphans()
            return self._jobs.get(job_id)

    def all_jobs(self) -> list[Job]:
        """Every job, newest first."""
        with self._lock:
            self.reap_orphans()
            return sorted(
                self._jobs.values(), key=lambda job: job.created_at, reverse=True
            )

    def delete(self, job_id: str) -> bool:
        """Remove a stopped project and its private workspace, never external assets.

        Returns whether all working files were removed. State must be durably
        saved before removing files; a failed save leaves the project intact.
        A locked file may prevent cleanup, but cannot resurrect a deleted entry.
        """
        with self._lock:
            job = self._jobs[job_id]
            if not job.state.is_terminal or (job.future is not None and not job.future.done()):
                raise ValueError("Wait for this project's render to stop before deleting it.")
            directory = self.job_directory(job_id)
            if (len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id)
                    or directory.is_symlink() or directory.resolve().parent != self.root.resolve()):
                raise ValueError("This project's working folder cannot be safely removed.")
            del self._jobs[job_id]
            try:
                self._persist(strict=True)
            except OSError:
                self._jobs[job_id] = job
                raise
            self._subscribers.pop(job_id, None)
            try:
                if directory.exists():
                    shutil.rmtree(directory)
            except OSError:
                log.warning("job.delete_cleanup_failed", job=job_id)
                return False
            return True

    def submit(self, job: Job, work: Callable[[Job], None]) -> None:
        """Queue ``work`` for a job on the worker pool.

        ``work`` receives the job and reports progress through
        :meth:`record_progress`. Exceptions are caught and recorded as a failure
        rather than vanishing into the executor, which is what makes a failed
        render visible to the user instead of silently stuck at "running".
        """

        def runner() -> None:
            with self._lock:
                if job.cancel_requested:
                    self._finish(job, JobState.CANCELLED, "Cancelled before starting")
                    return
                job.state = JobState.RUNNING
                job.message = "Starting"
                self._persist()
            try:
                work(job)
            except BaseException as exc:
                # Record the failure FIRST, before anything that can itself
                # fail. Logging came first once, and on Windows the traceback
                # renderer raised UnicodeEncodeError writing to a cp1252 stream
                # -- so the handler died inside the handler and the job sat at
                # "running" forever with no error shown (D-124). A failure the
                # user cannot see is worse than the failure.
                with self._lock:
                    self._finish(
                        job, JobState.FAILED, _describe_failure(exc)
                    )
                _log_failure_safely(job.id, exc)
                if not isinstance(exc, Exception):
                    # KeyboardInterrupt and SystemExit are recorded, then
                    # allowed to do what they are for.
                    raise
            else:
                with self._lock:
                    if job.state is JobState.RUNNING:
                        self._finish(job, JobState.SUCCEEDED, "Done")

        job.future = self._executor.submit(runner)

    def reap_orphans(self) -> list[str]:
        """Mark jobs that claim to be in progress, with no live worker, interrupted.

        This is the state D-124 left behind for half an hour: a worker that
        stopped without recording anything, and a job still saying "running".
        See :func:`_is_orphaned` for exactly which jobs qualify, and why a job
        that has only just been created does not.

        Safe to race with a finishing worker: a future is only *done* once the
        runner has returned, and the runner records its outcome before it
        returns. A done future on a job still marked running is therefore never
        a job about to record its own result.

        Returns:
            The ids of the jobs marked interrupted.
        """
        reaped: list[str] = []
        with self._lock:
            for job in self._jobs.values():
                if _is_orphaned(job):
                    self._mark_interrupted(job, INTERRUPTED_BY_WORKER)
                    reaped.append(job.id)
        for job_id in reaped:
            log.warning("job.reaped", job=job_id)
        return reaped

    def resume(self, job_id: str, work: Callable[[Job], None]) -> Job:
        """Start a stopped job again, reusing everything it already rendered.

        Raises:
            KeyError: No such job.
            ValueError: The job is not in a state that can be resumed.
        """
        with self._lock:
            job = self._jobs[job_id]
            if not job.state.is_resumable:
                raise ValueError(f"A {job.state} job cannot be resumed.")
            return self._start_again(job, work, "Resuming")

    def restart(self, job_id: str, work: Callable[[Job], None]) -> Job:
        """Run a finished job again -- a re-render after its plan was edited.

        Unlike :meth:`resume` this accepts a job that succeeded: editing a
        finished video and rendering it again is the normal case.

        Raises:
            KeyError: No such job.
            ValueError: The job is still rendering.
        """
        with self._lock:
            job = self._jobs[job_id]
            if not job.state.is_terminal:
                raise ValueError("This job is still rendering.")
            return self._start_again(job, work, "Rendering your changes")

    def remember(self, job: Job, key: str, value: Any) -> None:
        """Keep a detail with the job across renders (its key starts with ``kept_``).

        Used for a music track the person switched away from, so the Sound
        card can switch back to it (D-179).
        """
        if not key.startswith("kept_"):
            raise ValueError("remembered details are named kept_...")
        with self._lock:
            if value is None:
                job.summary.pop(key, None)
            else:
                job.summary[key] = value
            self._persist()

    def set_pending(self, job: Job, count: int) -> None:
        """How many changes the plan holds that the video does not show (D-182).

        Read from the plan's history, so undoing back to what the video shows
        leaves none.
        """
        with self._lock:
            job.summary["pending_edits"] = max(0, int(count))
            self._persist()

    def note_edit(self, job: Job) -> None:
        """Count an edit made to a job's plan since its last render.

        Shown to the person as "N changes not yet in the video", so they know
        a re-render is what applies them.
        """
        with self._lock:
            job.summary["pending_edits"] = int(job.summary.get("pending_edits", 0)) + 1
            self._persist()

    def _start_again(self, job: Job, work: Callable[[Job], None], message: str) -> Job:
        """Reset a stopped job and submit it. Caller holds the lock."""
        job.state = JobState.QUEUED
        job.stage = Stage.TRANSCRIBING
        job.message = message
        job.error = ""
        job.cancel_requested = False
        job.future = None
        job.progress.clear()
        self._persist()
        # Submitted under the lock, so no reaper pass can see this job QUEUED
        # with a finished future left over from its previous run.
        self.submit(job, work)
        log.info("job.started_again", job=job.id, reason=message)
        return job

    def _mark_interrupted(self, job: Job, message: str) -> None:
        """Record an interruption. Caller holds the lock."""
        job.state = JobState.INTERRUPTED
        job.stage = Stage.DONE
        job.message = message
        job.error = ""
        self._persist()
        self._notify_state(job)

    def _notify_state(self, job: Job) -> None:
        """Wake any progress stream waiting on this job.

        A stream ends when it sees a terminal state; without a nudge, one
        attached to a reaped job would wait until its next keepalive.
        """
        event = ProgressEvent(
            stage=Stage.DONE, message=job.message, fraction=None, at=datetime.now(UTC)
        )
        for listener in list(self._subscribers.get(job.id, ())):
            try:
                listener(event)
            except Exception:
                log.warning("job.listener_failed", job=job.id)

    def _reap_periodically(self, interval: float) -> None:
        while not self._stopping.wait(interval):
            try:
                self.reap_orphans()
            except Exception:
                # The reaper must outlive any single bad pass.
                log.warning("job.reap_failed")

    def request_cancel(self, job_id: str) -> bool:
        """Ask a job to stop.

        Cooperative: a render in FFmpeg cannot be interrupted mid-segment, so
        the flag is honoured at the next stage boundary. Returns whether the job
        existed and was still cancellable.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state.is_terminal:
                return False
            job.cancel_requested = True
            self._persist()
        log.info("job.cancel_requested", job=job_id)
        return True

    def _finish(self, job: Job, state: JobState, message: str) -> None:
        """Record a terminal state. Caller holds the lock."""
        job.state = state
        job.message = message
        if state is JobState.FAILED:
            job.error = message
        job.stage = Stage.DONE
        self._persist()
        log.info("job.finished", job=job.id, state=str(state))

    # --- progress --------------------------------------------------------

    def record_progress(
        self, job: Job, stage: Stage, message: str, fraction: float | None
    ) -> None:
        """Record one update and fan it out to subscribers.

        Subscriber callbacks run outside the lock: a client with a stalled
        connection must not be able to block the render that is feeding it.
        """
        event = ProgressEvent(
            stage=stage, message=message, fraction=fraction, at=datetime.now(UTC)
        )
        with self._lock:
            job.stage = stage
            job.message = message
            job.progress.append(event)
            listeners = list(self._subscribers.get(job.id, ()))
            self._persist()

        for listener in listeners:
            try:
                listener(event)
            except Exception:
                log.warning("job.listener_failed", job=job.id)

    def subscribe(
        self, job_id: str, listener: Callable[[ProgressEvent], None]
    ) -> Callable[[], None]:
        """Register a progress listener.

        Returns:
            A function that unsubscribes. The caller must call it, or a closed
            browser tab leaks a listener for the process's lifetime.
        """
        with self._lock:
            self._subscribers.setdefault(job_id, []).append(listener)

        def unsubscribe() -> None:
            with self._lock:
                listeners = self._subscribers.get(job_id)
                if listeners and listener in listeners:
                    listeners.remove(listener)
                if listeners is not None and not listeners:
                    del self._subscribers[job_id]

        return unsubscribe

    def history(self, job_id: str) -> list[ProgressEvent]:
        """Progress so far, for a client that connected late."""
        with self._lock:
            job = self._jobs.get(job_id)
            return list(job.progress) if job else []

    # --- results ---------------------------------------------------------

    def record_result(
        self,
        job: Job,
        *,
        artifacts: dict[str, Path],
        warnings: tuple[str, ...],
        summary: dict[str, Any],
    ) -> None:
        """Attach a finished render's outputs to its job.

        Artifacts are stored as ``name -> absolute path``, and only the names
        are ever sent to a client. The download route maps a name back to a path
        and re-checks it against the sandbox, so a client cannot substitute one.
        """
        with self._lock:
            job.artifacts = {name: str(path) for name, path in artifacts.items()}
            job.warnings = warnings
            kept = {k: v for k, v in job.summary.items() if k.startswith("kept_")}
            job.summary = {**kept, **summary}
            self._persist()

    def artifact_path(self, job_id: str, name: str) -> Path | None:
        """The path for one named artifact, or ``None``."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            raw = job.artifacts.get(name)
            return Path(raw) if raw else None

    # --- persistence -----------------------------------------------------

    @property
    def _state_file(self) -> Path:
        return self._root / "jobs.json"

    def _persist(self, *, strict: bool = False) -> None:
        """Write job state. Caller holds the lock.

        Written to a temporary file and renamed, so a crash mid-write cannot
        leave a truncated file that loses every job rather than the last one.
        Progress history is deliberately not persisted: it is for a live view,
        and replaying a finished job's spinner messages has no value.
        """
        payload = {
            "version": 1,
            "jobs": [
                {
                    **job.snapshot(),
                    "options": job.options,
                    "artifact_paths": job.artifacts,
                }
                for job in self._jobs.values()
            ],
        }
        temporary = self._state_file.with_suffix(".json.partial")
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8"
            )
            temporary.replace(self._state_file)
        except OSError:
            # A failed write must not kill a running render; the jobs are still
            # correct in memory and the next update will try again.
            log.warning("job.persist_failed", path=str(self._state_file))
            if strict:
                raise

    def _load(self) -> None:
        """Restore jobs from a previous run.

        A job recorded as running cannot still be: the process that ran it is
        gone. It is marked interrupted so the user sees what happened and can
        resume, which reuses every cached segment (D-101).
        """
        if not self._state_file.is_file():
            return

        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("job.state_unreadable", path=str(self._state_file))
            return

        for entry in payload.get("jobs", []):
            try:
                state = JobState(entry["state"])
                if state in {JobState.RUNNING, JobState.QUEUED}:
                    # The process that ran it is gone, so it cannot still be
                    # running. It did not fail either: it is interrupted, and
                    # resuming reuses every scene it finished (D-101).
                    state = JobState.INTERRUPTED
                    message = INTERRUPTED_BY_RESTART
                else:
                    message = entry.get("message", "")

                job = Job(
                    id=entry["id"],
                    state=state,
                    created_at=datetime.fromisoformat(entry["created_at"]),
                    audio_name=entry.get("audio_name", ""),
                    options=entry.get("options", {}),
                    message=message,
                    error=entry.get("error", "") if state is not JobState.FAILED
                    else entry.get("error") or message,
                    warnings=tuple(entry.get("warnings", ())),
                    artifacts=dict(entry.get("artifact_paths", {})),
                    summary=dict(entry.get("summary", {})),
                )
                self._jobs[job.id] = job
            except (KeyError, ValueError):
                # One unreadable entry must not discard the rest.
                log.warning("job.entry_unreadable", entry=str(entry)[:80])

        log.info("job.state_loaded", jobs=len(self._jobs))

    def shutdown(self, *, wait: bool = False) -> None:
        """Stop accepting work.

        ``wait=False`` by default so Ctrl-C returns the terminal immediately;
        an interrupted render resumes from cache.
        """
        self._stopping.set()
        self._executor.shutdown(wait=wait, cancel_futures=not wait)
