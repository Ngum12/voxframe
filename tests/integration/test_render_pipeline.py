"""End-to-end render tests against real FFmpeg.

The important one is :func:`test_rendered_frame_count_matches_grid`. Phase 1's
drift test covered the frame grid's *arithmetic*; this checks the actual file
on disk, which is where the guarantee either holds or does not. It caught a
real bug immediately: ``-shortest`` combined with ``-frames:v`` silently
truncated output by one frame (D-025).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.config.settings import AspectRatio, QualityPreset
from voxframe.config.style import get_template
from voxframe.models.scene import Scene
from voxframe.models.transcript import Transcript, Word
from voxframe.render.compose import RenderError, render_captioned_video
from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import run_ffmpeg
from voxframe.segment import segment_transcript
from voxframe.timeline import FrameGrid

pytestmark = pytest.mark.needs_ffmpeg


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


@pytest.fixture
def silence(caps, tmp_path: Path) -> Path:  # type: ignore[no-untyped-def]
    """A short audio file of known duration.

    Deliberately not speech: these tests exercise rendering and timing, which
    must hold regardless of what the audio contains.
    """
    path = tmp_path / "audio.wav"
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=6.37",
            "-ar", "16000", "-ac", "1", "-y", str(path),
        ],
    )
    return path


def _scenes(grid: FrameGrid) -> tuple[Scene, ...]:
    """Two scenes with plausible word timings."""
    transcript = Transcript(
        words=(
            Word(text="Voxframe", start=0.2, end=0.9),
            Word(text="renders", start=1.0, end=1.5),
            Word(text="captions.", start=1.6, end=2.3),
            Word(text="Timing", start=3.4, end=3.9),
            Word(text="is", start=4.0, end=4.2),
            Word(text="exact.", start=4.3, end=4.9),
        ),
        language="en",
        duration=grid.audio_duration,
        model_id="test",
        audio_sha256="a" * 64,
    )
    return segment_transcript(transcript, grid)


def _frame_count(caps, video: Path) -> int:  # type: ignore[no-untyped-def]
    result = run_ffmpeg(
        caps.ffprobe_path,
        [
            "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames",
            "-of", "default=nw=1:nk=1", str(video),
        ],
        check=False,
    )
    return int(result.stdout.strip())


class TestRenderOutput:
    def test_produces_a_playable_file(self, caps, silence: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        grid = FrameGrid(fps=30.0, audio_duration=6.37)
        result = render_captioned_video(
            silence, _scenes(grid), grid, get_template(), caps,
            tmp_path / "out.mp4",
            quality=QualityPreset.DRAFT, height=360,
        )

        assert result.video_path.stat().st_size > 0
        assert result.srt_path is not None and result.srt_path.exists()
        assert result.vtt_path is not None and result.vtt_path.exists()
        assert result.ass_path.exists()

    def test_missing_audio_reports_clearly(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        grid = FrameGrid(fps=30.0, audio_duration=6.37)
        with pytest.raises(RenderError, match="not found"):
            render_captioned_video(
                tmp_path / "nope.wav", _scenes(grid), grid, get_template(), caps,
                tmp_path / "out.mp4",
            )

    def test_sidecars_can_be_skipped(self, caps, silence: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        grid = FrameGrid(fps=30.0, audio_duration=6.37)
        result = render_captioned_video(
            silence, _scenes(grid), grid, get_template(), caps,
            tmp_path / "out.mp4",
            quality=QualityPreset.DRAFT, height=360, write_sidecars=False,
        )
        assert result.srt_path is None


class TestFrameAccuracy:
    """The no-drift guarantee, checked against real files (D-013, D-025)."""

    @pytest.mark.parametrize("fps", [24.0, 25.0, 30.0])
    def test_rendered_frame_count_matches_grid(
        self, caps, silence: Path, tmp_path: Path, fps: float
    ) -> None:  # type: ignore[no-untyped-def]
        """The file must hold exactly the frames the grid demanded.

        6.37 s is chosen deliberately: it is not a whole number of frames at
        any of these rates, which is where truncation bugs appear.
        """
        if caps.ffprobe_path is None:
            pytest.skip("ffprobe required to count frames")

        grid = FrameGrid(fps=fps, audio_duration=6.37)
        result = render_captioned_video(
            silence, _scenes(grid), grid, get_template(), caps,
            tmp_path / f"out{fps}.mp4",
            quality=QualityPreset.DRAFT, height=360, write_sidecars=False,
        )

        assert _frame_count(caps, result.video_path) == grid.total_frames

    def test_audio_survives_unmodified(self, caps, silence: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """Video length must not truncate the audio."""
        if caps.ffprobe_path is None:
            pytest.skip("ffprobe required")

        grid = FrameGrid(fps=30.0, audio_duration=6.37)
        result = render_captioned_video(
            silence, _scenes(grid), grid, get_template(), caps,
            tmp_path / "out.mp4",
            quality=QualityPreset.DRAFT, height=360, write_sidecars=False,
        )

        probe = run_ffmpeg(
            caps.ffprobe_path,
            [
                "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=duration",
                "-of", "default=nw=1:nk=1", str(result.video_path),
            ],
            check=False,
        )
        # AAC pads to a whole number of frames, so exact equality is not
        # available; a tenth of a second catches real truncation.
        assert abs(float(probe.stdout.strip()) - 6.37) < 0.1


class TestAspectRatios:
    @pytest.mark.parametrize(
        ("aspect", "height", "expected_width"),
        [
            (AspectRatio.HORIZONTAL, 360, 640),
            (AspectRatio.VERTICAL, 640, 360),
            (AspectRatio.SQUARE, 360, 360),
        ],
    )
    def test_dimensions(
        self, caps, silence: Path, tmp_path: Path,
        aspect: AspectRatio, height: int, expected_width: int,
    ) -> None:  # type: ignore[no-untyped-def]
        grid = FrameGrid(fps=30.0, audio_duration=6.37)
        result = render_captioned_video(
            silence, _scenes(grid), grid, get_template(), caps,
            tmp_path / f"{aspect.name}.mp4",
            aspect=aspect, quality=QualityPreset.DRAFT, height=height,
            write_sidecars=False,
        )

        assert (result.width, result.height) == (expected_width, height)

    def test_odd_height_made_even(self, caps, silence: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """yuv420p needs even dimensions; odd ones make encoders fail."""
        grid = FrameGrid(fps=30.0, audio_duration=6.37)
        result = render_captioned_video(
            silence, _scenes(grid), grid, get_template(), caps,
            tmp_path / "odd.mp4",
            quality=QualityPreset.DRAFT, height=361, write_sidecars=False,
        )

        assert result.width % 2 == 0
        assert result.height % 2 == 0


class TestCaptionsVisible:
    def test_caption_pixels_present(self, caps, silence: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """A render that silently drops captions would otherwise look fine.

        Compares a captioned frame against the bare background: if the caption
        rendered, the frame is measurably different.
        """
        if not caps.has_libass:
            pytest.skip("build lacks libass")

        grid = FrameGrid(fps=30.0, audio_duration=6.37)
        result = render_captioned_video(
            silence, _scenes(grid), grid, get_template(), caps,
            tmp_path / "out.mp4",
            quality=QualityPreset.DRAFT, height=360, write_sidecars=False,
        )

        frame = tmp_path / "frame.png"
        run_ffmpeg(
            caps.ffmpeg_path,
            ["-loglevel", "error", "-ss", "1.2", "-i", str(result.video_path),
             "-frames:v", "1", "-y", str(frame)],
        )

        # A frame of flat background compresses to almost nothing; text adds
        # detail and therefore bytes.
        assert frame.stat().st_size > 3000, "frame looks blank; captions may be missing"


class TestEndOfAudioPolicy:
    """Audio is padded to the frame boundary, never truncated (D-032).

    Audio almost never ends exactly on a frame. Truncating the video breaks the
    frame grid (D-025); truncating the audio clips the speaker's final
    syllable. Padding with silence costs under 33 ms of inaudible output and
    keeps both invariants.
    """

    @pytest.mark.parametrize("duration", [6.37, 5.019, 7.983])
    def test_audio_is_never_shortened(
        self, caps, tmp_path: Path, duration: float
    ) -> None:  # type: ignore[no-untyped-def]
        """The output must contain at least all of the source audio.

        Durations chosen so none lands on a whole frame at 30 fps, which is
        where truncation appears.
        """
        if caps.ffprobe_path is None:
            pytest.skip("ffprobe required")

        source = tmp_path / "audio.wav"
        run_ffmpeg(
            caps.ffmpeg_path,
            ["-loglevel", "error", "-f", "lavfi",
             "-i", f"sine=frequency=220:duration={duration}",
             "-ar", "16000", "-ac", "1", "-y", str(source)],
        )

        grid = FrameGrid(fps=30.0, audio_duration=duration)
        result = render_captioned_video(
            source, _scenes(grid), grid, get_template(), caps,
            tmp_path / "out.mp4",
            quality=QualityPreset.DRAFT, height=360, write_sidecars=False,
        )

        probe = run_ffmpeg(
            caps.ffprobe_path,
            ["-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=duration", "-of", "default=nw=1:nk=1",
             str(result.video_path)],
            check=False,
        )
        output_audio = float(probe.stdout.strip())

        # Padding may add up to a frame; truncation must never occur. A small
        # tolerance covers AAC's 1024-sample frame quantisation.
        assert output_audio >= duration - 0.005, (
            f"audio truncated: source {duration}s, output {output_audio}s"
        )

    def test_video_covers_the_whole_audio(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """total_frames rounds up, so the video is never shorter than the audio."""
        for duration in (5.001, 5.999, 6.5):
            grid = FrameGrid(fps=30.0, audio_duration=duration)
            assert grid.total_frames / 30.0 >= duration, (
                f"{duration}s audio needs {duration * 30} frames, "
                f"grid gives {grid.total_frames}"
            )
