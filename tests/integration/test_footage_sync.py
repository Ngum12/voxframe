"""The speaker's picture stays on their voice, measured in the finished video (D-192).

A face makes any sync error visible: lips a frame or two late read as a badly
dubbed film. So this trusts nothing the plan says. Each source recording has
white flashes in its picture at the very moments a tone sounds in its sound,
like a clapperboard, and the test finds both in the rendered video: every
flash must land on its tone.

It does so through the things that move scenes on the video's clock: a chapter
card that pauses the speech, a crossfade that renders a scene longer than it
lasts, a vertical crop, a recording whose sound starts later than its picture
(as phone recordings often do), and footage at 25 frames a second rendered on
a 30 frame grid.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from voxframe.config.settings import AspectRatio, QualityPreset
from voxframe.config.style import get_template
from voxframe.plan import MotionKind, PlannedScene, ScenePlan
from voxframe.plan.scene_plan import Footage, Shot
from voxframe.render.compose import render_from_plan
from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities, probe_footage
from voxframe.render.ffpath import run_ffmpeg

pytestmark = [pytest.mark.slow, pytest.mark.needs_ffmpeg]

FPS = 30.0

#: When the clapper sounds and flashes, on the recording's sound clock.
CLAPS = (1.5, 4.5)

#: One frame of the video, plus a little for finding the tone's start.
TOLERANCE = 1.5 / FPS


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


def _recording(
    caps, path: Path, *, rate: int = 30, audio_delay: float = 0.0, size: str = "640x360",
    video_delay: float = 0.0,
) -> Path:  # type: ignore[no-untyped-def]
    """Seven seconds: dark picture and silence, with a flash and a tone at each clap.

    With ``audio_delay`` the sound stream starts that much after the picture,
    and the flashes are placed so they still coincide with the tones. With
    ``video_delay`` the picture starts that much after the sound instead.
    """
    # ``geq`` names time ``T``; ``aevalsrc`` names it ``t``. ``T`` is the
    # file's time, the picture's delay included, as the tones' times are.
    flashes = "+".join(
        f"between(T,{clap + audio_delay:.4f},{clap + audio_delay + 0.1:.4f})" for clap in CLAPS
    )
    tones = "+".join(f"between(t,{clap:.4f},{clap + 0.3:.4f})" for clap in CLAPS)
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-itsoffset", f"{video_delay:.3f}",
            "-f", "lavfi", "-i", f"color=0x202020:s={size}:r={rate}:d={7 + audio_delay - video_delay}",
            "-itsoffset", f"{audio_delay:.3f}",
            "-f", "lavfi", "-i", f"aevalsrc='if({tones},0.8*sin(2*PI*880*t),0)':s=48000:d=7",
            "-filter_complex", f"[0:v]geq=lum='if({flashes},235,32)':cb=128:cr=128[v]",
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-g", str(rate * 2), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-y", str(path),
        ],
    )
    return path


def _footage(caps, path: Path) -> Footage:  # type: ignore[no-untyped-def]
    info = probe_footage(path, caps)
    assert info is not None
    return Footage(
        path=str(path), width=info.width, height=info.height, fps=info.fps,
        duration=info.duration, audio_offset=info.audio_offset,
    )


def _speaker(index: int, start: float, end: float, source: float) -> PlannedScene:
    return PlannedScene(
        index=index,
        start_frame=round(start * FPS),
        end_frame=round(end * FPS),
        motion=MotionKind.NONE,
        shot=Shot.SPEAKER,
        footage_start=source,
    )


def _plan(footage: Footage, *scenes: PlannedScene, aspect: AspectRatio = AspectRatio.HORIZONTAL) -> ScenePlan:
    return ScenePlan(
        audio_path=footage.path,
        audio_sha256="0" * 64,
        audio_duration=7.0,
        fps=FPS,
        total_frames=scenes[-1].end_frame,
        aspect=aspect,
        scenes=scenes,
        footage=footage,
    )


def _flashes(caps, video: Path) -> list[float]:  # type: ignore[no-untyped-def]
    """When each run of bright frames starts in the rendered video."""
    frames_file = video.with_suffix(".gray")
    run_ffmpeg(
        caps.ffmpeg_path,
        ["-loglevel", "error", "-i", str(video), "-vf", "scale=32:18,format=gray",
         "-f", "rawvideo", "-y", str(frames_file)],
    )
    frames = np.frombuffer(frames_file.read_bytes(), dtype=np.uint8).reshape(-1, 18 * 32)
    bright = frames.mean(axis=1) > 128
    starts = [i for i in range(len(bright)) if bright[i] and (i == 0 or not bright[i - 1])]
    return [i / FPS for i in starts]


def _tones(caps, video: Path) -> list[float]:  # type: ignore[no-untyped-def]
    """When each tone starts in the rendered video's sound."""
    result = run_ffmpeg(
        caps.ffmpeg_path,
        ["-i", str(video), "-af", "silencedetect=noise=-30dB:d=0.2", "-f", "null", "-"],
        check=False,
    )
    return [float(t) for t in re.findall(r"silence_end: ([\d.]+)", result.stderr)]


