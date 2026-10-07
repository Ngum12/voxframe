"""The editing, re-render and resume routes (D-127, D-128, D-129).

Rendering itself is replaced with a fake here: these tests are about what the
routes accept, refuse, save and report. The real render path is exercised by
the browser verification on real audio.
"""

from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient

from voxframe.api.app import ApiContext, create_app
from voxframe.api.security import SessionToken
from voxframe.config.settings import Settings
from voxframe.jobs.pipeline import PipelineOutcome
from voxframe.jobs.store import JobState, JobStore
from voxframe.plan.scene_plan import (
    MotionKind,
    PlanAsset,
    PlannedScene,
    ScenePlan,
)
from voxframe.render.compose.captioned import RenderResult

LOOPBACK = "http://127.0.0.1:8765"


def _asset(asset_id: str, path: Path, similarity: float | None = None) -> PlanAsset:
    return PlanAsset(
        id=asset_id, path=str(path), width=1600, height=900,
        license_name="CC0", license_author="Someone", license_source="Test",
        similarity=similarity,
    )


@pytest.fixture
def library(tmp_path: Path) -> Path:
    directory = tmp_path / "library"
    directory.mkdir()
    for name in ("chosen", "runner-up", "close"):
        (directory / f"{name}.jpg").write_bytes(b"not really a jpeg")
    return directory


@pytest.fixture
def context(tmp_path: Path, library: Path) -> ApiContext:
    root = tmp_path / "web"
    return ApiContext(
        settings=Settings(
            library_path=library, cache_path=tmp_path / "cache",
            output_path=tmp_path / "out",
        ),
        store=JobStore(root, reap_interval=None),
        token=SessionToken("t"),
        allowed_paths=(root.resolve(), library.resolve()),
    )


@pytest.fixture
def client(context: ApiContext) -> TestClient:
    test_client = TestClient(create_app(context), base_url=LOOPBACK)
    test_client.headers.update({"x-voxframe-token": "t"})
    return test_client


def _plan(library: Path) -> ScenePlan:
    return ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64, audio_duration=10.0,
        fps=30.0, total_frames=300,
        scenes=(
            PlannedScene(
                index=0, start_frame=0, end_frame=90, card_kind="title",
                card_text="A Talk", motion=MotionKind.NONE,
            ),
            PlannedScene(
                index=1, start_frame=90, end_frame=200, text="a river",
                asset=_asset("chosen", library / "chosen.jpg"),
                alternatives=(_asset("runner-up", library / "runner-up.jpg", 0.21),),
                semantic_score=0.24,
            ),
            PlannedScene(
                index=2, start_frame=200, end_frame=300, text="something high",
                near_misses=(_asset("close", library / "close.jpg", 0.16),),
                motion=MotionKind.NONE,
                match_reason="best similarity 0.16 below threshold 0.17",
            ),
        ),
    )


@pytest.fixture
def finished_job(context: ApiContext, library: Path) -> str:
    """A job that rendered, with a plan and a video on disk."""
    job = context.store.create(audio_name="a.wav", options={"height": 480})
    directory = context.store.job_directory(job.id)
    plan_path = directory / "source.plan.json"
    _plan(library).save(plan_path)
    video = directory / "source.mp4"
    video.write_bytes(b"video")
    context.store.submit(job, lambda _job: None)
    assert job.future is not None
    job.future.result(timeout=60)
    context.store.record_result(
        job, artifacts={"plan": plan_path, "video": video}, warnings=(), summary={}
    )
    return job.id


def _saved_plan(context: ApiContext, job_id: str) -> ScenePlan:
    path = context.store.artifact_path(job_id, "plan")
    assert path is not None
    return ScenePlan.load(path)


