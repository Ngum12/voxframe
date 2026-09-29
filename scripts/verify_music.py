"""Verify adding a music bed in the browser (D-148).

Generates a track -- a soft chord, so no licence question arises -- then, as a
user would: uploads the sonnet, attaches the track with a credit on the settings
screen, makes the video, and checks the credit on the result page and in the
plan, and that the music is in the soundtrack. The recording opens with 1.7s of
silence before the first word, so a bed is measurable there: with music it has
level, without it is silent.

    python scripts/verify_music.py
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_editing import SHOTS, Server, environment, wait_for_result

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIO = REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav"
CREDIT = "A soft chord, generated for this test"


def _chord(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=60",
            "-f", "lavfi", "-i", "sine=frequency=277:duration=60",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=60",
            "-filter_complex", "amix=inputs=3,volume=0.5",
            "-c:a", "libmp3lame", "-q:a", "5", str(path),
        ],
        check=True,
    )


def _opening_level(video: Path) -> float:
    """Mean volume, in dB, of the first second: before anyone speaks."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-t", "1", "-i", str(video),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    match = re.search(r"mean_volume: (-?[\d.]+) dB", result.stderr)
    return float(match.group(1)) if match else -120.0


def run(audio: Path, failures: list[str]) -> None:
    from playwright.sync_api import sync_playwright

    root = Path(tempfile.mkdtemp(prefix="vf-music-"))
    track = root / "Evening Chord.mp3"
    _chord(track)
    server = Server(environment(root, None), root / "server.log")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))

            page.goto(server.url, wait_until="networkidle")
            consent = page.get_by_text("Should Voxframe look for images online?")
            if consent.count():
                page.click("text=Not now")
            page.wait_for_selector("text=Turn a recording into a video")
            page.set_input_files("input[type=file]", str(audio))
            page.wait_for_selector("text=How should it look?", timeout=120_000)
            page.click("text=Draft")

            page.set_input_files("#music-file", str(track))
            page.get_by_text("Evening Chord.mp3").wait_for(timeout=60_000)
            page.fill("#music-credit", CREDIT)
            page.screenshot(path=str(SHOTS / "phase-8-25-music.png"), full_page=True)
            print("   music attached")

            page.click("text=Make the video")
            if not wait_for_result(page, failures):
                return
            credit_lines = page.locator(".list-plain").inner_text()
            if f"Music: {CREDIT}" not in credit_lines:
                failures.append(f"the music credit is missing: {credit_lines!r}")
            else:
                print("   credited on the result page")

            plans = sorted((root / "out").rglob("*.plan.json"), key=lambda p: p.stat().st_mtime)
            plan = json.loads(plans[-1].read_text(encoding="utf-8"))
            if plan.get("music_credit") != CREDIT or not plan.get("music_path"):
                failures.append("the plan does not record the music")
            video = next(plans[-1].parent.glob("*.mp4"))
            level = _opening_level(video)
            print(f"   level before the first word: {level:.1f} dB")
            if level < -60:
                failures.append(f"no music in the opening second ({level:.1f} dB)")

            if errors:
                failures.append(f"page errors: {errors}")
            browser.close()
    finally:
        server.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    arguments = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    failures: list[str] = []
    print("== music ==")
    run(arguments.audio, failures)
    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nmusic verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