def _duration(video: Path) -> float:
    from voxframe.render.encode.probe import probe_media

    return probe_media(video).duration


def _render(caps, plan: ScenePlan, tmp_path: Path, template: str = "clean-educational"):  # type: ignore[no-untyped-def]
    return render_from_plan(
        plan, Path(plan.audio_path), get_template(template), caps, tmp_path / "out.mp4",
        quality=QualityPreset.DRAFT, height=360, write_sidecars=False,
    )


def _assert_in_sync(caps, video: Path, expected: list[float]) -> None:  # type: ignore[no-untyped-def]
    flashes, tones = _flashes(caps, video), _tones(caps, video)
    assert len(flashes) == len(expected), f"flashes at {flashes}, expected {expected}"
    # The last silence can end with the video, which is not a tone.
    tones = [tone for tone in tones if tone < _duration(video) - 0.05]
    assert len(tones) == len(expected), f"tones at {tones}, expected {expected}"
    for flash, tone, want in zip(flashes, tones, expected, strict=True):
        assert flash == pytest.approx(want, abs=TOLERANCE), f"flash at {flash:.3f}s, not {want}s"
        assert tone == pytest.approx(want, abs=TOLERANCE), f"tone at {tone:.3f}s, not {want}s"
        assert flash == pytest.approx(tone, abs=TOLERANCE), (
            f"picture and sound apart: flash {flash:.3f}s, tone {tone:.3f}s"
        )


