#!/usr/bin/env python3
"""Render the same frames with each caption style, for a fair comparison.

Run::

    python scripts/caption_style_sheet.py --plan out.plan.json --audio in.wav

The three styles differ only in how caption text is separated from the image
behind it (D-060), and which reads best depends entirely on what is behind it.
Comparing them on synthetic backgrounds settled nothing; this renders the
*same* timestamps from the *same* plan with each style, over the real stock
photography and video clips the tool now sources.

Output is one sheet per timestamp, three rows, so each frame is compared
against itself rather than against a different moment of the video.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

STYLES = ("box", "band", "outline")


def render_with_style(
    plan: Path, audio: Path, style: str, output: Path, height: int
) -> bool:
    """Render the plan once with one caption backing.

    The backing is passed through the environment so the plan itself is
    untouched: all three renders describe exactly the same edit decisions.
    """
    import os

    environment = {**os.environ, "VOXFRAME_CAPTION_BACKING": style}

    completed = subprocess.run(
        [
            sys.executable, "-m", "voxframe.cli.main", "render", str(plan),
            "--output", str(output), "--height", str(height),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=2400,
    )

    if completed.returncode != 0:
        print(f"  {style}: FAILED {completed.stderr.strip()[:160]}", file=sys.stderr)
        return False

    return output.is_file()


def extract(video: Path, seconds: float, destination: Path, width: int) -> bool:
    """One frame at a timestamp."""
    completed = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", f"{seconds:.3f}", "-i", str(video),
            "-frames:v", "1", "-vf", f"scale={width}:-1",
            "-y", str(destination),
        ],
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0 and destination.is_file()


def build_sheet(
    frames: dict[str, list[Path]], timestamps: list[float], output: Path
) -> None:
    """One sheet: a row per style, a column per timestamp."""
    from PIL import Image, ImageDraw

    first = Image.open(next(iter(frames.values()))[0])
    width, height = first.size
    label_height = 26

    sheet = Image.new(
        "RGB",
        (width * len(timestamps), (height + label_height) * len(STYLES)),
        "#111",
    )
    draw = ImageDraw.Draw(sheet)

    for row, style in enumerate(STYLES):
        for column, seconds in enumerate(timestamps):
            path = frames[style][column]
            x = column * width
            y = row * (height + label_height)

            sheet.paste(Image.open(path).convert("RGB"), (x, y + label_height))
            draw.text(
                (x + 8, y + 6),
                f"{style.upper()}   t={seconds:.1f}s",
                fill="#ffd966",
            )

    sheet.save(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument(
        "--at",
        type=float,
        nargs="*",
        help="Timestamps to sample. Default: one inside each of the first four scenes.",
    )
    parser.add_argument(
        "--output", type=Path, default=REPO_ROOT / "docs/phases/phase-5-caption-styles.png"
    )
    args = parser.parse_args()

    from voxframe.plan import ScenePlan

    plan = ScenePlan.load(args.plan)

    timestamps = args.at
    if not timestamps:
        # Mid-scene, so a caption is definitely on screen, preferring scenes
        # that actually have imagery behind them.
        timestamps = [
            (scene.start_frame + scene.end_frame) / 2 / plan.fps
            for scene in plan.scenes
            if scene.asset is not None
        ][:4]

    if not timestamps:
        print("No matched scenes to sample.", file=sys.stderr)
        return 1

    work = REPO_ROOT / "demo_output" / "_styles"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    frames: dict[str, list[Path]] = {}

    for style in STYLES:
        video = work / f"{style}.mp4"
        print(f"rendering {style}...")
        if not render_with_style(args.plan, args.audio, style, video, args.height):
            return 1

        captured: list[Path] = []
        for index, seconds in enumerate(timestamps):
            frame = work / f"{style}_{index}.png"
            if extract(video, seconds, frame, 520):
                captured.append(frame)

        if len(captured) != len(timestamps):
            print(f"  {style}: only {len(captured)}/{len(timestamps)} frames")
            return 1
        frames[style] = captured

    args.output.parent.mkdir(parents=True, exist_ok=True)
    build_sheet(frames, list(timestamps), args.output)

    print(f"\nWrote {args.output}")
    print(f"Timestamps: {', '.join(f'{t:.1f}s' for t in timestamps)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
