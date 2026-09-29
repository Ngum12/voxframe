#!/usr/bin/env python3
"""Measure what fraction of scenes get a good visual match.

Run::

    python scripts/measure_fill_rate.py
    python scripts/measure_fill_rate.py --library ./library
    python scripts/measure_fill_rate.py --audio "samples/private/mine.m4a"

This is the number Phase 4 exists to move. A scene with no match renders as a
plain gradient, and a narration whose video is mostly gradient has not been
turned into anything worth watching — so "percentage of scenes filled" is the
functional measure, not retrieval scores on an eval set.

Two thresholds, because they answer different questions:

**Filled** — any asset was chosen at all. Without a library this is 0% by
construction, which is the pre-sourcing baseline.

**Good** — the match scored at or above ``--threshold``. A weak match is worse
than a gradient if it is visibly unrelated to what is being said, so filling
scenes with whatever ranks first is not automatically progress.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

#: Below this, a match is counted as present but not good. Set from the
#: matcher's own scale rather than guessed; see DECISIONS.md D-051 for how
#: these scores behave.
DEFAULT_GOOD_THRESHOLD = 0.25


@dataclass
class FillResult:
    """Fill rates for one audio file."""

    name: str
    scenes: int
    filled: int
    good: int
    unique_assets: int
    language: str

    @property
    def fill_rate(self) -> float:
        return self.filled / self.scenes if self.scenes else 0.0

    @property
    def good_rate(self) -> float:
        return self.good / self.scenes if self.scenes else 0.0

    @property
    def gradient_scenes(self) -> int:
        """Scenes that render as a plain gradient — the thing to eliminate."""
        return self.scenes - self.filled


def measure(
    audio: Path,
    library: Path | None,
    *,
    model: str,
    language: str | None,
    languages: tuple[str, ...],
    threshold: float,
) -> FillResult:
    """Build a plan and count how many scenes got a match."""
    from voxframe.config.settings import AspectRatio, get_settings
    from voxframe.config.style import get_template
    from voxframe.library import AssetLibrary, Embedder
    from voxframe.match import Matcher
    from voxframe.plan import build_plan
    from voxframe.segment import segment_transcript
    from voxframe.timeline import FrameGrid
    from voxframe.transcribe import Transcriber

    settings = get_settings()
    template = get_template()

    transcript = Transcriber(
        model,
        cache_dir=settings.cache_path,
        language=language,
        allowed_languages=languages,
    ).transcribe(audio)

    grid = FrameGrid(fps=settings.fps, audio_duration=transcript.duration)
    scenes = segment_transcript(transcript, grid, template.pacing)

    matches = None
    if library is not None and library.is_dir():
        embedder = Embedder(use_gpu=settings.use_gpu, model_key=settings.resolved_embed_model)
        matches = Matcher(AssetLibrary(library), embedder).match_scenes(
            scenes, transcript.language, aspect=AspectRatio.HORIZONTAL
        )

    plan = build_plan(
        audio, transcript, scenes, matches, grid, template,
        aspect=AspectRatio.HORIZONTAL,
    )

    good = sum(
        1
        for scene in plan.scenes
        if scene.asset is not None and scene.match_score >= threshold
    )

    return FillResult(
        name=audio.name,
        scenes=len(plan.scenes),
        filled=plan.matched_scenes,
        good=good,
        unique_assets=plan.unique_assets,
        language=transcript.language,
    )


def find_audio(explicit: Path | None) -> list[Path]:
    """Audio to measure: the explicit file, or every available sample."""
    if explicit:
        return [explicit]

    found: list[Path] = []
    for directory in (REPO_ROOT / "samples" / "public", REPO_ROOT / "samples" / "private"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in {".wav", ".mp3", ".m4a", ".flac"}:
                continue
            # Prefer the trimmed excerpt over the full recording.
            if path.suffix.lower() == ".mp3" and (
                directory / f"{path.stem}_45s.wav"
            ).exists():
                continue
            if path.stem in {"demo_tts", "nature_demo", "demo_fr", "probe_speech"}:
                continue
            found.append(path)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, help="One file instead of all samples.")
    parser.add_argument(
        "--library", type=Path, default=None, help="Image library. Omit for the baseline."
    )
    parser.add_argument("--model", default=None, help="Whisper size. Default follows the profile.")
    parser.add_argument("--language", help="Force a language code.")
    parser.add_argument(
        "--languages", default="en,fr", help="Restrict detection, e.g. 'en,fr'."
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_GOOD_THRESHOLD,
        help="Score at or above which a match counts as good.",
    )
    args = parser.parse_args()

    from voxframe.config.settings import get_settings

    model = args.model or get_settings().resolved_transcribe_model
    languages = tuple(c.strip() for c in (args.languages or "").split(",") if c.strip())

    files = find_audio(args.audio)
    if not files:
        print("No audio found. Run scripts/fetch_samples.py first.", file=sys.stderr)
        return 1

    label = str(args.library) if args.library else "none (baseline)"
    print(f"Library:   {label}")
    print(f"Model:     {model}")
    print(f"Threshold: {args.threshold} for a 'good' match")
    print()

    header = (
        f"  {'audio':34} {'lang':>4} {'scenes':>7} {'filled':>14} "
        f"{'good':>14} {'assets':>7}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    results: list[FillResult] = []
    for audio in files:
        try:
            result = measure(
                audio, args.library,
                model=model, language=args.language,
                languages=languages, threshold=args.threshold,
            )
        except Exception as exc:
            print(f"  {audio.name:34} FAILED: {type(exc).__name__}: {exc}")
            continue

        results.append(result)
        print(
            f"  {result.name[:34]:34} {result.language:>4} {result.scenes:>7} "
            f"{result.filled:>5}/{result.scenes:<3} {result.fill_rate:>6.0%} "
            f"{result.good:>5}/{result.scenes:<3} {result.good_rate:>6.0%} "
            f"{result.unique_assets:>7}"
        )

    if not results:
        return 1

    total_scenes = sum(r.scenes for r in results)
    total_filled = sum(r.filled for r in results)
    total_good = sum(r.good for r in results)
    total_gradient = sum(r.gradient_scenes for r in results)

    print("  " + "-" * (len(header) - 2))
    print(
        f"  {'TOTAL':34} {'':>4} {total_scenes:>7} "
        f"{total_filled:>5}/{total_scenes:<3} {total_filled / total_scenes:>6.0%} "
        f"{total_good:>5}/{total_scenes:<3} {total_good / total_scenes:>6.0%}"
    )
    print()
    print(
        f"  {total_gradient} of {total_scenes} scenes render as a plain "
        f"gradient (n={len(results)} files)."
    )
    print("  Phase 4's goal is to bring that to zero with good matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