class TestTheSpeakerStaysOnTheirVoice:
    def test_one_long_speaker_shot(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        footage = _footage(caps, _recording(caps, tmp_path / "rec.mp4"))
        video = _render(caps, _plan(footage, _speaker(0, 0, 7, 0.0)), tmp_path).video_path
        _assert_in_sync(caps, video, list(CLAPS))

    def test_scenes_cut_from_the_footage(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """Cuts on both sides of each clap: every scene seeks on its own."""
        footage = _footage(caps, _recording(caps, tmp_path / "rec.mp4"))
        plan = _plan(
            footage,
            _speaker(0, 0.0, 1.4, 0.0),
            _speaker(1, 1.4, 3.1, 1.4),
            _speaker(2, 3.1, 4.6, 3.1),
            _speaker(3, 4.6, 7.0, 4.6),
        )
        _assert_in_sync(caps, _render(caps, plan, tmp_path).video_path, list(CLAPS))

    def test_a_chapter_card_pauses_picture_and_speech_together(
        self, caps, tmp_path: Path  # type: ignore[no-untyped-def]
    ) -> None:
        """A 1s card at 3.0s: the second clap is seen and heard at 5.5s."""
        footage = _footage(caps, _recording(caps, tmp_path / "rec.mp4"))
        plan = _plan(
            footage,
            _speaker(0, 0.0, 3.0, 0.0),
            PlannedScene(
                index=1, start_frame=90, end_frame=120, card_kind="chapter",
                card_text="Part two", motion=MotionKind.NONE,
            ),
            _speaker(2, 4.0, 8.0, 3.0),
        )
        _assert_in_sync(caps, _render(caps, plan, tmp_path).video_path, [1.5, 5.5])

    def test_crossfades_keep_the_footage_in_step(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """A template with crossfades renders scenes longer than they last."""
        footage = _footage(caps, _recording(caps, tmp_path / "rec.mp4"))
        plan = _plan(
            footage,
            _speaker(0, 0.0, 2.5, 0.0),
            _speaker(1, 2.5, 7.0, 2.5),
        )
        result = _render(caps, plan, tmp_path, template="documentary")
        assert result.frame_count == plan.total_frames
        _assert_in_sync(caps, result.video_path, list(CLAPS))

    def test_sound_that_starts_after_the_picture(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """Phones often start the sound a little after the picture.

        The sound's clock starts at its first decoded sample, which an AAC
        encoder places a little before the sound itself (its "priming"), so
        the claps arrive a few hundredths after their nominal times. What
        matters is that picture and sound arrive together.
        """
        delay = 0.4
        footage = _footage(caps, _recording(caps, tmp_path / "rec.mp4", audio_delay=delay))
        assert footage.audio_offset == pytest.approx(delay, abs=0.03)
        video = _render(caps, _plan(footage, _speaker(0, 0, 7, 0.0)), tmp_path).video_path
        priming = delay - footage.audio_offset
        _assert_in_sync(caps, video, [clap + priming for clap in CLAPS])

    def test_a_picture_that_starts_after_the_sound(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """The first frame is held until the picture begins, never shown early."""
        footage = _footage(caps, _recording(caps, tmp_path / "rec.mp4", video_delay=0.3))
        assert footage.audio_offset == pytest.approx(0.0, abs=0.01)
        plan = _plan(footage, _speaker(0, 0.0, 3.0, 0.0), _speaker(1, 3.0, 7.0, 3.0))
        _assert_in_sync(caps, _render(caps, plan, tmp_path).video_path, list(CLAPS))

    def test_footage_at_another_frame_rate(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        footage = _footage(caps, _recording(caps, tmp_path / "rec.mp4", rate=25))
        plan = _plan(footage, _speaker(0, 0.0, 2.0, 0.0), _speaker(1, 2.0, 7.0, 2.0))
        _assert_in_sync(caps, _render(caps, plan, tmp_path).video_path, list(CLAPS))

    def test_a_vertical_video_from_landscape_footage(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        footage = _footage(caps, _recording(caps, tmp_path / "rec.mp4"))
        plan = _plan(footage, _speaker(0, 0, 7, 0.0), aspect=AspectRatio.VERTICAL)
        result = _render(caps, plan, tmp_path)
        assert result.width < result.height
        _assert_in_sync(caps, result.video_path, list(CLAPS))


class TestWhenTheFootageIsGone:
    def test_the_scene_falls_back_rather_than_failing(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """The video is still made; the sound is a separate file here."""
        recording = _recording(caps, tmp_path / "rec.mp4")
        footage = _footage(caps, recording)
        sound = tmp_path / "sound.wav"
        run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-i", str(recording), "-vn", "-y", str(sound)])
        plan = _plan(footage, _speaker(0, 0, 7, 0.0)).model_copy(update={"audio_path": str(sound)})
        recording.unlink()
        result = render_from_plan(
            plan, sound, get_template(), caps, tmp_path / "out.mp4",
            quality=QualityPreset.DRAFT, height=240, write_sidecars=False,
        )
        assert result.frame_count == plan.total_frames
        assert _flashes(caps, result.video_path) == []


class TestReadingTheRecording:
    def test_a_sound_file_has_no_picture(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        sound = tmp_path / "talk.mp3"
        run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-f", "lavfi", "-i", "sine=d=2", "-y", str(sound)])
        assert probe_footage(sound, caps) is None

    def test_album_art_is_not_a_picture(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """An MP3's cover image is a one-frame "video": nothing to show."""
        cover = tmp_path / "cover.png"
        run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=64x64", "-frames:v", "1", "-y", str(cover)])
        sound = tmp_path / "song.mp3"
        run_ffmpeg(
            caps.ffmpeg_path,
            ["-loglevel", "error", "-f", "lavfi", "-i", "sine=d=2", "-i", str(cover),
             "-map", "0:a", "-map", "1:v", "-c:v", "png", "-disposition:v", "attached_pic",
             "-y", str(sound)],
        )
        assert probe_footage(sound, caps) is None

    def test_a_phone_held_upright(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """Stored sideways with a rotation, as phones do: shown upright."""
        landscape = _recording(caps, tmp_path / "rec.mp4")
        upright = tmp_path / "upright.mp4"
        run_ffmpeg(
            caps.ffmpeg_path,
            ["-loglevel", "error", "-display_rotation", "90", "-i", str(landscape), "-c", "copy", "-y", str(upright)],
        )
        info = probe_footage(upright, caps)
        assert info is not None
        assert (info.width, info.height) == (360, 640)

    def test_the_frame_rate_and_length(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        info = probe_footage(_recording(caps, tmp_path / "rec.mp4", rate=25), caps)
        assert info is not None
        assert info.fps == pytest.approx(25.0)
        assert info.duration == pytest.approx(7.0, abs=0.1)
        assert info.audio_offset == pytest.approx(0.0, abs=0.01)
