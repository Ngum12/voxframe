"""A track of the person's own, added, heard, switched and removed after the video is made (D-184)."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest

soundfile = pytest.importorskip("soundfile")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from voxframe.api.app import ApiContext, create_app  # noqa: E402
from voxframe.api.security import SessionToken  # noqa: E402
from voxframe.config.settings import Settings  # noqa: E402
from voxframe.jobs.store import JobStore  # noqa: E402
from voxframe.plan.scene_plan import ScenePlan  # noqa: E402
from voxframe.render.audio import mixdown  # noqa: E402
from voxframe.render.audio.mixdown import SpeechSpan, Stems  # noqa: E402
from voxframe.render.compose.from_plan import stems_path  # noqa: E402

LOOPBACK = "http://127.0.0.1:8765"


@pytest.fixture
def context(tmp_path: Path) -> ApiContext:
    root = tmp_path / "web"
    return ApiContext(
        settings=Settings(cache_path=tmp_path / "cache", output_path=tmp_path / "out"),
        store=JobStore(root, reap_interval=None),
        token=SessionToken("t"),
        allowed_paths=(root.resolve(),),
    )


@pytest.fixture
def client(context: ApiContext) -> TestClient:
    test_client = TestClient(create_app(context), base_url=LOOPBACK)
    test_client.headers.update({"x-voxframe-token": "t"})
    return test_client


@pytest.fixture
def finished(context: ApiContext) -> tuple[str, Path]:
    """A video made without music, with its sound stems kept."""
    job = context.store.create(audio_name="a.wav", options={"height": 480})
    directory = context.store.job_directory(job.id)
    plan = ScenePlan.model_validate(
        {
            "audio_path": "a.wav", "audio_sha256": "0" * 64, "audio_duration": 6.0,
            "fps": 30.0, "total_frames": 180,
            "scenes": [{"index": 0, "start_frame": 0, "end_frame": 180, "text": "hello"}],
        }
    )
    plan_path = directory / "source.plan.json"
    plan.save(plan_path)
    video = directory / "source.mp4"
    video.write_bytes(b"video")
    context.store.submit(job, lambda _job: None)
    assert job.future is not None
    job.future.result(timeout=60)
    context.store.record_result(
        job, artifacts={"plan": plan_path, "video": video}, warnings=(), summary={}
    )
    rate = mixdown.RATE
    t = np.arange(6 * rate) / rate
    voice = np.where((t >= 1) & (t < 4), 0.2 * np.sin(2 * np.pi * 220 * t), 0.0).astype(np.float32)
    soundfile.write(directory / "voice.wav", voice, rate, subtype="FLOAT")
    Stems(
        voice=directory / "voice.wav", music=None,
        spans=(SpeechSpan(1.0, 4.0, voice_db=-17.0, music_db=-90.0),),
        landing=4.0, video_end=6.0, fps=30.0, voice_lufs=-20.0,
    ).save(stems_path(video))
    return job.id, plan_path


def _upload_track(client: TestClient, name: str = "My Song.wav") -> str:
    rate = 44100
    t = np.arange(3 * rate) / rate
    tone = np.stack([0.3 * np.sin(2 * np.pi * 660 * t)] * 2, axis=1).astype(np.float32)
    data = io.BytesIO()
    soundfile.write(data, tone, rate, format="WAV", subtype="PCM_16")
    response = client.post("/api/uploads", files={"file": (name, data.getvalue(), "audio/wav")})
    assert response.status_code == 200, response.text
    return str(response.json()["upload_id"])


class TestChoosingATrack:
    def test_a_track_uploaded_after_the_video_becomes_its_music(
        self, client: TestClient, finished: tuple[str, Path]
    ) -> None:
        job_id, plan_path = finished
        upload = _upload_track(client)

        state = client.put(
            f"/api/jobs/{job_id}/music",
            json={"choice": "own", "upload_id": upload, "credit": "  Song by   Someone "},
        ).json()

        plan = ScenePlan.load(plan_path)
        assert Path(plan.music_path).name == "My Song.wav"  # credited by its own name
        assert plan.music_credit == "Song by Someone"
        assert plan.score is None
        assert state["music"]["choice"] == "own"
        assert state["music"]["track_name"] == "My Song.wav"
        assert state["music"]["track_credit"] == "Song by Someone"
        assert state["pending_edits"] == 1

    def test_switching_away_and_back_keeps_the_track(
        self, client: TestClient, finished: tuple[str, Path]
    ) -> None:
        job_id, plan_path = finished
        client.put(f"/api/jobs/{job_id}/music", json={"choice": "own", "upload_id": _upload_track(client)})

        none = client.put(f"/api/jobs/{job_id}/music", json={"choice": "none"}).json()
        back = client.put(f"/api/jobs/{job_id}/music", json={"choice": "own"}).json()

        assert none["music"]["choice"] == "none"
        assert none["music"]["track_name"] == "My Song.wav"  # still offered
        assert back["music"]["choice"] == "own"
        assert Path(ScenePlan.load(plan_path).music_path).is_file()

    def test_removing_the_track_stops_offering_it(
        self, client: TestClient, finished: tuple[str, Path]
    ) -> None:
        job_id, _ = finished
        client.put(f"/api/jobs/{job_id}/music", json={"choice": "own", "upload_id": _upload_track(client)})

        removed = client.put(f"/api/jobs/{job_id}/music", json={"choice": "none", "forget_track": True})
        back = client.put(f"/api/jobs/{job_id}/music", json={"choice": "own"})

        assert removed.json()["music"]["track_name"] is None
        assert back.status_code == 409

    def test_using_and_removing_at_once_is_refused(
        self, client: TestClient, finished: tuple[str, Path]
    ) -> None:
        job_id, _ = finished

        response = client.put(
            f"/api/jobs/{job_id}/music",
            json={"choice": "own", "upload_id": _upload_track(client), "forget_track": True},
        )

        assert response.status_code == 422

    @pytest.mark.parametrize("upload_id", ["nothing-here", "../../etc"])
    def test_an_unknown_upload_is_refused(
        self, client: TestClient, finished: tuple[str, Path], upload_id: str
    ) -> None:
        job_id, _ = finished

        response = client.put(f"/api/jobs/{job_id}/music", json={"choice": "own", "upload_id": upload_id})

        assert response.status_code in (400, 404)

    def test_undo_goes_back_to_no_music(self, client: TestClient, finished: tuple[str, Path]) -> None:
        job_id, plan_path = finished
        client.put(f"/api/jobs/{job_id}/music", json={"choice": "own", "upload_id": _upload_track(client)})

        client.post(f"/api/jobs/{job_id}/plan/undo")

        assert ScenePlan.load(plan_path).music_path == ""


def _loudest_hz(signal: np.ndarray, rate: int) -> float:
    mono = signal if signal.ndim == 1 else signal.mean(axis=1)
    spectrum = np.abs(np.fft.rfft(mono))
    return float(np.fft.rfftfreq(len(mono), 1 / rate)[int(np.argmax(spectrum))])


class TestHearingATrack:
    def test_a_preview_plays_the_track_before_it_is_chosen(
        self, client: TestClient, finished: tuple[str, Path], tmp_path: Path
    ) -> None:
        job_id, plan_path = finished
        upload = _upload_track(client)

        response = client.post(
            f"/api/jobs/{job_id}/mix/preview",
            json={"mix": {}, "start": 0, "seconds": 5, "music_upload_id": upload},
        )

        assert response.status_code == 200, response.text
        (tmp_path / "heard.wav").write_bytes(response.content)
        heard, rate = soundfile.read(tmp_path / "heard.wav")
        # Before the voice: the track, and nothing was chosen.
        assert _loudest_hz(heard[int(0.2 * rate) : int(0.9 * rate)], rate) == pytest.approx(660, abs=5)
        assert ScenePlan.load(plan_path).music_path == ""

    def test_the_track_sits_under_the_voice(
        self, client: TestClient, finished: tuple[str, Path], tmp_path: Path
    ) -> None:
        job_id, _ = finished

        response = client.post(
            f"/api/jobs/{job_id}/mix/preview",
            json={"mix": {}, "start": 0, "seconds": 5, "music_upload_id": _upload_track(client)},
        )

        (tmp_path / "heard.wav").write_bytes(response.content)
        heard, rate = soundfile.read(tmp_path / "heard.wav")
        assert _loudest_hz(heard[int(1.5 * rate) : int(3.5 * rate)], rate) == pytest.approx(220, abs=5)

    def test_hearing_the_kept_track_needs_one(self, client: TestClient, finished: tuple[str, Path]) -> None:
        job_id, _ = finished

        response = client.post(
            f"/api/jobs/{job_id}/mix/preview", json={"mix": {}, "kept_track": True}
        )

        assert response.status_code == 409

    def test_the_kept_track_can_be_heard_after_switching_away(
        self, client: TestClient, finished: tuple[str, Path]
    ) -> None:
        job_id, _ = finished
        client.put(f"/api/jobs/{job_id}/music", json={"choice": "own", "upload_id": _upload_track(client)})
        client.put(f"/api/jobs/{job_id}/music", json={"choice": "none"})

        response = client.post(
            f"/api/jobs/{job_id}/mix/preview", json={"mix": {}, "seconds": 3, "kept_track": True}
        )

        assert response.status_code == 200

    def test_a_file_that_is_not_sound_is_said_plainly(
        self, client: TestClient, finished: tuple[str, Path]
    ) -> None:
        job_id, _ = finished
        upload = client.post(
            "/api/uploads", files={"file": ("fake.mp3", b"not sound at all", "audio/mpeg")}
        ).json()["upload_id"]

        response = client.post(
            f"/api/jobs/{job_id}/mix/preview", json={"mix": {}, "music_upload_id": upload}
        )

        assert response.status_code == 422
        assert "could not be played" in response.json()["detail"]


class TestTheCredit:
    def test_the_credit_can_be_changed_and_is_kept(
        self, client: TestClient, finished: tuple[str, Path]
    ) -> None:
        job_id, plan_path = finished
        client.put(
            f"/api/jobs/{job_id}/music",
            json={"choice": "own", "upload_id": _upload_track(client), "credit": "First"},
        )

        client.put(f"/api/jobs/{job_id}/music", json={"choice": "own", "credit": "Second"})
        client.put(f"/api/jobs/{job_id}/music", json={"choice": "none"})
        back = client.put(f"/api/jobs/{job_id}/music", json={"choice": "own"}).json()

        assert back["music"]["track_credit"] == "Second"
        assert ScenePlan.load(plan_path).music_credit == "Second"


def test_the_track_the_plan_has_can_be_heard(client: TestClient, finished: tuple[str, Path]) -> None:
    """Chosen, not yet applied: the plan has it, the kept stems do not."""
    job_id, _ = finished
    client.put(f"/api/jobs/{job_id}/music", json={"choice": "own", "upload_id": _upload_track(client)})

    response = client.post(f"/api/jobs/{job_id}/mix/preview", json={"mix": {}, "seconds": 3, "kept_track": True})

    assert response.status_code == 200
