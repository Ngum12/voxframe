"""Verify editing title and chapter cards, in a real browser (D-145).

Renders a recording with no title, then in the scene plan: adds a title,
starts a chapter before a scene, rewords the chapter, and updates the video.
Checks the plan on disk, saves frames from inside both cards for inspection,
and checks that the words still line up with the speech after the cards moved
everything (D-144) using ``check_av_sync``. Finally removes the chapter again,
to check a removal leaves a whole plan.

    python scripts/verify_cards.py --library demo_output/p4b/lib_rec
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_editing import (
    SHOTS,
    Server,
    environment,
    wait_for_result,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIO = REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav"

#: The scene a chapter is started before, by plan index once the title exists.
CHAPTER_BEFORE = 3


def _output(root: Path) -> Path:
    plans = sorted((root / "out").rglob("*.plan.json"), key=lambda p: p.stat().st_mtime)
    return plans[-1].parent


def _frame(video: Path, seconds: float):  # type: ignore[no-untyped-def]
    """One frame as an array, the top 55% only: captions change below."""
    import io

    import numpy as np
    from PIL import Image

    png = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", f"{seconds:.3f}", "-i", str(video),
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, check=True,
    ).stdout
    image = np.asarray(Image.open(io.BytesIO(png)).convert("L"), dtype=float)
    return image[: int(image.shape[0] * 0.55)]


def _movement(video: Path, scene: dict, fps: float) -> float:  # type: ignore[type-arg]
    """Mean pixel change between a scene's early and late frames."""
    start = scene["start_frame"] / fps + 0.6
    end = scene["end_frame"] / fps - 0.6
    return float(abs(_frame(video, start) - _frame(video, end)).mean())


def run(audio: Path, library: Path, failures: list[str]) -> None:
    from playwright.sync_api import sync_playwright

    root = Path(tempfile.mkdtemp(prefix="vf-cards-"))
    server = Server(environment(root, library), root / "server.log")
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
            page.click("text=Make the video")
            if not wait_for_result(page, failures):
                return
            print("   first render done, no title")

            page.click("text=See how it was made")
            page.wait_for_selector("text=The scene plan", timeout=30_000)

            # 1. A title, added afterwards.
            page.get_by_role("button", name="Add a title card").click()
            page.get_by_placeholder("The title, as it should appear").fill("A Calendar of Sonnets")
            page.get_by_role("button", name="Add", exact=True).click()
            page.get_by_label("What the title card says").wait_for(timeout=15_000)
            print("   title added")

            # 2. A chapter before a later scene, then reworded.
            page.locator("button.scene").nth(CHAPTER_BEFORE).click()
            page.get_by_role("button", name="Start a chapter here").click()
            field = page.get_by_label("What the chapter card says")
            field.wait_for(timeout=15_000)
            print(f"   chapter added, defaulting to {field.input_value()!r}")
            field.fill("Winter")
            page.get_by_role("button", name="Save", exact=True).click()
            page.wait_for_timeout(800)
            page.screenshot(path=str(SHOTS / "phase-8-20-cards.png"), full_page=True)

            plan = json.loads(next(_output(root).glob("*.plan.json")).read_text(encoding="utf-8"))
            kinds = [(s["card_kind"], s["card_text"]) for s in plan["scenes"] if s["card_kind"]]
            print(f"   cards in the plan: {kinds}")
            if kinds != [("title", "A Calendar of Sonnets"), ("chapter", "Winter")]:
                failures.append(f"unexpected cards: {kinds}")

            # 2b. Camera movement off for one photograph (D-154).
            still = next(
                s for s in plan["scenes"]
                if not s["card_kind"] and s.get("asset") and s["asset"]["kind"] == "image"
            )
            page.locator("button.scene").nth(still["index"]).click()
            # Controlled by the saved plan: it flips when the server answers.
            switch = page.get_by_label("Camera movement")
            switch.click()
            page.wait_for_function(
                "() => !document.querySelector('input[type=checkbox]:checked')"
                " || ![...document.querySelectorAll('label.toggle')].some("
                "l => l.textContent.includes('Camera movement')"
                " && l.querySelector('input').checked)",
                timeout=15_000,
            )
            print(f"   camera movement off for scene index {still['index']}")

            # 3. Update the video.
            page.get_by_role("button", name="Update the video").click()
            started = time.monotonic()
            if not wait_for_result(page, failures):
                return
            print(f"   re-render: {time.monotonic() - started:.0f}s")

            output = _output(root)
            plan = json.loads(next(output.glob("*.plan.json")).read_text(encoding="utf-8"))
            video = next(output.glob("*.mp4"))
            fps = plan["fps"]
            for scene in plan["scenes"]:
                if not scene["card_kind"]:
                    continue
                middle = (scene["start_frame"] + scene["end_frame"]) / 2 / fps
                frame = SHOTS / f"phase-8-21-{scene['card_kind']}-frame.png"
                subprocess.run(
                    ["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{middle:.2f}",
                     "-i", str(video), "-frames:v", "1", str(frame)],
                    check=True,
                )
                print(f"   {scene['card_kind']} frame at {middle:.1f}s saved")

            motion = {s["index"]: s["motion"] for s in plan["scenes"]}
            if motion.get(still["index"]) != "none":
                failures.append("the switch did not reach the plan")
            moving = next(
                (s for s in plan["scenes"]
                 if s.get("asset") and s["asset"]["kind"] == "image"
                 and s["motion"] == "ken_burns"),
                None,
            )
            still_change = _movement(video, still, fps)
            print(f"   still scene changes by {still_change:.1f} between its first and last frame")
            if still_change > 2.0:
                failures.append(f"the still scene moved ({still_change:.1f})")
            if moving is not None:
                moving_change = _movement(video, moving, fps)
                print(f"   a moving scene changes by {moving_change:.1f}")
                if moving_change <= still_change:
                    failures.append("a moving scene moved no more than the still one")

            sync = subprocess.run(
                [sys.executable, str(REPO_ROOT / "scripts" / "check_av_sync.py"),
                 str(video), str(next(output.glob("*.ass"))),
                 "--plan", str(next(output.glob("*.plan.json")))],
                capture_output=True, text=True, check=False,
            )
            print("   " + "\n   ".join(sync.stdout.strip().splitlines()))
            if sync.returncode != 0:
                failures.append("words and speech are out of step after the cards")

            # 4. Remove the chapter again.
            page.click("text=See how it was made")
            page.wait_for_selector("text=The scene plan", timeout=30_000)
            chapter = next(s["index"] for s in plan["scenes"] if s["card_kind"] == "chapter")
            page.locator("button.scene").nth(chapter).click()
            page.get_by_role("button", name="Remove this card").click()
            page.wait_for_timeout(1500)
            plan = json.loads(next(_output(root).glob("*.plan.json")).read_text(encoding="utf-8"))
            if any(s["card_kind"] == "chapter" for s in plan["scenes"]):
                failures.append("the chapter was not removed")
            else:
                print("   chapter removed; plan still whole")

            if errors:
                failures.append(f"page errors: {errors}")
            browser.close()
    finally:
        server.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--library", type=Path, required=True)
    arguments = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    failures: list[str] = []
    print("== cards ==")
    run(arguments.audio, arguments.library, failures)
    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\ncards verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
