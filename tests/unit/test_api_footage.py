"""The routes for showing the speaker (D-192): upload, render request, edits, thumbnails."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient

from voxframe.api.app import ApiContext, RenderRequest, _job_options, create_app
from voxframe.api.security import SessionToken
from voxframe.config.settings import Settings
from voxframe.jobs.store import JobStore
from voxframe.plan.scene_plan import (
    Footage,
    MotionKind,
    PlanAsset,
    PlannedScene,
    ScenePlan,
    Shot,
)
from voxframe.plan.shots import attach_footage

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


def _ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if binary is None:
        pytest.skip("FFmpeg not available")
    return binary


def _video(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [_ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=4",
         "-f", "lavfi", "-i", "sine=d=4", "-shortest", "-y", str(path)],
        check=True,
    )
    return path


def _plan(recording: Path, library: Path) -> ScenePlan:
    picture = library / "river.jpg"
    picture.write_bytes(b"not really a jpeg")
    asset = PlanAsset(
        id="river", path=str(picture), width=1600, height=900,
        license_name="CC0", license_author="Someone", license_source="Test",
    )
    plan = ScenePlan(
        audio_path=str(recording), audio_sha256="0" * 64, audio_duration=4.0,
        fps=30.0, total_frames=120,
        scenes=(
            PlannedScene(index=0, start_frame=0, end_frame=60, text="hello"),
            PlannedScene(
                index=1, start_frame=60, end_frame=120, text="a river", asset=asset,
                match_score=0.9,
            ),
        ),
    )
    footage = Footage(path=str(recording), width=320, height=180, fps=30.0, duration=4.0)
    return attach_footage(plan, footage, cutaways=False)


def _finished_job(context: ApiContext, plan: ScenePlan) -> str:
    job = context.store.create(audio_name="talk.mp4", options={"height": 480})
    directory = context.store.job_directory(job.id)
    plan_path = plan.save(directory / "source.plan.json")
    video = directory / "source.mp4"
    video.write_bytes(b"video")
    context.store.submit(job, lambda _job: None)
    assert job.future is not None
    job.future.result(timeout=5)
    context.store.record_result(
        job, artifacts={"plan": plan_path, "video": video}, warnings=(), summary={}
    )
    return job.id


def _saved(context: ApiContext, job_id: str) -> ScenePlan:
    path = context.store.artifact_path(job_id, "plan")
    assert path is not None
    return ScenePlan.load(path)


class TestUploading:
    def test_a_video_says_it_has_a_picture(self, client: TestClient, tmp_path: Path) -> None:
        video = _video(tmp_path / "talk.mp4")
        with video.open("rb") as handle:
            response = client.post("/api/uploads", files={"file": ("talk.mp4", handle)})
        assert response.status_code == 200
        assert response.json()["has_video"] is True

    def test_a_sound_file_does_not(self, client: TestClient, tmp_path: Path) -> None:
        sound = tmp_path / "talk.wav"
        subprocess.run(
            [_ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "sine=d=1", "-y", str(sound)],
            check=True,
        )
        with sound.open("rb") as handle:
            response = client.post("/api/uploads", files={"file": ("talk.wav", handle)})
        assert response.json()["has_video"] is False


class TestTheRequest:
    def test_use_video_reaches_the_pipeline(self, context: ApiContext, tmp_path: Path) -> None:
        recording = tmp_path / "source.mp4"
        recording.write_bytes(b"x")
        request = RenderRequest(upload_id="u1", use_video=True, use_library=False)
        assert _job_options(request, recording, context).footage is True

    def test_it_is_off_unless_asked(self, context: ApiContext, tmp_path: Path) -> None:
        recording = tmp_path / "source.mp4"
        recording.write_bytes(b"x")
        request = RenderRequest(upload_id="u2", use_library=False)
        assert _job_options(request, recording, context).footage is False


class TestChoosingTheShot:
    def test_a_scene_can_cut_to_its_picture(
        self, client: TestClient, context: ApiContext, tmp_path: Path
    ) -> None:
        recording = context.store.root / "uploads" / "r" / "source.mp4"
        recording.parent.mkdir(parents=True)
        recording.write_bytes(b"x")
        job = _finished_job(context, _plan(recording, context.settings.library_path))

        response = client.put(f"/api/jobs/{job}/scenes/1/shot", json={"shot": "picture"})

        assert response.status_code == 200
        scene = _saved(context, job).scenes[1]
        assert scene.shot is Shot.PICTURE and scene.shot_source == "user"
        assert client.get(f"/api/jobs/{job}").json()["summary"]["pending_edits"] == 1

    def test_and_back_to_the_speaker(self, client: TestClient, context: ApiContext) -> None:
        recording = context.store.root / "uploads" / "r" / "source.mp4"
        recording.parent.mkdir(parents=True)
        recording.write_bytes(b"x")
        job = _finished_job(context, _plan(recording, context.settings.library_path))
        client.put(f"/api/jobs/{job}/scenes/1/shot", json={"shot": "picture"})

        client.put(f"/api/jobs/{job}/scenes/1/shot", json={"shot": "speaker"})

        assert _saved(context, job).scenes[1].shot is Shot.SPEAKER

    def test_a_sound_only_video_says_why_not(
        self, client: TestClient, context: ApiContext
    ) -> None:
        plan = ScenePlan(
            audio_path="a.wav", audio_sha256="0" * 64, audio_duration=2.0, fps=30.0,
            total_frames=60,
            scenes=(PlannedScene(index=0, start_frame=0, end_frame=60, text="hi"),),
        )
        job = _finished_job(context, plan)

        response = client.put(f"/api/jobs/{job}/scenes/0/shot", json={"shot": "speaker"})

        assert response.status_code == 422
        assert "sound only" in response.json()["detail"]

    def test_a_card_says_why_not(self, client: TestClient, context: ApiContext) -> None:
        from voxframe.plan.builder import insert_cards

        recording = context.store.root / "uploads" / "r" / "source.mp4"
        recording.parent.mkdir(parents=True)
        recording.write_bytes(b"x")
        plan = insert_cards(
            _plan(recording, context.settings.library_path), title="T", chapters=False
        )
        job = _finished_job(context, plan)

        response = client.put(f"/api/jobs/{job}/scenes/0/shot", json={"shot": "speaker"})

        assert response.status_code == 422
        assert "card" in response.json()["detail"]

    def test_only_two_shots_exist(self, client: TestClient, context: ApiContext) -> None:
        recording = context.store.root / "uploads" / "r" / "source.mp4"
        recording.parent.mkdir(parents=True)
        recording.write_bytes(b"x")
        job = _finished_job(context, _plan(recording, context.settings.library_path))
        response = client.put(f"/api/jobs/{job}/scenes/1/shot", json={"shot": "/etc/passwd"})
        assert response.status_code == 422


class TestChoosingAPictureShowsIt:
    def test_a_chosen_image_replaces_the_speaker_on_screen(self) -> None:
        from voxframe.plan.editing import choose_image

        footage = Footage(path="t.mp4", width=320, height=180, fps=30.0, duration=4.0)
        other = PlanAsset(
            id="other", path="o.jpg", width=10, height=10, license_name="CC0",
            license_author="a", license_source="b", similarity=0.3,
        )
        plan = ScenePlan(
            audio_path="t.mp4", audio_sha256="0" * 64, audio_duration=4.0, fps=30.0,
            total_frames=120,
            scenes=(
                PlannedScene(index=0, start_frame=0, end_frame=60, text="a"),
                PlannedScene(
                    index=1, start_frame=60, end_frame=120, text="b",
                    alternatives=(other,), motion=MotionKind.NONE,
                ),
            ),
        )
        plan = attach_footage(plan, footage, cutaways=False)
        assert plan.scenes[1].shot is Shot.SPEAKER

        edited = choose_image(plan, 1, "other")

        assert edited.scenes[1].shot is Shot.PICTURE
        assert edited.scenes[1].shot_source == "user"


class TestThumbnails:
    def test_a_speaker_scene_shows_the_speaker(
        self, client: TestClient, context: ApiContext
    ) -> None:
        recording = _video(context.store.root / "uploads" / "r" / "source.mp4")
        job = _finished_job(context, _plan(recording, context.settings.library_path))

        response = client.get(f"/api/jobs/{job}/scenes/0/thumbnail")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content[:2] == b"\xff\xd8"

    def test_a_recording_outside_the_sandbox_is_refused(
        self, client: TestClient, context: ApiContext, tmp_path: Path
    ) -> None:
        outside = _video(tmp_path / "elsewhere.mp4")
        job = _finished_job(context, _plan(outside, context.settings.library_path))

        response = client.get(f"/api/jobs/{job}/scenes/0/thumbnail")

        assert response.status_code == 403
