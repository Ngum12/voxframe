"""Check that a rendered video's word highlights match its own audio (D-144).

A wrapper around :mod:`voxframe.render.sync_check`, which the test suite also
runs on a real render with a title and a chapter card.

    python scripts/check_av_sync.py video.mp4 video.ass --plan video.plan.json

Exits non-zero if any stretch between cards is off by more than
``--tolerance`` seconds (median).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voxframe.render.sync_check import (
    card_boundaries,
    heard,
    highlights,
    offsets,
    stretches,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("ass", type=Path)
    parser.add_argument("--plan", type=Path, default=None)
    parser.add_argument("--language", default="en")
    parser.add_argument("--model", default="base")
    parser.add_argument("--tolerance", type=float, default=0.5)
    arguments = parser.parse_args()

    found = offsets(
        highlights(arguments.ass), heard(arguments.video, arguments.language, arguments.model)
    )
    if not found:
        print("no highlighted words could be located in the audio")
        return 1

    worst = 0.0
    for stretch in stretches(found, card_boundaries(arguments.plan)):
        worst = max(worst, abs(stretch.median))
        flag = "  <-- off" if abs(stretch.median) > arguments.tolerance else ""
        label = (
            f"{stretch.start:7.1f}s onwards"
            if stretch.end == float("inf")
            else f"{stretch.start:7.1f}s-{stretch.end:.1f}s"
        )
        print(f"  {label:22} {stretch.words:4} words  median {stretch.median:+.2f}s{flag}")
    print(f"\n{len(found)} words located; worst stretch median {worst:.2f}s")
    return 0 if worst <= arguments.tolerance else 1


if __name__ == "__main__":
    sys.exit(main())
