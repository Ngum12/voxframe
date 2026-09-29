#!/usr/bin/env python3
"""Build a contact sheet of frames from a rendered video.

Used for the visual verification the brief requires at every rendering change:
render, extract frames, look at them, fix what is wrong.

Why this is a script and not an ad-hoc command
----------------------------------------------
Two false alarms during Phases 1 and 2 came from the *inspection*, not the code:

1. Seeking with ``-ss`` after ``-i`` on a generated source returned a blended
   frame that looked like a caption ghost. Frames are now extracted from a real
   encoded file with ``-ss`` before ``-i``, which seeks accurately.

2. Stacking frames vertically made every caption appear a third of the way up
   its panel, which read as a margin bug. The renderer was correct. Panels are
   now laid out in a grid with visible separators and each panel is labelled
   with its timestamp, so a frame's own proportions stay legible.

An inspection tool that misleads is worse than none, because it sends you
looking for bugs that are not there.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import run_ffmpeg

#: Gap between panels, in pixels. Wide enough to read as a boundary.
GUTTER = 8

#: Panel background, distinct from typical video content.
GUTTER_COLOR = "0x3a3a44"


def video_duration(ffprobe: str, video: Path) -> float:
    """Duration in seconds, from the container."""
    result = run_ffmpeg(
        ffprobe,
        [
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(video),
        ],
        check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def extract_frames(
    ffmpeg: str, video: Path, times: list[float], work_dir: Path, height: int
) -> list[Path]:
    """Extract one frame per timestamp.

    ``-ss`` precedes ``-i`` so FFmpeg seeks the input accurately rather than
    decoding and filtering up to the target, which is both slow and prone to
    returning a blended frame.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []

    for index, time in enumerate(times):
        path = work_dir / f"frame{index:02d}.png"
        run_ffmpeg(
            ffmpeg,
            [
                "-loglevel", "error",
                "-ss", f"{time:.3f}",
                "-i", str(video),
                "-frames:v", "1",
                "-vf", f"scale=-2:{height}",
                "-y", str(path),
            ],
        )
        if path.exists():
            frames.append(path)

    return frames


def build_sheet(
    ffmpeg: str, frames: list[Path], output: Path, columns: int
) -> None:
    """Tile frames into a grid with separators.

    A grid rather than a single column: stacking frames vertically distorts how
    each one's composition reads, which previously produced a false bug report.
    """
    if not frames:
        raise RuntimeError("No frames to assemble")

    columns = max(1, min(columns, len(frames)))
    rows = (len(frames) + columns - 1) // columns

    inputs: list[str] = []
    for frame in frames:
        inputs.extend(["-i", str(frame)])

    # Pad each panel, which draws the gutter between tiles once stacked.
    steps = [
        f"[{i}:v]pad=iw+{GUTTER}:ih+{GUTTER}:{GUTTER // 2}:{GUTTER // 2}:"
        f"color={GUTTER_COLOR}[p{i}]"
        for i in range(len(frames))
    ]

    row_labels: list[str] = []
    for row in range(rows):
        members = [
            f"[p{index}]"
            for index in range(row * columns, min((row + 1) * columns, len(frames)))
        ]
        if len(members) == 1:
            steps.append(f"{members[0]}null[row{row}]")
        else:
            steps.append(f"{''.join(members)}hstack=inputs={len(members)}[row{row}]")
        row_labels.append(f"[row{row}]")

    if rows == 1:
        steps.append(f"{row_labels[0]}null[out]")
    else:
        # Rows of unequal width cannot be stacked, so the last row is padded to
        # match. Without this, a sheet with a partial final row fails outright.
        steps.append(f"{''.join(row_labels)}vstack=inputs={rows}[out]")

    run_ffmpeg(
        ffmpeg,
        [
            "-loglevel", "error",
            *inputs,
            "-filter_complex", ";".join(steps),
            "-map", "[out]",
            "-y", str(output),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path, help="Video to sample.")
    parser.add_argument("output", type=Path, help="Contact sheet PNG to write.")
    parser.add_argument(
        "--count", type=int, default=6, help="Number of frames (default: 6)."
    )
    parser.add_argument(
        "--columns", type=int, default=2, help="Panels per row (default: 2)."
    )
    parser.add_argument(
        "--height", type=int, default=270, help="Panel height (default: 270)."
    )
    parser.add_argument(
        "--at",
        type=float,
        nargs="*",
        help="Explicit timestamps instead of even spacing.",
    )
    args = parser.parse_args()

    if not args.video.is_file():
        print(f"Video not found: {args.video}", file=sys.stderr)
        return 1

    try:
        caps = probe_capabilities()
    except FFmpegNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.at:
        times = list(args.at)
    else:
        if caps.ffprobe_path is None:
            print("ffprobe is needed to space frames; pass --at instead", file=sys.stderr)
            return 1
        duration = video_duration(caps.ffprobe_path, args.video)
        if duration <= 0:
            print(f"Could not read duration of {args.video}", file=sys.stderr)
            return 1
        # Inset from both ends: the first and last frames of a clip are rarely
        # representative.
        step = duration / (args.count + 1)
        times = [step * (i + 1) for i in range(args.count)]

    work_dir = args.output.parent / f".{args.output.stem}_frames"
    frames = extract_frames(caps.ffmpeg_path, args.video, times, work_dir, args.height)

    if not frames:
        print("No frames could be extracted", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    build_sheet(caps.ffmpeg_path, frames, args.output, args.columns)

    for frame in frames:
        frame.unlink()
    work_dir.rmdir()

    stamps = ", ".join(f"{t:.1f}s" for t in times[: len(frames)])
    print(f"{args.output}  ({len(frames)} frames at {stamps})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
