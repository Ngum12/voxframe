"""A long video's segments join without a command line too long for Windows (D-167).

A 126-scene talk rendered 157 segments, and joining them with crossfades put
every segment's full path and the whole filter graph on one FFmpeg command
line: past Windows' 32,767 characters, so ``subprocess`` failed with WinError
206 ("The filename or extension is too long") and no video was made. The join
now runs in chunks that break only at hard cuts, and the runner refuses any
command near the limit with a message that says what happened.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from voxframe.render.compose import scenes
from voxframe.render.compose.scenes import SegmentResult, concat_segments, transition_chunks
from voxframe.render.compose.transitions import (
    TransitionKind,
    TransitionPlan,
    total_frames_after,
)
from voxframe.render.encode.probe import probe_capabilities
from voxframe.render.ffpath.runner import COMMAND_LINE_LIMIT, FFmpegError, run_ffmpeg


def _boundaries(count: int, blend_every: int = 2, frames: int = 4) -> list[TransitionPlan]:
    """Blends and cuts alternating, like a real plan's mix of both."""
    return [
        TransitionPlan(
            after_scene=i,
            kind=TransitionKind.CROSSFADE if i % blend_every == 0 else TransitionKind.CUT,
            frames=frames if i % blend_every == 0 else 0,
        )
        for i in range(count - 1)
    ]


class TestChunks:
    def test_every_segment_once_in_order(self) -> None:
        runs = transition_chunks(_boundaries(157), 157, limit=40)

        covered = [i for start, end in runs for i in range(start, end)]
        assert covered == list(range(157))

    def test_runs_break_only_at_cuts(self) -> None:
        boundaries = _boundaries(157)

        runs = transition_chunks(boundaries, 157, limit=40)

        for _, end in runs[:-1]:
            assert not boundaries[end - 1].is_blend, "a blend was split across two calls"

    def test_runs_stay_within_the_limit_when_cuts_allow(self) -> None:
        runs = transition_chunks(_boundaries(157), 157, limit=40)

        assert len(runs) >= 4
        assert all(end - start <= 40 for start, end in runs)

    def test_a_short_video_is_one_run(self) -> None:
        assert transition_chunks(_boundaries(12), 12) == [(0, 12)]


def test_157_segments_with_long_paths_fit_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failing render, without FFmpeg: every command must fit."""
    folder = tmp_path / ("a-long-folder-name-like-an-app-data-path-" * 3) / "segments"
    segments = [
        SegmentResult(i, folder / f"seg_{i:04d}_{'0123456789abcdef' * 2}.mp4", 30, True)
        for i in range(157)
    ]
    boundaries = _boundaries(157)
    commands: list[list[str]] = []

    def record(binary: str, args: list[str], **kwargs: object) -> None:
        commands.append([binary, *args])

    monkeypatch.setattr(scenes, "run_ffmpeg", record)
    caps = type("Caps", (), {"ffmpeg_path": "ffmpeg"})()

    concat_segments(segments, caps, tmp_path / "out.mp4", tmp_path, boundaries, 30.0)  # type: ignore[arg-type]

    longest = max(len(subprocess.list2cmdline(command)) for command in commands)
    assert longest < COMMAND_LINE_LIMIT // 2, longest
    graphs = " ".join(c[c.index("-filter_complex") + 1] for c in commands if "-filter_complex" in c)
    assert graphs.count("xfade=") == sum(1 for b in boundaries if b.is_blend), "a blend was lost"
    assert "-f" in commands[-1] and "concat" in commands[-1], "the runs are joined from a list"


def test_the_runner_refuses_a_command_windows_would_refuse() -> None:
    too_long = ["-i", "x" * (COMMAND_LINE_LIMIT + 10)]

    with pytest.raises(FFmpegError, match="characters long"):
        run_ffmpeg("ffmpeg", too_long)


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except Exception as exc:  # pragma: no cover - FFmpeg is part of the dev setup
        pytest.skip(f"FFmpeg not available: {exc}")


def _frames(path: Path) -> int:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return int(json.loads(probe.stdout)["streams"][0]["nb_read_frames"])


def test_a_chunked_join_has_every_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caps  # type: ignore[no-untyped-def]
) -> None:
    """Real FFmpeg, forced into several runs: the frame count must be exact,
    because a lost or doubled frame desynchronises every later caption (D-025)."""
    monkeypatch.setattr(scenes, "MAX_SEGMENTS_PER_RUN", 5)
    segments = []
    for i in range(17):
        path = tmp_path / f"seg_{i:02d}.mp4"
        run_ffmpeg(caps.ffmpeg_path, [
            "-loglevel", "error", "-f", "lavfi",
            "-i", f"color=c=0x{(i * 14) % 256:02x}4080:s=64x36:r=30",
            "-frames:v", "12", *scenes._INTERMEDIATE_ARGS, "-y", str(path),
        ])
        segments.append(SegmentResult(i, path, 12, True))
    boundaries = _boundaries(17, blend_every=3, frames=4)
    output = tmp_path / "joined.mp4"

    concat_segments(segments, caps, output, tmp_path, boundaries, 30.0)

    assert len(transition_chunks(boundaries, 17, limit=5)) > 1
    assert _frames(output) == total_frames_after([12] * 17, boundaries)
