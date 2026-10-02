"""A video file through the whole pipeline with "Use my video" on (D-192).

Only transcription is replaced, by words at known times: everything after it
-- reading the footage, finding the speaker, choosing shots, cards, rendering,
the sound -- is the real thing, and the finished video is checked for the
clapper flashes landing on their tones.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.test_footage_sync import CLAPS, TOLERANCE, _flashes, _recording, _tones
from voxframe.config.settings import AspectRatio, QualityPreset, Settings
from voxframe.config.style import get_template
from voxframe.jobs.pipeline import JobOptions, run_pipeline
from voxframe.models.transcript import Transcript, Word
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import run_ffmpeg

pytestmark = [pytest.mark.slow, pytest.mark.needs_ffmpeg]

SENTENCES = (
    "The winter morning was bright and cold.",
    "We walked along the river to the old mill.",
    "Snow covered every roof in the village.",
    "By noon the sun had melted the paths.",
)


def _transcript(duration: float) -> Transcript:
    """The sentences spread across the recording, a short pause between each."""
    words: list[Word] = []
    span = duration / len(SENTENCES)
    for number, sentence in enumerate(SENTENCES):
        tokens = sentence.split()
        start = number * span + 0.1
        step = (span - 0.6) / len(tokens)
        for position, token in enumerate(tokens):
            at = start + position * step
            words.append(Word(text=token, start=round(at, 3), end=round(at + step * 0.85, 3)))
    return Transcript(
        words=tuple(words), language="en", duration=duration, model_id="fake",
        audio_sha256="0" * 64,
    )


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


@pytest.fixture
def fake_transcription(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTranscriber:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def transcribe(self, audio: Path, *, force: bool = False) -> Transcript:
            return _transcript(7.0)

    monkeypatch.setattr("voxframe.transcribe.whisper.Transcriber", FakeTranscriber)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        library_path=tmp_path / "library", cache_path=tmp_path / "cache",
        output_path=tmp_path / "out",
    )


@pytest.mark.usefixtures("fake_transcription")
class TestUseMyVideo:
    def test_a_vertical_video_with_a_title_shows_the_speaker_in_sync(
        self, caps, tmp_path: Path  # type: ignore[no-untyped-def]
    ) -> None:
        recording = _recording(caps, tmp_path / "talk.mp4")
        outcome = run_pipeline(
            JobOptions(
                audio=recording, output=tmp_path / "talk-out.mp4", aspect=AspectRatio.VERTICAL,
                quality=QualityPreset.DRAFT, height=480, title="A Winter Walk",
                chapters=False, footage=True,
            ),
            _settings(tmp_path), get_template("documentary"), caps,
        )

        plan = outcome.plan
        assert plan is not None and plan.footage is not None
        spoken = [scene for scene in plan.scenes if not scene.is_card]
        assert spoken and all(plan.shows_speaker(scene) for scene in spoken)
        assert outcome.result.width < outcome.result.height
        # The speaker is on screen, so nothing says scenes are plain.
        assert not any("plain background" in warning for warning in outcome.warnings)
        assert outcome.fill_rate == 1.0

        # The title card holds the picture and the speech for 3s.
        title = plan.scenes[0].duration_frames / plan.fps
        flashes, tones = _flashes(caps, outcome.result.video_path), _tones(caps, outcome.result.video_path)
        assert len(flashes) == len(CLAPS)
        for flash, tone, clap in zip(flashes, tones, CLAPS, strict=False):
            assert flash == pytest.approx(clap + title, abs=TOLERANCE)
            assert flash == pytest.approx(tone, abs=TOLERANCE)

        # The plan on disk is the one that was rendered, and re-loads.
        assert outcome.plan_path is not None
        assert ScenePlan.load(outcome.plan_path) == plan

    def test_a_sound_file_says_it_has_no_picture(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        sound = tmp_path / "talk.wav"
        run_ffmpeg(
            caps.ffmpeg_path,
            ["-loglevel", "error", "-f", "lavfi", "-i", "sine=d=7", "-y", str(sound)],
        )
        outcome = run_pipeline(
            JobOptions(
                audio=sound, output=tmp_path / "out.mp4", quality=QualityPreset.DRAFT,
                height=240, chapters=False, footage=True,
            ),
            _settings(tmp_path), get_template(), caps,
        )

        assert outcome.plan is not None and outcome.plan.footage is None
        assert any("no picture to show" in warning for warning in outcome.warnings)

    def test_off_by_default(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        recording = _recording(caps, tmp_path / "talk.mp4")
        outcome = run_pipeline(
            JobOptions(
                audio=recording, output=tmp_path / "out.mp4", quality=QualityPreset.DRAFT,
                height=240, chapters=False,
            ),
            _settings(tmp_path), get_template(), caps,
        )

        assert outcome.plan is not None and outcome.plan.footage is None
        assert _flashes(caps, outcome.result.video_path) == []
