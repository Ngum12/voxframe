"""Verify the Library screen in a real browser (D-146).

A new user with an empty library opens the Library, adds three photographs --
each credited to its real photographer under its real licence, read from the
file's own provenance, so nothing in the screenshots is misattributed -- makes
a video from the owner's recording, and then sees which videos show each
photograph and removes one.

    python scripts/verify_library.py
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_editing import (
    DEFAULT_AUDIO,
    SHOTS,
    Server,
    environment,
    wait_for_result,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PHASE_4 = REPO_ROOT / "demo_output" / "p4b" / "lib_rec" / "sourced"

#: Three photographs that matched the owner's recording in Phase 4.
PHOTOS = ("pexels_0030.jpg", "pexels_0000.jpg", "pixabay_0039.jpg")


def _credit(photo: Path) -> tuple[str, str]:
    """The photographer and licence, from the file's own provenance record."""
    sidecar = photo.with_name(photo.name + ".provenance.json")
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    license_info = record.get("license", record)  # older records are flat
    return license_info["author"], license_info["name"]


def run(audio: Path, failures: list[str]) -> None:
    from playwright.sync_api import sync_playwright

    root = Path(tempfile.mkdtemp(prefix="vf-library-"))
    env = environment(root, None)
    env["VOXFRAME_LIBRARY_PATH"] = str(root / "library")  # a new user's: none yet
    server = Server(env, root / "server.log")
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
            page.get_by_role("button", name="Library").click()
            page.get_by_text("Your library is empty").wait_for()
            page.screenshot(path=str(SHOTS / "phase-8-22-library-empty.png"), full_page=True)
            print("   empty library captured")

            for number, name in enumerate(PHOTOS, start=1):
                photo = PHASE_4 / name
                author, license_name = _credit(photo)
                page.set_input_files("#library-files", str(photo))
                page.fill("#library-author", author)
                page.fill("#library-license", license_name)
                page.get_by_role("button", name="Add to the library").click()
                # The grid growing is the signal; the message text is the same
                # for every upload, so it would be found before this one ends.
                page.wait_for_function(
                    "n => document.querySelectorAll('[aria-label=\"Library\"] li').length === n",
                    arg=number,
                    timeout=180_000,
                )
                print(f"   added {name}, credited to {author} ({license_name})")

            items = page.get_by_role("list", name="Library").locator("li")
            if items.count() != len(PHOTOS):
                failures.append(f"expected {len(PHOTOS)} items, found {items.count()}")
            page.screenshot(path=str(SHOTS / "phase-8-23-library.png"), full_page=True)

            # A video from the owner's recording, matched against these three.
            page.get_by_role("button", name="Make a video").click()
            page.wait_for_selector("text=Turn a recording into a video")
            page.set_input_files("input[type=file]", str(audio))
            page.wait_for_selector("text=How should it look?", timeout=120_000)
            page.click("text=Draft")
            page.fill("#title", "What Am I Here For")
            page.click("text=Make the video")
            if not wait_for_result(page, failures):
                return
            print("   video made")

            page.get_by_role("button", name="Library").click()
            page.get_by_role("list", name="Library").wait_for()
            used = page.get_by_role("list", name="Library").locator("li", has_text="In 1 video")
            print(f"   {used.count()} of {len(PHOTOS)} photographs are in the video")
            if used.count() == 0:
                failures.append("no photograph shows it is in the video")
                return
            used.first.locator("button.library-item").click()
            page.get_by_text("What Am I Here For", exact=False).last.wait_for()
            page.get_by_role("button", name="Remove from the library").click()
            page.get_by_role("alertdialog").wait_for()
            warning = page.get_by_role("alertdialog").inner_text()
            if "shown in 1 video" not in warning:
                failures.append(f"the removal did not warn about the video: {warning!r}")
            page.screenshot(path=str(SHOTS / "phase-8-24-library-remove.png"), full_page=True)
            page.get_by_role("button", name="Remove", exact=True).click()
            page.get_by_text("Removed from your library.").wait_for()
            try:
                page.wait_for_function(
                    "n => document.querySelectorAll('[aria-label=\"Library\"] li').length === n",
                    arg=len(PHOTOS) - 1,
                    timeout=15_000,
                )
            except Exception:
                pass  # reported below with the count actually shown
            left = page.get_by_role("list", name="Library").locator("li").count()
            print(f"   removed one; {left} left")
            if left != len(PHOTOS) - 1:
                failures.append(f"expected {len(PHOTOS) - 1} left, found {left}")

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
    print("== library ==")
    run(arguments.audio, failures)
    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nlibrary verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
