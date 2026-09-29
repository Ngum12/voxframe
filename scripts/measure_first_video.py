"""What a new user's first video looks like, measured on real recordings.

Renders each recording from a genuinely empty library with online search on --
a brand-new user -- and reports, per recording and in total (D-132..D-137):

- **filled**: scenes showing an image the matcher chose as a match.
- **good**: of those, matches scoring at least 0.25 -- the Phase 4 definition
  (D-069), kept so numbers stay comparable. A score proxy, not a judgement by
  eye.
- **close**: plain scenes that offer close matches, one click from filled.
- **atmospheric**: scenes filled by the atmospheric fallback (D-137). Counted
  separately and never as matches, good or otherwise.
- **repeats**: scenes showing an image an earlier scene already showed, and the
  most times any one image appears (D-140).

``--library`` measures against an existing library instead, with search off:
how the owner's recording fares against the Phase 4 library, for example.

Uses the live image services and the configured keys -- a few dozen requests a
recording -- and a fresh temporary library each time, so nothing from an earlier
run can flatter the numbers. ``--baseline`` also renders with search off.

    python scripts/measure_first_video.py --label "after query fix"
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

RECORDINGS = [
    REPO_ROOT / "samples" / "private" / "Recording (3).m4a",
    REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav",
    REPO_ROOT / "samples" / "public" / "fr_fable_cigale_45s.wav",
]

#: Phase 4's definition of a good match (D-069).
GOOD_SCORE = 0.25

RESULTS = REPO_ROOT / "demo_output" / "first_video_rounds.json"


def render(audio: Path, *, source: bool, library: Path | None = None) -> dict:
    from voxframe.config.settings import QualityPreset, get_settings
    from voxframe.config.style import get_template
    from voxframe.jobs.pipeline import JobOptions, run_pipeline
    from voxframe.render.encode.probe import probe_capabilities

    work = Path(tempfile.mkdtemp(prefix="vf-first-"))
    settings = get_settings().model_copy(
        update={
            # Empty, a brand-new user, unless an existing library is measured.
            "library_path": library or work / "library",
            "transcribe_model": "base",
        }
    )
    options = JobOptions(
        audio=audio,
        output=work / "out.mp4",
        quality=QualityPreset.DRAFT,
        height=480,
        library=library,
        source_imagery=source,
        languages=("en", "fr"),
    )

    started = time.monotonic()
    outcome = run_pipeline(
        options, settings, get_template("documentary"), probe_capabilities()
    )
    plan = outcome.plan
    assert plan is not None
    spoken = [scene for scene in plan.scenes if not scene.card_kind]
    atmospheric = [s for s in spoken if s.asset and s.asset_source == "atmospheric"]
    matched = [s for s in spoken if s.asset and s.asset_source != "atmospheric"]
    shown = [s.asset.id for s in spoken if s.asset]
    return {
        "scenes": len(spoken),
        "filled": len(matched),
        "good": sum(1 for s in matched if s.match_score >= GOOD_SCORE),
        "close": sum(1 for s in spoken if not s.asset and s.near_misses),
        "atmospheric": len(atmospheric),
        "repeats": len(shown) - len(set(shown)),
        "most_uses": max((shown.count(a) for a in shown), default=0),
        "seconds": round(time.monotonic() - started),
        "warnings": list(outcome.warnings),
        "scene_detail": [
            {
                "text": s.text[:60],
                "queries": list(s.queries),
                "outcome": (
                    "atmospheric" if s in atmospheric
                    else "filled" if s in matched
                    else "plain"
                ),
                "score": round(s.match_score, 3),
                "asset": s.asset.id[:12] if s.asset else None,
                "reason": s.match_reason[:70],
            }
            for s in spoken
        ],
    }


def pct(part: int, whole: int) -> str:
    return f"{part} ({part / whole:.0%})" if whole else "0"


def main() -> int:
    from voxframe.config.settings import get_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="unlabelled")
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--library", type=Path, default=None)
    parser.add_argument("--only", default=None, help="measure one recording by name")
    arguments = parser.parse_args()

    settings = get_settings()
    if arguments.library is None and not (
        settings.pexels_api_key or settings.pixabay_api_key
    ):
        print("No Pexels or Pixabay key configured; nothing to measure.")
        return 1

    rows = []
    for audio in RECORDINGS:
        if arguments.only and arguments.only not in audio.name:
            continue
        if not audio.is_file():
            print(f"skipping missing {audio.name}")
            continue
        print(f"\n{audio.name}")
        if arguments.baseline:
            off = render(audio, source=False)
            print(f"  search off: {off['filled']}/{off['scenes']}")
        if arguments.library is not None:
            on = render(audio, source=False, library=arguments.library.resolve())
        else:
            on = render(audio, source=True)
        print(
            f"  filled {on['filled']}/{on['scenes']}, good {on['good']}, "
            f"close {on['close']}, atmospheric {on['atmospheric']}, "
            f"repeats {on['repeats']} (most uses {on['most_uses']}), {on['seconds']}s"
        )
        for scene in on["scene_detail"]:
            print(
                f"    {scene['outcome']:11} {scene['score']:.3f} {scene['asset'] or '-':12} "
                f"{scene['text'][:36]!r:40} {scene['reason'][:44]}"
            )
        rows.append((audio.name, on))

    total = {key: sum(row[1][key] for row in rows)
             for key in ("scenes", "filled", "good", "close", "atmospheric", "repeats")}

    print(f"\n### {arguments.label}\n")
    print("| recording | scenes | filled | good | close matches | atmospheric | repeats |")
    print("|---|---|---|---|---|---|---|")
    for name, on in rows:
        print(
            f"| {name} | {on['scenes']} | {pct(on['filled'], on['scenes'])} "
            f"| {on['good']} | {on['close']} | {on['atmospheric']} | {on['repeats']} |"
        )
    print(
        f"| **total** | **{total['scenes']}** | **{pct(total['filled'], total['scenes'])}** "
        f"| **{pct(total['good'], total['filled'])} of filled** | {total['close']} "
        f"| {total['atmospheric']} | {total['repeats']} |"
    )

    history = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.is_file() else []
    history.append({"label": arguments.label, "total": total,
                    "rows": dict(rows)})
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(history, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
