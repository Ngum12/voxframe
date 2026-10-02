"""The sound settings, their arithmetic, their checks and their routes (D-171).

The real renders -- loudness reached, pictures reused, frame counts exact --
are in ``tests/integration/test_sound_mix.py``. These pin the decisions.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

soundfile = pytest.importorskip("soundfile")

from voxframe.plan.audio_mix import (  # noqa: E402
    LOUDNESS_TARGETS,
    AudioMix,
    Destination,
)
from voxframe.plan.scene_plan import ScenePlan  # noqa: E402
from voxframe.render.audio import mixdown  # noqa: E402
from voxframe.render.audio.mixdown import BED_DB, MixReport, SpeechSpan, Stems  # noqa: E402


class TestSettings:
    def test_the_defaults_are_what_every_video_had(self) -> None:
        mix = AudioMix()

        assert (mix.voice_db, mix.music_db, mix.speech_margin_db) == (0.0, 0.0, 15.0)
        assert mix.destination is Destination.YOUTUBE

    def test_the_approved_targets(self) -> None:
        """-14 LUFS by default; the owner approved the table as proposed."""
        assert {d.value: (t.lufs, t.true_peak) for d, t in LOUDNESS_TARGETS.items()} == {
            "youtube": (-14.0, -1.0),
            "social": (-14.0, -1.0),
            "whatsapp": (-15.0, -1.5),
            "podcast": (-16.0, -1.0),
        }
        assert AudioMix().target.lufs == -14.0

    @pytest.mark.parametrize("field,value", [("voice_db", 13), ("music_db", -31), ("speech_margin_db", 2)])
    def test_out_of_range_is_refused(self, field: str, value: float) -> None:
        with pytest.raises(ValueError):
            AudioMix(**{field: value})

    def test_close_music_is_flagged_not_changed(self) -> None:
        close = AudioMix(speech_margin_db=9)

        assert close.too_close
        assert close.speech_margin_db == 9

    def test_a_plan_from_before_loads_with_the_defaults(self, tmp_path: Path) -> None:
        plan = {
            "version": 2,
            "audio_path": "a.wav", "audio_sha256": "0" * 64, "audio_duration": 3.0,
            "fps": 30.0, "total_frames": 90,
            "scenes": [{"index": 0, "start_frame": 0, "end_frame": 90, "text": "hello"}],
        }
        path = tmp_path / "old.plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")

        assert ScenePlan.load(path).audio_mix == AudioMix()

    def test_the_mix_is_kept_in_the_plan(self, tmp_path: Path) -> None:
        plan = ScenePlan.model_validate(
            {
                "audio_path": "a.wav", "audio_sha256": "0" * 64, "audio_duration": 3.0,
                "fps": 30.0, "total_frames": 90,
                "scenes": [{"index": 0, "start_frame": 0, "end_frame": 90, "text": "hello"}],
                "audio_mix": {"voice_db": 2, "destination": "podcast"},
            }
        )
        plan.save(tmp_path / "p.json")

        again = ScenePlan.load(tmp_path / "p.json").audio_mix
        assert (again.voice_db, again.destination) == (2.0, Destination.PODCAST)


def _stems(**overrides: object) -> Stems:
    fields: dict[str, object] = {
        "voice": Path("v.wav"), "music": Path("m.wav"),
        "spans": (SpeechSpan(1.0, 4.0, voice_db=-20.0, music_db=-12.0),),
        "landing": 4.0, "video_end": 6.0, "fps": 30.0,
    }
    fields.update(overrides)
    return Stems(**fields)  # type: ignore[arg-type]


class TestTheArithmetic:
    """The music sits exactly the chosen distance under the voice."""

    @pytest.mark.parametrize("margin", [6.0, 15.0, 24.0])
    def test_the_margin_is_what_was_asked(self, margin: float) -> None:
        stems = _stems()

        assert mixdown._min_margin(stems, AudioMix(speech_margin_db=margin)) == pytest.approx(margin)

    def test_music_level_changes_the_pauses_not_the_margin(self) -> None:
        stems = _stems()
        loud = AudioMix(music_db=6.0)

        times, levels = mixdown._music_envelope(stems, loud)

        assert mixdown._min_margin(stems, loud) == pytest.approx(15.0)
        before_speech = float(np.interp(0.5, times, levels))
        assert before_speech == pytest.approx(BED_DB + 6.0)

    def test_a_louder_voice_lets_the_music_rise_with_it(self) -> None:
        stems = _stems()

        plain = mixdown._depths(stems, AudioMix())[0].duck_db
        louder = mixdown._depths(stems, AudioMix(voice_db=6.0))[0].duck_db

        assert louder == pytest.approx(plain + 6.0)

    def test_no_music_no_margin(self) -> None:
        assert mixdown._min_margin(_stems(music=None), AudioMix()) is None


class TestTheReport:
    def _report(self, **overrides: object) -> MixReport:
        fields: dict[str, object] = {
            "destination": "youtube", "target_lufs": -14.0, "target_true_peak": -1.0,
            "integrated_lufs": -14.2, "true_peak": -1.3,
            "min_speech_margin_db": 15.0, "speech_margin_setting_db": 15.0,
        }
        fields.update(overrides)
        return MixReport(**fields)  # type: ignore[arg-type]

    def test_a_good_mix_passes(self) -> None:
        assert self._report().passed

    @pytest.mark.parametrize(
        "overrides,words",
        [
            ({"integrated_lufs": -17.0}, "loudness came out"),
            ({"true_peak": -0.2}, "loudest peak"),
            ({"clipped": True}, "clips"),
            ({"min_speech_margin_db": 9.0}, "only 9.0 dB under the voice"),
            ({"clicks_at": [12.5]}, "click may be heard"),
        ],
    )
    def test_each_failure_is_said_plainly(self, overrides: dict[str, object], words: str) -> None:
        report = self._report(**overrides)

        assert not report.passed
        assert any(words in problem for problem in report.problems)


# --- the routes -------------------------------------------------------------------

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient  # noqa: E402

from voxframe.api.app import ApiContext, create_app  # noqa: E402
from voxframe.api.security import SessionToken  # noqa: E402
from voxframe.config.settings import Settings  # noqa: E402
from voxframe.jobs.store import JobStore  # noqa: E402
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
    """A rendered job, with a plan and a video (and no stems yet)."""
    job = context.store.create(audio_name="a.wav", options={"height": 480})
    directory = context.store.job_directory(job.id)
    plan = ScenePlan.model_validate(
        {
            "audio_path": "a.wav", "audio_sha256": "0" * 64, "audio_duration": 6.0,
            "fps": 30.0, "total_frames": 180, "music_path": "track.mp3",
            "scenes": [{"index": 0, "start_frame": 0, "end_frame": 180, "text": "hello"}],
        }
    )
    plan_path = directory / "source.plan.json"
    plan.save(plan_path)
    video = directory / "source.mp4"
    video.write_bytes(b"video")
    context.store.submit(job, lambda _job: None)
    assert job.future is not None
    job.future.result(timeout=5)
    context.store.record_result(
        job, artifacts={"plan": plan_path, "video": video}, warnings=(), summary={}
    )
    return job.id, video


def _write_stems(video: Path) -> None:
    """Six seconds: a voice (a tone) from 1 s to 4 s, and music throughout."""
    rate = mixdown.RATE
    t = np.arange(6 * rate) / rate
    voice = np.where((t >= 1) & (t < 4), 0.2 * np.sin(2 * np.pi * 220 * t), 0.0).astype(np.float32)
    music = np.stack([0.3 * np.sin(2 * np.pi * 110 * t)] * 2, axis=1).astype(np.float32)
    soundfile.write(video.parent / "voice.wav", voice, rate, subtype="FLOAT")
    soundfile.write(video.parent / "music.wav", music, rate, subtype="FLOAT")
    Stems(
        voice=video.parent / "voice.wav", music=video.parent / "music.wav",
        spans=(SpeechSpan(1.0, 4.0, voice_db=-17.0, music_db=-13.5),),
        landing=4.0, video_end=6.0, fps=30.0, voice_lufs=-20.0,
    ).save(stems_path(video))


class TestRoutes:
    def test_the_settings_and_the_choices(self, client: TestClient, finished: tuple[str, Path]) -> None:
        job_id, _ = finished

        state = client.get(f"/api/jobs/{job_id}/mix").json()

        assert state["audio_mix"]["speech_margin_db"] == 15.0
        assert [d["id"] for d in state["destinations"]] == ["youtube", "social", "whatsapp", "podcast"]
        assert state["has_music"] is True
        assert state["can_preview"] is False

    def test_saving_is_an_edit_that_waits_for_update(
        self, client: TestClient, context: ApiContext, finished: tuple[str, Path]
    ) -> None:
        job_id, _ = finished

        saved = client.put(
            f"/api/jobs/{job_id}/mix",
            json={"voice_db": 2, "music_db": -3, "speech_margin_db": 9, "destination": "podcast"},
        ).json()

        assert saved["pending_edits"] == 1
        assert saved["too_close"] is True
        plan_path = context.store.artifact_path(job_id, "plan")
        assert plan_path is not None
        assert ScenePlan.load(plan_path).audio_mix.destination is Destination.PODCAST

    def test_nonsense_is_refused(self, client: TestClient, finished: tuple[str, Path]) -> None:
        job_id, _ = finished

        response = client.put(f"/api/jobs/{job_id}/mix", json={"speech_margin_db": 99})

        assert response.status_code == 422

    def test_a_preview_needs_the_stems(self, client: TestClient, finished: tuple[str, Path]) -> None:
        job_id, _ = finished

        response = client.post(f"/api/jobs/{job_id}/mix/preview", json={"mix": {}})

        assert response.status_code == 409
        assert "Update this video" in response.json()["detail"]

    def test_a_preview_is_a_few_seconds_of_sound(
        self, client: TestClient, finished: tuple[str, Path], tmp_path: Path
    ) -> None:
        job_id, video = finished
        _write_stems(video)

        full = client.post(f"/api/jobs/{job_id}/mix/preview", json={"mix": {}, "start": 0, "seconds": 5})
        voice = client.post(
            f"/api/jobs/{job_id}/mix/preview", json={"mix": {}, "start": 0, "seconds": 5, "voice_only": True}
        )

        assert full.status_code == voice.status_code == 200
        assert full.headers["content-type"] == "audio/wav"
        (tmp_path / "full.wav").write_bytes(full.content)
        (tmp_path / "voice.wav").write_bytes(voice.content)
        with_music, rate = soundfile.read(tmp_path / "full.wav")
        voice_alone, _ = soundfile.read(tmp_path / "voice.wav")
        assert len(with_music) == pytest.approx(5 * rate, abs=2)
        # Before the voice starts: music in one, silence in the other.
        opening = slice(int(0.2 * rate), int(0.8 * rate))
        assert np.abs(with_music[opening]).max() > 0.01
        assert np.abs(voice_alone[opening]).max() < 1e-4


def test_the_mix_module_imports_on_its_own() -> None:
    """It once imported the plan package, which imports the renderer, which
    imports it: fine when the app loaded the renderer first, a crash when
    anything imported this module first."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import voxframe.render.audio.mixdown"],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr[-800:]
