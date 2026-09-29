"""Verify searching online for one scene, in a real browser (D-142).

Starts a real server with an empty library, agrees to online search on the
consent screen, renders a recording, then for one scene: opens the search,
searches the live image services, checks the previews load, uses a result, and
re-renders -- then checks the chosen image reached the plan, the disk and the
credits.

The server reads the developer's keys from ``.env`` as the app normally does.
No key is typed into the page, written to the temporary config, logged or
shown in a screenshot; the settings screen is not captured.

    python scripts/verify_search.py --audio samples/public/en_sonnet_january_45s.wav
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_editing import (
    SECRET_ENVIRONMENT,
    SHOTS,
    Server,
    environment,
    plan_on_disk,
    wait_for_result,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIO = REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav"

#: The scene searched, by plan index.
SCENE = 3


def search_environment(root: Path) -> dict[str, str]:
    """The editing environment, minus the key masking: search needs a key.

    Removing the masked variables lets the server's settings read ``.env``
    from the repository, exactly as a developer's own run does.
    """
    env = environment(root, None)
    env["VOXFRAME_LIBRARY_PATH"] = str(root / "library")  # empty: a new user
    for name in SECRET_ENVIRONMENT:
        env.pop(name, None)
    return env


def run(audio: Path, failures: list[str]) -> None:
    from playwright.sync_api import sync_playwright

    root = Path(tempfile.mkdtemp(prefix="vf-search-"))
    server = Server(search_environment(root), root / "server.log")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))

            page.goto(server.url, wait_until="networkidle")
            page.get_by_text("Should Voxframe look for images online?").wait_for()
            page.get_by_role("button", name="Search for images").click()
            page.wait_for_selector("text=Turn a recording into a video")
            page.set_input_files("input[type=file]", str(audio))
            page.wait_for_selector("text=How should it look?", timeout=120_000)
            page.click("text=Draft")
            page.fill("#title", "January")
            page.click("text=Make the video")
            if not wait_for_result(page, failures):
                return
            print("   first render done")

            page.click("text=See how it was made")
            page.wait_for_selector("text=The scene plan", timeout=30_000)
            # The third spoken scene, "A winter, frozen pulse...": the title card
            # is first, and the second scene is a reader credit, which has no
            # search terms of its own since D-141.
            page.locator("button.scene").nth(SCENE).click()
            page.get_by_role("button", name="Search online").click()

            box = page.get_by_role("searchbox")
            prefilled = box.input_value()
            if not prefilled:
                failures.append("the search box was not prefilled from the scene")
            print(f"   prefilled with {prefilled!r}")
            box.fill("frozen lake in winter")
            page.get_by_role("button", name="Search", exact=True).click()

            results = page.get_by_role("list", name="Search results")
            results.wait_for(timeout=60_000)
            count = results.locator("li").count()
            print(f"   {count} results")
            if count == 0:
                failures.append("the search returned nothing")
                return

            # Every preview must actually load: they come through the server.
            page.wait_for_function(
                """() => [...document.querySelectorAll('[aria-label="Search results"] img')]
                         .every(img => img.complete)""",
                timeout=60_000,
            )
            broken = page.evaluate(
                """() => [...document.querySelectorAll('[aria-label="Search results"] img')]
                         .filter(img => img.naturalWidth === 0).length"""
            )
            if broken:
                failures.append(f"{broken} of {count} previews did not load")
            credit = results.locator(".candidate-note").first.inner_text()
            print(f"   first result: {credit}")
            page.screenshot(path=str(SHOTS / "phase-8-18-search.png"), full_page=True)

            results.get_by_role("button", name="Use this").first.click()
            page.get_by_text("Update the video").wait_for(timeout=120_000)
            page.wait_for_timeout(1500)
            page.screenshot(path=str(SHOTS / "phase-8-19-search-used.png"), full_page=True)

            scene = plan_on_disk(root)["scenes"][SCENE]
            asset = scene.get("asset") or {}
            if scene.get("asset_source") != "user":
                failures.append("the chosen result is not recorded as the person's choice")
            if asset.get("license_source") not in {"pexels", "pixabay", "openverse"}:
                failures.append(f"unexpected provenance: {asset.get('license_source')!r}")
            if not Path(asset.get("path", "")).is_file():
                failures.append("the chosen image is not on disk")
            author = asset.get("license_author", "")
            print(f"   used: {author} via {asset.get('license_source')}")

            page.get_by_role("button", name="Update the video").click()
            started = time.monotonic()
            if not wait_for_result(page, failures):
                return
            print(f"   re-render: {time.monotonic() - started:.0f}s")
            credit_lines = page.locator(".list-plain").inner_text()
            if author and author not in credit_lines:
                failures.append(f"{author!r} is missing from the credits")
            else:
                print("   credited on the result page")

            if errors:
                failures.append(f"page errors: {errors}")
            browser.close()
    finally:
        server.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    arguments = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")  # credits use a middle dot
    failures: list[str] = []
    print("== search ==")
    run(arguments.audio, failures)
    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nsearch verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
