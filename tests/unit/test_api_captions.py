"""The routes for captions in the studio (D-196): the live document and its choices."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient

from voxframe.api.app import ApiContext, create_app
from voxframe.api.security import SessionToken
from voxframe.config.settings import Settings
from voxframe.config.style import CaptionAnimation
from voxframe.jobs.store import JobStore
from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan

LOOPBACK = "http://127.0.0.1:8765"


@pytest.fixture
def context(tmp_path: Path) -> ApiContext:
    root = tmp_path / "web"
    library = tmp_path / "library"
    library.mkdir()
    return ApiContext(
        settings=Settings(
            library_path=library, cache_path=tmp_path / "cache", output_path=tmp_path / "out"
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


def _plan() -> ScenePlan:
    words = (
        PlanWord(text="Floods", start=0.3, end=0.7),
        PlanWord(text="hit", start=0.75, end=0.95),
        PlanWord(text="Douala", start=1.0, end=1.5),
    )
    return ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64, audio_duration=4.0, fps=30.0,
        total_frames=120,
        scenes=(
            PlannedScene(index=0, start_frame=0, end_frame=60, text="Floods hit Douala", words=words),
            PlannedScene(index=1, start_frame=60, end_frame=120, card_kind="chapter",
                         card_text="Next"),
        ),
    )


def _job(context: ApiContext, *, made: bool = True) -> str:
    job = context.store.create(audio_name="talk.mp4", options={"height": 1920})
    directory = context.store.job_directory(job.id)
    plan_path = _plan().save(directory / "source.plan.json")
    video = directory / "source.mp4"
    video.write_bytes(b"video")
    context.store.submit(job, lambda _job: None)
    assert job.future is not None
    job.future.result(timeout=60)
    summary = {"width": 1080, "height": 1920} if made else {}
    context.store.record_result(
        job, artifacts={"plan": plan_path, "video": video}, warnings=(), summary=summary
    )
    return job.id


def _saved(context: ApiContext, job_id: str) -> ScenePlan:
    path = context.store.artifact_path(job_id, "plan")
    assert path is not None
    return ScenePlan.load(path)


class TestTheLiveDocument:
    def test_it_is_drawn_at_the_videos_size(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        response = client.get(f"/api/jobs/{job}/captions")
        assert response.status_code == 200
        assert "PlayResX: 1080" in response.text and "PlayResY: 1920" in response.text
        assert "Floods" in response.text
        assert response.headers["cache-control"] == "no-store"

    def test_it_follows_each_change_at_once(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        before = client.get(f"/api/jobs/{job}/captions").text
        client.put(f"/api/jobs/{job}/captions", json={"animation": "pop", "anchor_y": 0.4})
        after = client.get(f"/api/jobs/{job}/captions").text
        assert before != after
        assert "VoxframeWord" in after

    def test_none_before_the_video_is_made(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context, made=False)
        assert client.get(f"/api/jobs/{job}/captions").status_code == 404


class TestChoosing:
    def test_the_whole_videos_captions(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        response = client.put(
            f"/api/jobs/{job}/captions",
            json={"animation": "spotlight", "transition": "slide", "size": 1.2},
        )
        assert response.status_code == 200
        saved = _saved(context, job).captions
        assert saved.animation is CaptionAnimation.SPOTLIGHT
        assert saved.size == 1.2
        style = client.get(f"/api/jobs/{job}/captions/style").json()
        assert style["effective"]["animation"] == "spotlight"
        assert style["template"]["animation"] == "highlight"

    def test_an_unknown_style_is_refused(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        response = client.put(f"/api/jobs/{job}/captions", json={"animation": "wobble"})
        assert response.status_code == 422

    def test_a_placement_off_the_frame_is_refused(
        self, client: TestClient, context: ApiContext
    ) -> None:
        job = _job(context)
        assert client.put(f"/api/jobs/{job}/captions", json={"anchor_y": 2}).status_code == 422

    def test_one_scenes_own_style_and_emphasis(
        self, client: TestClient, context: ApiContext
    ) -> None:
        job = _job(context)
        response = client.put(
            f"/api/jobs/{job}/scenes/0/captions", json={"animation": "bounce", "emphasis": [2]}
        )
        assert response.status_code == 200
        scene = _saved(context, job).scenes[0]
        assert scene.caption_animation is CaptionAnimation.BOUNCE
        assert scene.emphasis == (2,)

    def test_a_word_not_in_the_scene_is_refused(
        self, client: TestClient, context: ApiContext
    ) -> None:
        job = _job(context)
        response = client.put(f"/api/jobs/{job}/scenes/0/captions", json={"emphasis": [9]})
        assert response.status_code == 422

    def test_a_card_has_no_captions(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        response = client.put(f"/api/jobs/{job}/scenes/1/captions", json={"animation": "pop"})
        assert response.status_code == 422

    def test_correcting_a_caption_clears_its_emphasis(
        self, client: TestClient, context: ApiContext
    ) -> None:
        job = _job(context)
        client.put(f"/api/jobs/{job}/scenes/0/captions", json={"emphasis": [2]})
        client.put(f"/api/jobs/{job}/scenes/0/caption", json={"text": "Floods struck Douala"})
        assert _saved(context, job).scenes[0].emphasis == ()

    def test_a_choice_can_be_undone(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        client.put(f"/api/jobs/{job}/captions", json={"animation": "pop"})
        assert client.post(f"/api/jobs/{job}/plan/undo").status_code == 200
        assert _saved(context, job).captions.animation is None
