"""A vertical video follows the speaker's face, measured in the finished video (D-193).

The recordings are made here, from a public-domain portrait (tests/fixtures):
the face stands, walks across the frame, and stands again. The finished
vertical video is then searched for the face with the same detector, and it
must be in every frame examined, near the middle.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tests.integration.test_footage_pipeline import _transcript
from voxframe.config.settings import AspectRatio, QualityPreset, Settings
from voxframe.config.style import get_template
from voxframe.jobs.pipeline import JobOptions, PipelineOutcome, run_pipeline
from voxframe.models.transcript import Transcript
from voxframe.render.captions.ass import TOP_STYLE
from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import run_ffmpeg

pytestmark = [pytest.mark.slow, pytest.mark.needs_ffmpeg]

cv2 = pytest.importorskip("cv2", reason="OpenCV is needed to find faces")

FACE = Path(__file__).resolve().parents[1] / "fixtures" / "astronaut_collins.jpg"


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


@pytest.fixture(autouse=True)
def fake_transcription(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTranscriber:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def transcribe(self, audio: Path, *, force: bool = False) -> Transcript:
            return _transcript(10.0)

    monkeypatch.setattr("voxframe.transcribe.whisper.Transcriber", FakeTranscriber)


def _recording(caps, path: Path, x: str, y: str, size: int = 300) -> Path:  # type: ignore[no-untyped-def]
    """Ten seconds of 1280x720: the portrait at ``x``, ``y`` (FFmpeg expressions of t)."""
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-f", "lavfi", "-i", "color=0x3a4a5a:s=1280x720:r=30:d=10",
            "-loop", "1", "-i", str(FACE),
            "-f", "lavfi", "-i", "sine=f=220:d=10",
            "-filter_complex",
            f"[1:v]scale={size}:-2,format=yuv420p[p];[0:v][p]overlay=x='{x}':y='{y}':shortest=1[v]",
            "-map", "[v]", "-map", "2:a", "-t", "10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-y", str(path),
        ],
    )
    return path


def _make(caps, recording: Path, tmp_path: Path) -> PipelineOutcome:  # type: ignore[no-untyped-def]
    return run_pipeline(
        JobOptions(
            audio=recording, output=tmp_path / "short.mp4", aspect=AspectRatio.VERTICAL,
            quality=QualityPreset.DRAFT, height=640, chapters=False, footage=True,
        ),
        Settings(
            library_path=tmp_path / "library", cache_path=tmp_path / "cache",
            output_path=tmp_path / "out",
        ),
        get_template("clean-educational"),
        caps,
    )


def _faces_across(caps, video: Path, width: int, height: int) -> list[float | None]:  # type: ignore[no-untyped-def]
    """The face's centre across the finished frame, four times a second."""
    from voxframe.render.motion.faces import MODEL_PATH

    raw = video.with_suffix(".bgr")
    run_ffmpeg(
        caps.ffmpeg_path,
        ["-loglevel", "error", "-i", str(video), "-vf", "fps=4,format=bgr24",
         "-f", "rawvideo", "-y", str(raw)],
    )
    frames = np.fromfile(raw, dtype=np.uint8).reshape(-1, height, width, 3)
    detector = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (width, height), 0.6)
    found: list[float | None] = []
    for frame in frames:
        _, faces = detector.detect(frame)
        found.append(None if faces is None else float((faces[0][0] + faces[0][2] / 2) / width))
    return found


class TestTheCameraFollowsTheFace:
    def test_a_speaker_who_walks_across_stays_in_the_middle(
        self, caps, tmp_path: Path  # type: ignore[no-untyped-def]
    ) -> None:
        # Stands at the left, walks to the right over two seconds, stands.
        recording = _recording(
            caps, tmp_path / "walk.mp4", x="if(lt(t,3),100,if(lt(t,5),100+(t-3)/2*800,900))", y="180"
        )
        outcome = _make(caps, recording, tmp_path)

        footage = outcome.plan.footage  # type: ignore[union-attr]
        assert footage is not None and footage.subject_source == "faces"
        assert len(footage.track) >= 3
        positions = _faces_across(caps, outcome.result.video_path, outcome.result.width, outcome.result.height)
        assert all(x is not None for x in positions), positions
        assert max(abs(x - 0.5) for x in positions if x is not None) < 0.2, positions
        # Before and after the walk, it is centred.
        assert abs(positions[4] - 0.5) < 0.05 and abs(positions[-4] - 0.5) < 0.05  # type: ignore[operator]

    def test_a_still_speaker_off_to_one_side_is_centred(
        self, caps, tmp_path: Path  # type: ignore[no-untyped-def]
    ) -> None:
        recording = _recording(caps, tmp_path / "side.mp4", x="900", y="200")
        outcome = _make(caps, recording, tmp_path)

        positions = _faces_across(caps, outcome.result.video_path, outcome.result.width, outcome.result.height)
        assert all(x is not None and abs(x - 0.5) < 0.05 for x in positions), positions


class TestCaptionsKeepClearOfTheFace:
    def test_a_face_low_in_the_frame_sends_the_captions_up(
        self, caps, tmp_path: Path  # type: ignore[no-untyped-def]
    ) -> None:
        recording = _recording(caps, tmp_path / "low.mp4", x="430", y="440", size=420)
        outcome = _make(caps, recording, tmp_path)

        assert outcome.result.ass_path is not None
        events = [
            line for line in outcome.result.ass_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("Dialogue:")
        ]
        assert events and all(f",{TOP_STYLE}," in line for line in events)

    def test_a_face_in_the_usual_place_keeps_them_down(
        self, caps, tmp_path: Path  # type: ignore[no-untyped-def]
    ) -> None:
        recording = _recording(caps, tmp_path / "usual.mp4", x="430", y="120")
        outcome = _make(caps, recording, tmp_path)

        assert outcome.result.ass_path is not None
        assert f",{TOP_STYLE}," not in outcome.result.ass_path.read_text(encoding="utf-8")


class TestWithoutAFace:
    def test_no_face_falls_back_to_where_the_picture_moves(
        self, caps, tmp_path: Path  # type: ignore[no-untyped-def]
    ) -> None:
        recording = tmp_path / "noface.mp4"
        run_ffmpeg(
            caps.ffmpeg_path,
            ["-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=10",
             "-f", "lavfi", "-i", "sine=d=10", "-shortest", "-pix_fmt", "yuv420p",
             "-y", str(recording)],
        )
        outcome = _make(caps, recording, tmp_path)

        footage = outcome.plan.footage  # type: ignore[union-attr]
        assert footage is not None and footage.track == ()
        assert footage.subject_source in {"motion", "centre"}
        assert outcome.result.frame_count == outcome.plan.total_frames  # type: ignore[union-attr]

    def test_without_the_detector_the_video_is_still_made(
        self, caps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
    ) -> None:
        monkeypatch.setattr("voxframe.render.motion.faces.detector_available", lambda: False)
        recording = _recording(caps, tmp_path / "side.mp4", x="900", y="200")
        outcome = _make(caps, recording, tmp_path)

        footage = outcome.plan.footage  # type: ignore[union-attr]
        assert footage is not None and footage.track == ()
        assert footage.subject_source in {"motion", "centre"}
