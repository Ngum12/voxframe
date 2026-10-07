"""Job tracking, and the single-pipeline rule (D-113).

Two front ends over one pipeline is the whole design of Phase 8. The parity
tests at the bottom are what make that a fact rather than an intention: they
fail if the CLI grows an option the API cannot express, or if either front end
starts making pipeline decisions of its own.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from voxframe.jobs.pipeline import (
    JobOptions,
    Stage,
    _imagery_warnings,
    _language_warnings,
)
from voxframe.jobs.store import Job, JobState, JobStore


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs")


class TestJobLifecycle:
    def test_a_new_job_is_queued(self, store: JobStore) -> None:
        job = store.create(audio_name="a.wav", options={})

        assert job.state is JobState.QUEUED

    def test_a_job_gets_its_own_directory(self, store: JobStore) -> None:
        job = store.create(audio_name="a.wav", options={})

        assert store.job_directory(job.id).is_dir()

    def test_job_ids_are_unguessable(self, store: JobStore) -> None:
        """An id appears in URLs; a counter would let one page enumerate another's."""
        first = store.create(audio_name="a.wav", options={})
        second = store.create(audio_name="b.wav", options={})

        assert first.id != second.id
        assert len(first.id) == 32

    def test_work_runs_and_succeeds(self, store: JobStore) -> None:
        job = store.create(audio_name="a.wav", options={})
        done = threading.Event()

        def work(_: Job) -> None:
            done.set()

        store.submit(job, work)
        assert done.wait(timeout=5)
        assert job.future is not None
        job.future.result(timeout=60)

        assert job.state is JobState.SUCCEEDED

    def test_a_failure_is_recorded_rather_than_lost(self, store: JobStore) -> None:
        """An exception vanishing into the executor leaves a job stuck at running."""
        job = store.create(audio_name="a.wav", options={})

        def work(_: Job) -> None:
            raise RuntimeError("encoder exploded")

        store.submit(job, work)
        assert job.future is not None
        job.future.result(timeout=60)

        assert job.state is JobState.FAILED
        assert "encoder exploded" in job.error

    def test_a_failure_is_recorded_even_when_logging_raises(
        self, store: JobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The handler must not die inside the handler (D-124).

        On Windows the traceback renderer raised UnicodeEncodeError writing to a
        cp1252 stream. Logging ran before the state was recorded, so the job sat
        at "running" forever and the user saw a spinner with no error.
        """
        import voxframe.jobs.store as store_module

        class ExplodingLogger:
            """Fails exactly where the real one did: rendering an error.

            Ordinary info lines went through fine; it was the traceback, with
            its box-drawing characters, that could not be encoded.
            """

            def __getattr__(self, name: str):
                def emit(*args: object, **kwargs: object) -> None:
                    if name in {"error", "exception", "critical"}:
                        raise UnicodeEncodeError("charmap", "\u2502", 0, 1, "nope")

                return emit

        monkeypatch.setattr(store_module, "log", ExplodingLogger())
        job = store.create(audio_name="a.wav", options={})

        def work(_: Job) -> None:
            raise RuntimeError("concat failed")

        store.submit(job, work)
        assert job.future is not None
        job.future.result(timeout=60)

        assert job.state is JobState.FAILED
        assert "concat failed" in job.error

    def test_the_failure_is_persisted_not_only_held_in_memory(
        self, store: JobStore
    ) -> None:
        """A reopened store must also say it failed, not "running"."""
        job = store.create(audio_name="a.wav", options={})

        def work(_: Job) -> None:
            raise RuntimeError("encoder exploded")

        store.submit(job, work)
        assert job.future is not None
        job.future.result(timeout=60)

        reopened = JobStore(store.root).get(job.id)

        assert reopened is not None
        assert reopened.state is JobState.FAILED

    def test_an_ffmpeg_error_shows_the_error_not_the_command(self) -> None:
        """The command line is hundreds of characters; the cause is at the end."""
        from voxframe.jobs.store import _describe_failure

        message = _describe_failure(
            RuntimeError(
                "FFmpeg failed with exit code 1.\n"
                "Command: ffmpeg -i a.mp4 -i b.mp4 " + "-x " * 200 + "\n"
                "Error:\nInput link parameters do not match"
            )
        )

        assert message.startswith("Input link parameters do not match")
        assert "-x -x" not in message

    def test_a_long_failure_is_truncated(self) -> None:
        from voxframe.jobs.store import MAX_FAILURE_MESSAGE, _describe_failure

        message = _describe_failure(RuntimeError("x" * 5000))

        assert len(message) <= MAX_FAILURE_MESSAGE + 1

    def test_a_failure_with_a_broken_str_is_still_recorded(self) -> None:
        from voxframe.jobs.store import _describe_failure

        class Unprintable(Exception):
            def __str__(self) -> str:
                raise ValueError("cannot print me")

        assert _describe_failure(Unprintable()) == "Unprintable"

    def test_cancelling_before_the_start_does_not_run_the_work(
        self, store: JobStore
    ) -> None:
        job = store.create(audio_name="a.wav", options={})
        ran = threading.Event()

        store.request_cancel(job.id)
        store.submit(job, lambda _: ran.set())
        assert job.future is not None
        job.future.result(timeout=60)

        assert job.state is JobState.CANCELLED
        assert not ran.is_set()

    def test_cancelling_a_finished_job_is_refused(self, store: JobStore) -> None:
        """A user's own stop must not be reported as an error afterwards."""
        job = store.create(audio_name="a.wav", options={})
        store.submit(job, lambda _: None)
        assert job.future is not None
        job.future.result(timeout=60)

        assert store.request_cancel(job.id) is False

    def test_cancelled_is_not_failed(self) -> None:
        assert JobState.CANCELLED is not JobState.FAILED
        assert JobState.CANCELLED.is_terminal


class TestProgress:
    def test_progress_is_recorded(self, store: JobStore) -> None:
        job = store.create(audio_name="a.wav", options={})

        store.record_progress(job, Stage.TRANSCRIBING, "Transcribing", None)

        assert job.stage is Stage.TRANSCRIBING
        assert store.history(job.id)[-1].message == "Transcribing"

    def test_subscribers_are_notified(self, store: JobStore) -> None:
        job = store.create(audio_name="a.wav", options={})
        seen = []
        store.subscribe(job.id, seen.append)

        store.record_progress(job, Stage.RENDERING, "Rendering", 0.5)

        assert [event.message for event in seen] == ["Rendering"]

    def test_unsubscribing_stops_notifications(self, store: JobStore) -> None:
        """A closed tab must not leak a listener for the process's lifetime."""
        job = store.create(audio_name="a.wav", options={})
        seen = []
        unsubscribe = store.subscribe(job.id, seen.append)

        unsubscribe()
        store.record_progress(job, Stage.RENDERING, "Rendering", None)

        assert seen == []

    def test_a_failing_listener_does_not_stop_the_render(
        self, store: JobStore
    ) -> None:
        """A broken client must not be able to kill the job feeding it."""
        job = store.create(audio_name="a.wav", options={})

        def broken(event: object) -> None:
            raise RuntimeError("client went away")

        store.subscribe(job.id, broken)
        store.record_progress(job, Stage.RENDERING, "Rendering", None)

        assert job.message == "Rendering"

    def test_history_is_bounded(self, store: JobStore) -> None:
        """A long render must not accumulate progress events without limit."""
        job = store.create(audio_name="a.wav", options={})

        for index in range(500):
            store.record_progress(job, Stage.RENDERING, f"scene {index}", None)

        assert len(store.history(job.id)) <= 200


class TestPersistence:
    def test_jobs_survive_a_restart(self, store: JobStore) -> None:
        store.create(audio_name="talk.wav", options={"height": 720})

        reopened = JobStore(store.root)

        assert [job.audio_name for job in reopened.all_jobs()] == ["talk.wav"]

    def test_a_running_job_becomes_interrupted(self, store: JobStore) -> None:
        """The process that ran it is gone; it cannot still be running."""
        job = store.create(audio_name="a.wav", options={})
        job.state = JobState.RUNNING
        store._persist()

        reopened = JobStore(store.root)
        restored = reopened.get(job.id)

        assert restored is not None
        # Interrupted, not failed: nothing went wrong with the job itself.
        assert restored.state is JobState.INTERRUPTED
        assert restored.state.is_resumable
        assert "resume" in restored.message.lower()
        assert not restored.error

    def test_an_unreadable_state_file_does_not_crash(self, tmp_path: Path) -> None:
        root = tmp_path / "jobs"
        root.mkdir()
        (root / "jobs.json").write_text("{not json", encoding="utf-8")

        assert JobStore(root).all_jobs() == []

    def test_one_bad_entry_does_not_discard_the_others(
        self, store: JobStore
    ) -> None:
        import json

        good = store.create(audio_name="good.wav", options={})
        payload = json.loads((store.root / "jobs.json").read_text(encoding="utf-8"))
        payload["jobs"].append({"id": "broken"})  # missing required fields
        (store.root / "jobs.json").write_text(json.dumps(payload), encoding="utf-8")

        reopened = JobStore(store.root)

        assert reopened.get(good.id) is not None

    def test_a_snapshot_carries_no_filesystem_paths(self, store: JobStore) -> None:
        """Artifacts are exposed as names; a path would invite asking for another."""
        job = store.create(audio_name="a.wav", options={})
        store.record_result(
            job,
            artifacts={"video": store.root / "out.mp4"},
            warnings=(),
            summary={},
        )

        snapshot = job.snapshot()

        assert snapshot["artifacts"] == ["video"]
        assert "out.mp4" not in str(snapshot)


class TestWarnings:
    """Honesty about failure has to survive the interface (D-102)."""

    def test_a_low_confidence_language_warns(self) -> None:
        warnings = _language_warnings("yo", 0.31, forced=False, restricted=False)

        assert warnings and "low confidence" in warnings[0]

    def test_a_forced_language_does_not_warn(self) -> None:
        assert not _language_warnings("en", 0.2, forced=True, restricted=False)

    def test_a_confident_detection_does_not_warn(self) -> None:
        assert not _language_warnings("en", 0.99, forced=False, restricted=False)

    def test_a_restricted_set_advises_forcing_rather_than_restricting(self) -> None:
        """Under a candidate set the probability stays low even when right (D-068)."""
        warnings = _language_warnings("fr", 0.4, forced=False, restricted=True)

        assert "skip detection" in warnings[0]

    def test_no_library_explains_the_plain_background(self) -> None:
        """A first-run user must be told why, not left to guess (decision 6)."""
        plan = _fake_plan(scenes=5, matched=0)

        warnings = _imagery_warnings(plan, library=None)

        assert warnings and "add your own photo" in warnings[0]

    @pytest.mark.parametrize(
        ("scenes", "matched", "library"),
        [(5, 0, None), (5, 0, Path("library")), (10, 6, Path("library"))],
    )
    @pytest.mark.parametrize("sourcing", [False, True])
    def test_the_advice_is_what_works(
        self, scenes: int, matched: int, library: Path | None, sourcing: bool
    ) -> None:
        """Messages say only what works (D-131). Searching online is suggested
        only when it is off, since telling someone to switch on what is
        already on reads as the tool not listening. The Library, which works
        now (D-146), is suggested when nothing at all was found.
        """
        warnings = _imagery_warnings(
            _fake_plan(scenes=scenes, matched=matched),
            library=library,
            sourcing=sourcing,
        )

        assert warnings
        for warning in warnings:
            text = warning.lower()
            assert ("your library" in text) == (matched == 0)
            if sourcing:
                assert "search online" not in text
            else:
                assert "search online for images" in text

    def test_an_empty_library_explains_itself(self) -> None:
        plan = _fake_plan(scenes=5, matched=0)

        warnings = _imagery_warnings(plan, library=Path("library"))

        assert warnings and "None of the 5 scenes" in warnings[0]

    def test_a_partial_fill_reports_the_count(self) -> None:
        plan = _fake_plan(scenes=10, matched=6)

        warnings = _imagery_warnings(plan, library=Path("library"))

        assert warnings and "4 of 10" in warnings[0]

    def test_a_title_card_is_not_counted_as_a_failed_match(self) -> None:
        """A card is drawn text; it never takes a photograph (D-126).

        Counting it made a first-run video say "all 4 scenes show a plain
        background" when one of the four was a title that looked as intended,
        and disagreed with the filmstrip, which counted 3.
        """
        plan = _fake_plan(scenes=3, matched=0, cards=1)

        warnings = _imagery_warnings(plan, library=None)

        assert "all 3 scenes" in warnings[0]

    def test_a_fully_matched_plan_with_a_card_says_nothing(self) -> None:
        plan = _fake_plan(scenes=3, matched=3, cards=1)

        assert _imagery_warnings(plan, library=Path("library")) == []

    def test_a_full_fill_says_nothing(self) -> None:
        """Nothing to explain, so no noise."""
        plan = _fake_plan(scenes=4, matched=4)

        assert _imagery_warnings(plan, library=Path("library")) == []


class TestFrontEndParity:
    """The CLI and the API must stay two front ends over one pipeline.

    Two implementations that drift are worse than one, and the drift is silent:
    a flag added to the CLI simply never appears in the browser.
    """

    def test_every_job_option_is_reachable_from_the_api(self) -> None:
        pytest.importorskip("fastapi", reason="web extra not installed")
        from voxframe.api.app import RenderRequest

        # Options the API sets itself rather than taking from a client: paths it
        # controls, and flags that only make sense for a terminal.
        internal = {
            "audio",       # comes from the upload, never a client path
            "output",      # the API chooses where a job writes
            "plan_out",    # derived from the output path
            "music",       # a file, uploaded separately (a later step)
            "score",       # chosen as score_style; the seed is made per video (D-176)
            "no_cache",    # a debugging flag, not a user-facing option
            "want_plan",   # the web app always wants a plan: it edits it
            # Decided by the server from saved consent and keys; a request must
            # never be able to switch on network access (D-132).
            "source_imagery",
            # The app always keeps the uncaptioned copy its studio plays; the
            # command line has no studio (D-196).
            "studio_copy",
        }
        expected = {field.name for field in JobOptions.__dataclass_fields__.values()}
        exposed = set(RenderRequest.model_fields) | internal
        # The API names these for the client; the pipeline names them plainly.
        exposed.add("library")
        exposed.add("footage")  # use_video (D-192)

        missing = expected - exposed

        assert not missing, (
            f"JobOptions the web app cannot express: {sorted(missing)}. "
            f"Add them to RenderRequest, or to the 'internal' set with a reason."
        )

    def test_the_pipeline_owns_the_stage_list(self) -> None:
        """Both front ends report the same stages, from one definition."""
        assert Stage.TRANSCRIBING in list(Stage)
        assert Stage.DONE is list(Stage)[-1]

    def test_no_pipeline_logic_lives_in_the_api(self) -> None:
        """The API validates and delegates; it must not build plans itself.

        An AST check rather than a review habit, in the same spirit as the
        subprocess guard (D-020).
        """
        pytest.importorskip("fastapi", reason="web extra not installed")
        import ast

        source = Path("src/voxframe/api/app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        forbidden = {"build_plan", "insert_cards", "segment_transcript",
                     "render_from_plan", "render_captioned_video"}
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert not (called & forbidden), (
            f"The API calls the pipeline directly: {sorted(called & forbidden)}. "
            f"It must go through voxframe.jobs.pipeline so the CLI and the web "
            f"app cannot diverge."
        )


def _fake_plan(*, scenes: int, matched: int, cards: int = 0) -> object:
    """A stand-in with just the attributes the warning helpers read."""

    class FakeScene:
        def __init__(self, card_kind: str = "") -> None:
            self.card_kind = card_kind

    class FakePlan:
        def __init__(self) -> None:
            self.scenes = [FakeScene("title") for _ in range(cards)] + [
                FakeScene() for _ in range(scenes)
            ]
            self.matched_scenes = matched

    return FakePlan()