class TestChooseImage:
    def test_a_near_miss_can_be_used_anyway(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        response = client.put(
            f"/api/jobs/{finished_job}/scenes/2/image",
            json={"action": "choose", "asset_id": "close"},
        )

        assert response.status_code == 200
        saved = _saved_plan(context, finished_job).scenes[2]
        assert saved.asset is not None
        assert saved.asset.id == "close"
        assert saved.asset_source == "user"

    def test_a_runner_up_can_be_chosen(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        response = client.put(
            f"/api/jobs/{finished_job}/scenes/1/image",
            json={"action": "choose", "asset_id": "runner-up"},
        )

        assert response.status_code == 200
        saved = _saved_plan(context, finished_job).scenes[1]
        assert saved.asset is not None
        assert saved.asset.id == "runner-up"

    def test_the_edit_is_counted_as_pending(
        self, client: TestClient, finished_job: str
    ) -> None:
        """So the page can say the change is not in the video yet."""
        client.put(
            f"/api/jobs/{finished_job}/scenes/2/image",
            json={"action": "choose", "asset_id": "close"},
        )
        client.put(f"/api/jobs/{finished_job}/scenes/1/image", json={"action": "remove"})

        job = client.get(f"/api/jobs/{finished_job}").json()

        assert job["summary"]["pending_edits"] == 2

    def test_an_image_can_be_removed(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        response = client.put(
            f"/api/jobs/{finished_job}/scenes/1/image", json={"action": "remove"}
        )

        assert response.status_code == 200
        assert _saved_plan(context, finished_job).scenes[1].asset is None

    def test_an_unknown_asset_is_refused(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        response = client.put(
            f"/api/jobs/{finished_job}/scenes/2/image",
            json={"action": "choose", "asset_id": "../../secret"},
        )

        assert response.status_code == 422
        assert _saved_plan(context, finished_job).scenes[2].asset is None

    def test_a_path_is_never_accepted(self, client: TestClient, finished_job: str) -> None:
        """The request model has no field that takes a path."""
        response = client.put(
            f"/api/jobs/{finished_job}/scenes/2/image",
            json={"action": "choose", "asset_id": "close", "path": "/etc/passwd"},
        )

        saved = response.json()["scene"]["asset"]
        assert saved["path"].endswith("close.jpg")

    def test_a_card_cannot_take_an_image(
        self, client: TestClient, finished_job: str
    ) -> None:
        response = client.put(
            f"/api/jobs/{finished_job}/scenes/0/image", json={"action": "remove"}
        )

        assert response.status_code == 422

    def test_a_bad_action_is_refused(self, client: TestClient, finished_job: str) -> None:
        response = client.put(
            f"/api/jobs/{finished_job}/scenes/1/image", json={"action": "delete-all"}
        )

        assert response.status_code == 422

    def test_editing_during_a_render_is_refused(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        """The renderer is reading the plan; changing it underneath would mix
        two versions."""
        release = threading.Event()
        context.store.restart(finished_job, lambda _job: release.wait(5))
        try:
            response = client.put(
                f"/api/jobs/{finished_job}/scenes/1/image", json={"action": "remove"}
            )
            assert response.status_code == 409
        finally:
            release.set()


class TestOwnImage:
    @staticmethod
    def _png() -> bytes:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (640, 360), (40, 90, 160)).save(buffer, format="PNG")
        return buffer.getvalue()

    def test_an_own_photo_is_used(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        response = client.post(
            f"/api/jobs/{finished_job}/scenes/2/image/own",
            files={"file": ("holiday.png", self._png(), "image/png")},
        )

        assert response.status_code == 200
        saved = _saved_plan(context, finished_job).scenes[2]
        assert saved.asset is not None
        assert (saved.asset.width, saved.asset.height) == (640, 360)

    def test_provenance_defaults_to_the_persons_own_work(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        """Owner's decision 4, and provenance is never blank (D-035)."""
        client.post(
            f"/api/jobs/{finished_job}/scenes/2/image/own",
            files={"file": ("holiday.png", self._png(), "image/png")},
        )

        saved = _saved_plan(context, finished_job).scenes[2].asset
        assert saved is not None
        assert saved.license_name == "Own work"
        assert saved.license_author == "You"

    def test_the_file_is_stored_inside_the_job(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        """Under a name the server chose; the client's name is not a path."""
        client.post(
            f"/api/jobs/{finished_job}/scenes/2/image/own",
            files={"file": ("../../evil.png", self._png(), "image/png")},
        )

        saved = _saved_plan(context, finished_job).scenes[2].asset
        assert saved is not None
        stored = Path(saved.path)
        assert context.store.job_directory(finished_job).resolve() in stored.parents
        assert "evil" not in stored.name

    def test_a_renamed_non_image_is_refused(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        """A file of any kind renamed .png would otherwise reach FFmpeg."""
        response = client.post(
            f"/api/jobs/{finished_job}/scenes/2/image/own",
            files={"file": ("photo.png", b"#!/bin/sh\nrm -rf /", "image/png")},
        )

        assert response.status_code == 415
        own = context.store.job_directory(finished_job) / "own"
        assert not any(own.glob("*")) if own.exists() else True

    def test_an_unsupported_type_is_refused(
        self, client: TestClient, finished_job: str
    ) -> None:
        response = client.post(
            f"/api/jobs/{finished_job}/scenes/2/image/own",
            files={"file": ("anim.gif", b"GIF89a", "image/gif")},
        )

        assert response.status_code == 415


class TestCandidateThumbnails:
    def test_a_candidate_outside_the_sandbox_is_refused(
        self, client: TestClient, context: ApiContext, tmp_path: Path
    ) -> None:
        """The plan is editable, so a candidate's path is untrusted (D-122)."""
        secret = tmp_path / "id_rsa.jpg"
        secret.write_text("PRIVATE", encoding="utf-8")
        job = context.store.create(audio_name="a.wav", options={})
        plan_path = context.store.job_directory(job.id) / "plan.json"
        plan = _plan(tmp_path / "library")
        scene = plan.scenes[2].model_copy(
            update={"near_misses": (_asset("close", secret, 0.16),)}
        )
        plan.model_copy(update={"scenes": (*plan.scenes[:2], scene)}).save(plan_path)
        context.store.record_result(
            job, artifacts={"plan": plan_path}, warnings=(), summary={}
        )

        response = client.get(f"/api/jobs/{job.id}/scenes/2/candidates/close/thumbnail")

        assert response.status_code == 403
        assert "PRIVATE" not in response.text

    def test_an_unknown_candidate_is_a_404(
        self, client: TestClient, finished_job: str
    ) -> None:
        response = client.get(
            f"/api/jobs/{finished_job}/scenes/2/candidates/nope/thumbnail"
        )

        assert response.status_code == 404


def _fake_render(monkeypatch: pytest.MonkeyPatch, calls: list[Path]) -> None:
    """Replace the renderer with one that records the call and succeeds."""

    def render_plan(plan_path, output, settings, capabilities, **kwargs):
        calls.append(plan_path)
        plan = ScenePlan.load(plan_path)
        result = RenderResult(
            video_path=output, srt_path=None, vtt_path=None, ass_path=output,
            width=854, height=480, fps=30.0, frame_count=300,
            audio_duration=10.0, elapsed_seconds=1.0,
        )
        return PipelineOutcome(
            result=result, plan=plan, plan_path=plan_path, language="en",
            language_probability=1.0, word_count=4, scene_count=len(plan.scenes),
        )

    monkeypatch.setattr("voxframe.jobs.pipeline.render_plan", render_plan)
    monkeypatch.setattr(
        "voxframe.render.encode.probe.probe_capabilities", lambda *a, **k: None
    )


def _wait(context: ApiContext, job_id: str) -> None:
    job = context.store.get(job_id)
    assert job is not None and job.future is not None
    job.future.result(timeout=10)


class TestRerender:
    def test_a_rerender_renders_the_edited_plan(
        self,
        client: TestClient,
        context: ApiContext,
        finished_job: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[Path] = []
        _fake_render(monkeypatch, calls)
        client.put(
            f"/api/jobs/{finished_job}/scenes/2/image",
            json={"action": "choose", "asset_id": "close"},
        )

        response = client.post(f"/api/jobs/{finished_job}/render")
        _wait(context, finished_job)

        assert response.status_code == 202
        assert calls, "the renderer was not called"
        rendered = ScenePlan.load(calls[0])
        assert rendered.scenes[2].asset is not None
        assert rendered.scenes[2].asset.id == "close"

    def test_a_rerender_keeps_the_same_job(
        self,
        client: TestClient,
        context: ApiContext,
        finished_job: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _fake_render(monkeypatch, [])

        client.post(f"/api/jobs/{finished_job}/render")
        _wait(context, finished_job)

        assert len(context.store.all_jobs()) == 1
        job = context.store.get(finished_job)
        assert job is not None and job.state is JobState.SUCCEEDED

    def test_a_rerender_clears_the_pending_edits(
        self,
        client: TestClient,
        context: ApiContext,
        finished_job: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _fake_render(monkeypatch, [])
        client.put(f"/api/jobs/{finished_job}/scenes/1/image", json={"action": "remove"})

        client.post(f"/api/jobs/{finished_job}/render")
        _wait(context, finished_job)

        job = client.get(f"/api/jobs/{finished_job}").json()
        assert job["summary"]["pending_edits"] == 0
        assert job["summary"]["edited_scenes"] == 1

    def test_the_summary_counts_scenes_one_click_from_an_image(
        self,
        client: TestClient,
        context: ApiContext,
        finished_job: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The result screen says so rather than leaving it to be found (D-138).
        Scene 2 is plain with a close match; the title card never counts."""
        _fake_render(monkeypatch, [])

        client.post(f"/api/jobs/{finished_job}/render")
        _wait(context, finished_job)

        summary = client.get(f"/api/jobs/{finished_job}").json()["summary"]
        assert summary["close_match_scenes"] == 1
        assert summary["atmospheric_scenes"] == 0

    def test_a_filled_scene_is_no_longer_one_click_away(
        self,
        client: TestClient,
        context: ApiContext,
        finished_job: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _fake_render(monkeypatch, [])
        client.put(
            f"/api/jobs/{finished_job}/scenes/2/image",
            json={"action": "choose", "asset_id": "close"},
        )

        client.post(f"/api/jobs/{finished_job}/render")
        _wait(context, finished_job)

        summary = client.get(f"/api/jobs/{finished_job}").json()["summary"]
        assert summary["close_match_scenes"] == 0

    def test_a_rerender_never_rematches(
        self,
        client: TestClient,
        context: ApiContext,
        finished_job: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Re-matching would replace the choices just made (D-128)."""
        _fake_render(monkeypatch, [])

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("a re-render ran the full pipeline")

        monkeypatch.setattr("voxframe.jobs.pipeline.run_pipeline", forbidden)

        client.post(f"/api/jobs/{finished_job}/render")
        _wait(context, finished_job)

        job = context.store.get(finished_job)
        assert job is not None and job.state is JobState.SUCCEEDED

    def test_a_job_with_no_plan_cannot_be_rerendered(
        self, client: TestClient, context: ApiContext
    ) -> None:
        job = context.store.create(audio_name="a.wav", options={})
        context.store.submit(job, lambda _job: None)
        assert job.future is not None
        job.future.result(timeout=60)

        assert client.post(f"/api/jobs/{job.id}/render").status_code == 409


class TestResume:
    def test_an_interrupted_job_with_a_plan_resumes_from_it(
        self,
        client: TestClient,
        context: ApiContext,
        finished_job: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Its plan may carry edits; the pipeline must not run over them."""
        calls: list[Path] = []
        _fake_render(monkeypatch, calls)
        job = context.store.get(finished_job)
        assert job is not None
        job.state = JobState.INTERRUPTED

        response = client.post(f"/api/jobs/{finished_job}/resume")
        _wait(context, finished_job)

        assert response.status_code == 202
        assert calls
        assert job.state is JobState.SUCCEEDED

    def test_a_succeeded_job_has_nothing_to_resume(
        self, client: TestClient, finished_job: str
    ) -> None:
        assert client.post(f"/api/jobs/{finished_job}/resume").status_code == 409

    def test_the_snapshot_says_whether_it_can_be_resumed(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        job = context.store.get(finished_job)
        assert job is not None

        assert client.get(f"/api/jobs/{finished_job}").json()["resumable"] is False
        job.state = JobState.INTERRUPTED
        assert client.get(f"/api/jobs/{finished_job}").json()["resumable"] is True


class TestCaptionRoute:
    def test_a_caption_is_corrected(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        response = client.put(
            f"/api/jobs/{finished_job}/scenes/1/caption", json={"text": "a quiet river"}
        )

        assert response.status_code == 200
        saved = _saved_plan(context, finished_job).scenes[1]
        assert saved.caption_text == "a quiet river"
        assert saved.text == "a river"

    def test_a_correction_counts_as_a_pending_edit(
        self, client: TestClient, finished_job: str
    ) -> None:
        client.put(
            f"/api/jobs/{finished_job}/scenes/1/caption", json={"text": "a quiet river"}
        )

        job = client.get(f"/api/jobs/{finished_job}").json()

        assert job["summary"]["pending_edits"] == 1

    def test_an_empty_caption_is_refused(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        response = client.put(
            f"/api/jobs/{finished_job}/scenes/1/caption", json={"text": "  "}
        )

        assert response.status_code == 422
        assert _saved_plan(context, finished_job).scenes[1].caption_text == ""

    def test_a_card_is_refused(self, client: TestClient, finished_job: str) -> None:
        response = client.put(
            f"/api/jobs/{finished_job}/scenes/0/caption", json={"text": "New title"}
        )

        assert response.status_code == 422

    def test_an_oversized_request_is_refused(
        self, client: TestClient, finished_job: str
    ) -> None:
        response = client.put(
            f"/api/jobs/{finished_job}/scenes/1/caption", json={"text": "x" * 5000}
        )

        assert response.status_code == 422


# --- undo and redo (D-182) ---------------------------------------------------


def _history(client: TestClient, job: str) -> dict:  # type: ignore[type-arg]
    response = client.get(f"/api/jobs/{job}/plan/history")
    assert response.status_code == 200
    return response.json()


class TestUndoRedo:
    """Undo and redo through the API, across every kind of edit (D-182)."""

    def test_a_fresh_video_has_nothing_to_undo(self, client: TestClient, finished_job: str) -> None:
        state = _history(client, finished_job)

        assert state == {
            "can_undo": False, "can_redo": False, "pending": 0,
            "undo_label": "", "redo_label": "", "changed_scenes": [], "changed_pictures": [],
            "captions_changed": False,
        }
        assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 409

    def test_edits_of_every_kind_undo_and_redo_in_order(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        job = f"/api/jobs/{finished_job}"
        picture = {"action": "choose", "asset_id": "close"}
        assert client.put(f"{job}/scenes/2/image", json=picture).status_code == 200
        assert client.put(f"{job}/scenes/1/caption", json={"text": "a wide river"}).status_code == 200
        assert client.put(f"{job}/mix", json={"voice_db": 3.0}).status_code == 200

        state = _history(client, finished_job)
        assert state["pending"] == 3 and state["undo_label"] == "the mix"
        assert set(state["changed_scenes"]) == {1, 2}  # the mix changes no scene
        assert state["changed_pictures"] == [2]  # the caption changed no picture

        undone = client.post(f"/api/jobs/{finished_job}/plan/undo").json()
        assert undone["pending"] == 2 and undone["redo_label"] == "the mix"
        assert _saved_plan(context, finished_job).audio_mix.voice_db == 0.0
        client.post(f"/api/jobs/{finished_job}/plan/undo")
        assert _saved_plan(context, finished_job).scenes[1].caption_text != "a wide river"
        client.post(f"/api/jobs/{finished_job}/plan/undo")
        plan = _saved_plan(context, finished_job)
        assert plan.scenes[2].asset is None
        assert _history(client, finished_job)["pending"] == 0
        assert context.store.get(finished_job).summary["pending_edits"] == 0  # type: ignore[union-attr]

        client.post(f"/api/jobs/{finished_job}/plan/redo")
        assert _saved_plan(context, finished_job).scenes[2].asset is not None
        assert _history(client, finished_job)["changed_scenes"] == [2]

    def test_undo_waits_for_a_render_to_finish(
        self, client: TestClient, context: ApiContext, finished_job: str
    ) -> None:
        """The renderer is reading the plan: undo must not change it underneath."""
        import threading

        client.put(f"/api/jobs/{finished_job}/scenes/1/caption", json={"text": "a wide river"})
        release = threading.Event()
        context.store.restart(finished_job, lambda _job: release.wait(5))
        try:
            assert client.post(f"/api/jobs/{finished_job}/plan/undo").status_code == 409
        finally:
            release.set()

    def test_the_version_rendered_counts_as_no_change(
        self, client: TestClient, context: ApiContext, finished_job: str, tmp_path: Path
    ) -> None:
        from voxframe.api.plan_history import PlanHistory

        client.put(f"/api/jobs/{finished_job}/scenes/1/caption", json={"text": "a wide river"})
        plan_path = context.store.artifact_path(finished_job, "plan")
        assert plan_path is not None
        PlanHistory(plan_path).rendered()  # as the re-render route does

        assert _history(client, finished_job)["pending"] == 0
        client.post(f"/api/jobs/{finished_job}/plan/undo")
        assert _history(client, finished_job)["pending"] == 1


class TestVideoRanges:
    """A player seeks by asking for byte ranges; they must be answered (D-182)."""

    def test_a_range_is_answered_with_just_those_bytes(
        self, client: TestClient, finished_job: str
    ) -> None:
        url = f"/api/jobs/{finished_job}/artifacts/video"  # the file holds b"video"

        part = client.get(url, headers={"Range": "bytes=1-3"})
        rest = client.get(url, headers={"Range": "bytes=2-"})
        tail = client.get(url, headers={"Range": "bytes=-2"})

        assert part.status_code == 206 and part.content == b"ide"
        assert part.headers["content-range"] == "bytes 1-3/5"
        assert rest.content == b"deo" and tail.content == b"eo"

    def test_an_impossible_range_is_refused_and_no_range_is_the_whole_file(
        self, client: TestClient, finished_job: str
    ) -> None:
        url = f"/api/jobs/{finished_job}/artifacts/video"

        beyond = client.get(url, headers={"Range": "bytes=10-20"})
        whole = client.get(url)

        assert beyond.status_code == 416 and beyond.headers["content-range"] == "bytes */5"
        assert whole.status_code == 200 and whole.content == b"video"
        assert whole.headers["accept-ranges"] == "bytes"
