"""Verify the first-run model download in a real browser (D-157).

Starts a server whose model cache is an empty temporary folder -- a new user's
machine, as far as the models are concerned -- and, in the browser: sees the
Getting-ready screen before anything else, chooses Lite, downloads the real
models (about 750 MB), and watches the measured progress reach the end. Then
checks the models are in that folder and the screen moved on. The folder is
deleted afterwards.

    python scripts/verify_first_run.py
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_editing import SHOTS, Server, environment


def run(failures: list[str]) -> None:
    from playwright.sync_api import sync_playwright

    root = Path(tempfile.mkdtemp(prefix="vf-firstrun-"))
    env = environment(root, None)
    env["HF_HUB_CACHE"] = str(root / "models")
    env.pop("HF_HOME", None)
    server = Server(env, root / "server.log")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))

            page.goto(server.url, wait_until="networkidle")
            page.get_by_role("heading", name="Getting ready").wait_for(timeout=30_000)
            page.screenshot(path=str(SHOTS / "phase-9-01-getting-ready.png"), full_page=True)
            print("   Getting ready shown first")

            page.get_by_text("Lite — smallest download").click()
            page.get_by_role("button", name="Download 750 MB").click()
            started = time.monotonic()
            page.get_by_role("status").filter(has_text="of 750 MB").wait_for(timeout=60_000)

            captured = False
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                if page.get_by_text("Ready. Everything now runs on this computer.").count():
                    break
                if page.get_by_role("button", name="Try again").count():
                    failures.append("the download failed")
                    page.screenshot(path=str(SHOTS / "phase-9-FAILED.png"), full_page=True)
                    return
                status = page.get_by_role("status").filter(has_text="of 750 MB")
                if not captured and status.count():
                    text = status.inner_text()
                    percent = re.search(r"\((\d+)%\)", text)
                    if percent and int(percent.group(1)) >= 30:
                        shot = SHOTS / "phase-9-02-downloading.png"
                        page.screenshot(path=str(shot), full_page=True)
                        print(f"   mid-download: {text}")
                        captured = True
                time.sleep(1)
            else:
                failures.append("the download did not finish within 30 minutes")
                return
            print(f"   downloaded in {time.monotonic() - started:.0f}s")
            page.screenshot(path=str(SHOTS / "phase-9-03-ready.png"), full_page=True)

            real = {path.resolve() for path in (root / "models").rglob("*")}
            held = sum(path.stat().st_size for path in real if path.is_file()) / 1_000_000
            print(f"   {held:.0f} MB in the new model folder")
            if held < 700:
                failures.append(f"only {held:.0f} MB arrived")

            page.get_by_role("button", name="Continue").click()
            page.get_by_text("Should Voxframe look for images online?").wait_for(timeout=15_000)
            print("   then the consent question, as before")

            if errors:
                failures.append(f"page errors: {errors}")
            browser.close()
    finally:
        server.kill()
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    failures: list[str] = []
    print("== first run ==")
    run(failures)
    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nfirst run verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
