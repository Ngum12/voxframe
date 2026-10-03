"""The studio's copy of a video: the same frames and sound, no captions (D-196).

The studio draws the captions live over this copy, so it must be the finished
video in every respect but the captions: any other difference would be a
preview that lies.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from voxframe.config.settings import QualityPreset
from voxframe.config.style import get_template
from voxframe.plan import PlannedScene, PlanWord, ScenePlan
from voxframe.plan.caption_choice import CaptionChoice
from voxframe.render.compose import render_from_plan
from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import run_ffmpeg

pytestmark = pytest.mark.needs_ffmpeg


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        found = probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")
    if not found.has_libass:
        pytest.skip("FFmpeg has no libass")
    return found


@pytest.fixture
def audio(caps, tmp_path: Path) -> Path:  # type: ignore[no-untyped-def]
    path = tmp_path / "talk.wav"
    run_ffmpeg(
        caps.ffmpeg_path,
        ["-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
         "-ar", "48000", "-y", str(path)],
    )
    return path


def _plan(audio: Path, choice: CaptionChoice | None = None) -> ScenePlan:
    words = tuple(
        PlanWord(text=text, start=0.2 + 0.5 * i, end=0.6 + 0.5 * i)
        for i, text in enumerate(["Floods", "hit", "Douala", "again"])
    )
    return ScenePlan(
        audio_path=str(audio), audio_sha256="0" * 64, audio_duration=3.0, fps=30.0,
        total_frames=90,
        scenes=(
            PlannedScene(index=0, start_frame=0, end_frame=90,
                         text="Floods hit Douala again", words=words),
        ),
        captions=choice or CaptionChoice(),
    )


def _frame(caps, video: Path, number: int, out: Path) -> np.ndarray:  # type: ignore[no-untyped-def]
    run_ffmpeg(
        caps.ffmpeg_path,
        ["-loglevel", "error", "-i", str(video), "-vf", f"select=eq(n\\,{number})",
         "-frames:v", "1", "-y", str(out)],
    )
    return np.asarray(Image.open(out).convert("L")).astype(int)


def _count(caps, video: Path, stream: str) -> str:  # type: ignore[no-untyped-def]
    import subprocess

    return subprocess.run(
        [caps.ffprobe_path, "-v", "error", "-select_streams", stream, "-count_packets",
         "-show_entries", "stream=nb_read_packets,codec_name", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_the_copy_is_the_video_without_its_captions(caps, audio: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    result = render_from_plan(
        _plan(audio), audio, get_template(), caps, tmp_path / "out" / "talk.mp4",
        quality=QualityPreset.DRAFT, height=360, cache_dir=tmp_path / "cache" / "segments",
        studio_copy=True,
    )
    assert result.studio_path is not None and result.studio_path.is_file()

    # The same number of frames and of sound packets, the sound itself copied.
    assert _count(caps, result.studio_path, "v:0") == _count(caps, result.video_path, "v:0")
    assert _count(caps, result.studio_path, "a:0") == _count(caps, result.video_path, "a:0")

    final = _frame(caps, result.video_path, 45, tmp_path / "final.png")
    studio = _frame(caps, result.studio_path, 45, tmp_path / "studio.png")
    height = final.shape[0]
    # Above the captions the two agree; in the caption area only the video
    # has text.
    top = slice(0, height // 2)
    assert np.abs(final[top] - studio[top]).mean() < 2
    bottom = slice(height // 2, height)
    assert (final[bottom] > 200).sum() > 50
    assert (studio[bottom] > 200).sum() < (final[bottom] > 200).sum() / 10


def test_changing_only_the_captions_keeps_the_pictures(caps, audio: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cache = tmp_path / "cache" / "segments"
    first = render_from_plan(
        _plan(audio), audio, get_template(), caps, tmp_path / "a" / "talk.mp4",
        quality=QualityPreset.DRAFT, height=360, cache_dir=cache, studio_copy=True,
    )
    pictures = tmp_path / "cache" / "pictures"
    clean = sorted(pictures.glob("*.clean.mp4"))
    assert len(clean) == 1
    made = clean[0].stat().st_mtime_ns

    second = render_from_plan(
        _plan(audio, CaptionChoice(animation="pop", anchor_y=0.3)),  # type: ignore[arg-type]
        audio, get_template(), caps, tmp_path / "b" / "talk.mp4",
        quality=QualityPreset.DRAFT, height=360, cache_dir=cache, studio_copy=True,
    )
    # The same uncaptioned pictures, not made again; new captions burned in.
    assert sorted(pictures.glob("*.clean.mp4")) == clean
    assert clean[0].stat().st_mtime_ns == made
    assert first.ass_path.read_text() != second.ass_path.read_text()
    assert len(list(pictures.glob("*.mp4"))) == 3
