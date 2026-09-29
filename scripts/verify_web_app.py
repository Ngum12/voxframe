"""Drive the web app in a real browser, on real audio, and screenshot it.

Unit tests cover the API and the guards. They cannot tell whether the page
actually renders, whether the token exchange works in a real browser, whether
the cookie is set and the URL cleaned, or whether the screens are legible. This
does, end to end: upload a real recording, watch it render, download the result.

Run it directly:

    python scripts/verify_web_app.py [--audio PATH] [--out DIR]

Screenshots land in ``docs/phases/`` by default, which is where the step report
references them.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

DEFAULT_AUDIO = REPO_ROOT / "samples" / "private" / "demo_tts.wav"
DEFAULT_OUT = REPO_ROOT / "docs" / "phases"

#: Render settings chosen so the whole check finishes in about a minute:
#: a cached model, draft quality, and a small frame.
RENDER_MODEL = "base"
RENDER_HEIGHT = 480


def _start_server(port: int, config_dir: Path, library: Path | None = None):
    """Start a real server in a thread, isolated from the user's own config."""
    import os

    os.environ["VOXFRAME_CONFIG_DIR"] = str(config_dir)
    if library is not None:
        # With a library the filmstrip shows real thumbnails, which is the
        # only way its screenshots say anything. Without one every scene is a
        # gradient and the check still runs, but proves less.
        os.environ["VOXFRAME_LIBRARY_PATH"] = str(library.resolve())
    # A cached, small model, so this check exercises the app rather than
    # spending twenty minutes downloading weights. The pipeline is identical.
    os.environ.setdefault("VOXFRAME_TRANSCRIBE_MODEL", RENDER_MODEL)

    # Mask the developer's own keys, so the screenshots show what a NEW user
    # sees: empty, editable key fields. With a real `.env` present the UI
    # correctly disables them and says the environment takes precedence --
    # correct behaviour, and a misleading screenshot.
    for name in (
        "VOXFRAME_PEXELS_API_KEY",
        "VOXFRAME_PIXABAY_API_KEY",
        "VOXFRAME_UNSPLASH_ACCESS_KEY",
    ):
        os.environ[name] = ""

    from voxframe.api.serve import build_server, serve

    handle = build_server(port=port)
    thread = threading.Thread(target=serve, args=(handle,), daemon=True)
    thread.start()

    import http.client

    for _ in range(120):
        try:
            connection = http.client.HTTPConnection(handle.host, handle.port, timeout=2)
            connection.request("GET", "/api/health")
            if connection.getresponse().status == 200:
                connection.close()
                return handle
        except Exception:
            time.sleep(0.25)

    raise RuntimeError("server never answered")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--port", type=int, default=8797)
    parser.add_argument(
        "--library",
        type=Path,
        default=None,
        help="An image library, so the filmstrip shows real thumbnails.",
    )
    parser.add_argument(
        "--headed", action="store_true", help="Show the browser window."
    )
    arguments = parser.parse_args()

    if not arguments.audio.is_file():
        print(f"No audio at {arguments.audio}")
        return 1

    from voxframe.api.app import static_root

    if not (static_root() / "index.html").is_file():
        print("The web app is not built. Run: cd web && npm install && npm run build")
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. pip install playwright")
        return 1

    import tempfile

    arguments.out.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as temporary:
        handle = _start_server(
            arguments.port, Path(temporary) / "config", arguments.library
        )
        shots = arguments.out

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not arguments.headed)
            # A laptop screen, which is what the brief asks it to be readable on.
            page = browser.new_page(viewport={"width": 1280, "height": 860})

            errors: list[str] = []
            page.on("pageerror", lambda exception: errors.append(str(exception)))
            page.on(
                "console",
                lambda message: errors.append(f"console.{message.type}: {message.text}")
                if message.type == "error"
                else None,
            )

            # --- 1. first load, carrying the launch token ---
            print("1. opening", handle.url.replace(handle.token.value, "<token>"))
            page.goto(handle.url, wait_until="networkidle")

            # The token must be gone from the address bar by now.
            if "token=" in page.url:
                failures.append(f"token still in URL: {page.url}")
            else:
                print("   token removed from the URL")

            cookies = {c["name"]: c for c in page.context.cookies()}
            session = cookies.get("voxframe_session")
            if not session:
                failures.append("no session cookie was set")
            else:
                if not session.get("httpOnly"):
                    failures.append("session cookie is not HttpOnly")
                if str(session.get("sameSite", "")).lower() != "strict":
                    failures.append("session cookie is not SameSite=Strict")
                print("   session cookie: HttpOnly, SameSite=Strict")

            # A script must not be able to read it.
            visible = page.evaluate("document.cookie")
            if "voxframe_session" in visible:
                failures.append("session cookie is readable from JavaScript")

            # --- 2. the consent screen, shown once on a fresh config ---
            page.wait_for_selector("text=Should Voxframe look for images online?")
            page.screenshot(path=str(shots / "phase-8-01-consent.png"), full_page=True)
            print("2. consent screen captured")

            page.click("text=Not now")

            # --- 3. upload ---
            page.wait_for_selector("text=Turn a recording into a video")
            page.screenshot(path=str(shots / "phase-8-02-upload.png"), full_page=True)
            print("3. upload screen captured")

            page.set_input_files("input[type=file]", str(arguments.audio))

            # --- 4. settings ---
            page.wait_for_selector("text=How should it look?", timeout=60_000)
            page.click("text=Draft")
            page.fill("#title", "A Walk Through Voxframe")
            page.screenshot(path=str(shots / "phase-8-03-settings.png"), full_page=True)
            print("4. settings screen captured")

            page.click("text=Make the video")

            # --- 5. progress ---
            page.wait_for_selector("text=Making your video", timeout=30_000)
            page.wait_for_timeout(1500)
            page.screenshot(path=str(shots / "phase-8-04-progress.png"), full_page=True)
            print("5. progress screen captured")

            # --- 6. result ---
            # Wait for success OR a visible failure. Waiting only for success
            # meant a failed render looked identical to a slow one until the
            # timeout -- which is how a real failure hid for half an hour
            # (D-124). The error notice is the user-visible failure path, so it
            # is also what this watches.
            #
            # Composed with or_(): a comma inside a "text=" selector is not a
            # list of alternatives, it is part of the text being matched -- so
            # an earlier version of this waited for a heading that could never
            # exist and timed out exactly as before (D-124).
            page.get_by_text("Your video is ready").or_(
                page.locator(".notice-error")
            ).first.wait_for(timeout=900_000)
            if page.locator(".notice-error").count():
                message = page.locator(".notice-error").first.inner_text()
                failures.append(f"render failed in the UI: {message[:200]}")
                page.screenshot(
                    path=str(shots / "phase-8-FAILED.png"), full_page=True
                )
                browser.close()
                print("FAILURES:")
                for failure in failures:
                    print("  -", failure)
                return 1
            page.wait_for_timeout(1200)
            page.screenshot(path=str(shots / "phase-8-05-result.png"), full_page=True)
            print("6. result screen captured")

            if not page.locator("video").count():
                failures.append("no video element on the result screen")

            # --- 6b. the filmstrip ---
            page.click("text=See how it was made")
            page.wait_for_selector("text=The scene plan", timeout=30_000)
            page.wait_for_timeout(2500)  # lazy thumbnails
            scene_count = page.locator(".scene").count()
            thumbnails = page.locator(".scene-frame img").count()
            empties = page.locator(".scene-empty").count()
            print(
                f"6b. filmstrip: {scene_count} scenes, {thumbnails} thumbnails, "
                f"{empties} explained-empty"
            )
            if scene_count == 0:
                failures.append("the filmstrip rendered no scenes")
            if arguments.library is not None and thumbnails == 0:
                failures.append("a library was given but no thumbnail loaded")
            # Every empty scene must say why; a bare rectangle is the failure
            # the filmstrip exists to prevent (D-123).
            unexplained = page.evaluate(
                "[...document.querySelectorAll('.scene-empty-why')]"
                ".filter(e => !e.textContent.trim()).length"
            )
            if unexplained:
                failures.append(f"{unexplained} empty scenes give no reason")
            if arguments.library is not None:
                page.screenshot(
                    path=str(shots / "phase-8-07-filmstrip.png"), full_page=True
                )
                # One scene opened, to show its detail panel.
                page.locator("button.scene").nth(2).click()
                page.wait_for_timeout(600)
                page.screenshot(
                    path=str(shots / "phase-8-08-filmstrip-scene.png"), full_page=True
                )
            page.click("text=Back to the video")
            page.wait_for_selector("text=Your video is ready")

            # --- 7. settings / keys ---
            page.click("nav >> text=Settings")
            page.wait_for_selector("text=Imagery sourcing")
            page.screenshot(
                path=str(shots / "phase-8-06-preferences.png"), full_page=True
            )
            print("7. settings screen captured")

            # --- 8. keyboard reachability ---
            # Every control must be operable without a mouse. Walking the tab
            # order and counting stops is a coarse check, but a screen with no
            # reachable controls fails it outright.
            page.keyboard.press("Tab")
            stops = 0
            for _ in range(25):
                tag = page.evaluate(
                    "document.activeElement ? document.activeElement.tagName : ''"
                )
                if tag in {"BUTTON", "A", "INPUT", "SELECT", "VIDEO"}:
                    stops += 1
                page.keyboard.press("Tab")
            if stops < 5:
                failures.append(f"only {stops} keyboard stops found on settings")
            else:
                print(f"8. keyboard: {stops} focusable stops reached")

            if errors:
                failures.append(f"browser errors: {errors[:3]}")

            browser.close()

    print()
    if failures:
        print("FAILURES:")
        for failure in failures:
            print("  -", failure)
        return 1

    print("All checks passed. Screenshots in", arguments.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
