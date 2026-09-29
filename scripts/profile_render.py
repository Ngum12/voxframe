#!/usr/bin/env python3
"""Time each stage of a render, so optimisation targets the right one.

Run::

    python scripts/profile_render.py samples/private/nature_demo.wav

Reports transcription, matching, per-scene rendering, concatenation, caption
burn-in and final encode separately, plus which encoder each quality preset
actually selected.

Why per-stage rather than a total
---------------------------------
A total hides where time goes, and reasoning about it is unreliable: the
background-gradient bottleneck (D-054) made scenes with *no image* cost more
than scenes with Ken Burns motion, which nobody would have guessed. Parallax
lands in Phase 5 and will add substantially to per-scene rendering, so the
split matters before that work starts.
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@dataclass
class Timings:
    """Elapsed seconds per stage."""

    stages: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def measure(self, name: str):  # type: ignore[no-untyped-def]
        started = time.monotonic()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + (
                time.monotonic() - started
            )

    def report(self, audio_seconds: float, title: str) -> None:
        total = sum(self.stages.values())
        print(f"\n{title}")
        print(f"  {'stage':26} {'seconds':>9} {'% total':>9} {'x audio':>9}")
        print("  " + "-" * 56)
        for name, seconds in self.stages.items():
            share = seconds / total * 100 if total else 0.0
            print(
                f"  {name:26} {seconds:9.2f} {share:8.1f}% "
                f"{seconds / audio_seconds:9.2f}"
            )
        print("  " + "-" * 56)
        print(
            f"  {'TOTAL':26} {total:9.2f} {100.0:8.1f}% "
            f"{total / audio_seconds:9.2f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--library", type=Path, default=Path("demo_output/photos"))
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--quality", default="standard")
    args = parser.parse_args()

    from voxframe.config.settings import AspectRatio, QualityPreset
    from voxframe.config.style import get_template
    from voxframe.library import AssetLibrary, Embedder
    from voxframe.match import Matcher
    from voxframe.plan import build_plan
    from voxframe.render.captions.ass import write_ass
    from voxframe.render.compose.captioned import _CRF, _SPEED, _dimensions
    from voxframe.render.compose.from_plan import _scenes_for_captions
    from voxframe.render.compose.scenes import concat_segments, render_scene_segments
    from voxframe.render.encode.probe import probe_capabilities
    from voxframe.render.ffpath import ass_filter, run_ffmpeg
    from voxframe.segment import segment_transcript
    from voxframe.timeline import FrameGrid
    from voxframe.transcribe import Transcriber

    if not args.audio.is_file():
        print(f"Audio not found: {args.audio}", file=sys.stderr)
        return 1

    quality = QualityPreset(args.quality)
    template = get_template()
    caps = probe_capabilities()
    timings = Timings()

    # --- encoder selection, reported before anything is timed ---
    print(f"FFmpeg {caps.version}")
    print(f"  best available encoder: {caps.best_encoder()}")
    print(f"  absent from chain:      {', '.join(caps.missing_from_chain()) or 'none'}")
    print("\nEncoder per stage (D-057):")
    print("  scene segments   libx264 crf 16 preset veryfast  (fixed, yuv444p)")
    print("  concatenation    stream copy, no encode")
    print(
        f"  final encode     libx264 crf {_CRF[quality]} "
        f"preset {_SPEED[quality]}  (quality={quality.value})"
    )

    # --- transcription ---
    with timings.measure("transcription (cached)"):
        transcript = Transcriber("base", cache_dir=Path(".voxframe_cache")).transcribe(
            args.audio
        )

    grid = FrameGrid(fps=30.0, audio_duration=transcript.duration)

    with timings.measure("segmentation"):
        scenes = segment_transcript(transcript, grid, template.pacing)

    # --- matching ---
    with timings.measure("embedder load"):
        embedder = Embedder(use_gpu=False)
        embedder.embed_text("warm up")

    with timings.measure("matching"):
        matches = Matcher(AssetLibrary(args.library), embedder).match_scenes(
            scenes, transcript.language, aspect=AspectRatio.HORIZONTAL
        )

    with timings.measure("plan build"):
        plan = build_plan(
            args.audio, transcript, scenes, matches, grid, template,
            aspect=AspectRatio.HORIZONTAL, embed_model=embedder.model_id,
        )

    width, height = _dimensions(plan.aspect, args.height)
    output = Path(f"demo_output/profile_{args.height}.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output.parent / ".profile_segments"

    # --- per-scene rendering ---
    with timings.measure("scene segments (Ken Burns)"):
        segments = render_scene_segments(
            plan, caps, work_dir,
            width=width, height=height, motion=template.motion, quality=quality,
        )

    with timings.measure("concatenation (stream copy)"):
        concatenated = work_dir / "concatenated.mp4"
        concat_segments(segments, caps, concatenated, work_dir)

    # --- captions, then the final encode ---
    with timings.measure("caption file (ASS)"):
        ass_path = output.with_suffix(".ass")
        write_ass(
            ass_path, _scenes_for_captions(plan), template.captions,
            width, height, plan.fps,
        )

    caption_filter, cwd = ass_filter(ass_path)
    video_seconds = plan.total_frames / plan.fps

    with timings.measure("caption burn + final encode"):
        run_ffmpeg(
            caps.ffmpeg_path,
            [
                "-loglevel", "error",
                "-i", str(concatenated.resolve()),
                "-i", str(args.audio.resolve()),
                "-vf", f"{caption_filter},format=yuv420p",
                "-af", "apad",
                "-frames:v", str(plan.total_frames),
                "-t", f"{video_seconds:.6f}",
                "-c:v", "libx264",
                "-crf", str(_CRF[quality]),
                "-preset", _SPEED[quality],
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                "-y", str(output.resolve()),
            ],
            cwd=cwd,
        )

    import shutil

    shutil.rmtree(work_dir, ignore_errors=True)

    matched = plan.matched_scenes
    timings.report(
        transcript.duration,
        f"{args.height}p {quality.value} — n={len(plan.scenes)} scenes "
        f"({matched} matched, {len(plan.scenes) - matched} background), "
        f"{transcript.duration:.1f}s audio",
    )

    print(
        "\nNote: transcription is cached here. First run costs about 99 s for "
        "31 s of audio."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
