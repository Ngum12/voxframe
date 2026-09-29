"""Verify editing and interrupted-render recovery in a real browser, on real audio.

Two scenarios, each against a server running as its own process -- so the
second can kill it for real:

1. **Editing.** Render a recording against an image library, open the scene
   plan, use a near miss anyway, and update the video. Checks that the change
   reaches the rendered plan, that only changed scenes are rendered again, and
   that the page says so.

2. **Interruption** (D-129). Start a render, kill the server process mid-render
   with no chance to clean up, start a new server on the same data, and check
   that the page offers to resume -- and that resuming finishes the video.

Screenshots land in ``docs/phases/`` for the step report.

    python scripts/verify_editing.py --library demo_output/p4b/lib_rec
        --audio samples/public/en_sonnet_january_45s.wav

The editing scenario needs a recording that leaves plain scenes with close
matches. Since D-136 the owner's recording fills every scene from that
library, so use the sonnet (D-138).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIO = REPO_ROOT / "samples" / "private" / "Recording (3).m4a"
SHOTS = REPO_ROOT / "docs" / "phases"

#: The developer's own keys are masked, as in verify_web_app.py.
SECRET_ENVIRONMENT = (
    "VOXFRAME_PEXELS_API_KEY",
    "VOXFRAME_PIXABAY_API_KEY",
    "VOXFRAME_UNSPLASH_ACCESS_KEY",
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Server:
    """``voxframe web`` in a child process, which this script can kill."""

    def __init__(self, environment: dict[str, str], log: Path) -> None:
        self.port = free_port()
        self.log = log
        self.process = subprocess.Popen(
            [
                sys.executable, "-m", "voxframe.cli.main", "web",
                "--no-open", "--port", str(self.port),
            ],
            cwd=REPO_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.url = self._read_url()
        # Keep reading, or a chatty server fills the pipe and stalls mid-render.
        import threading

        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        assert self.process.stdout is not None
        with self.log.open("a", encoding="utf-8") as handle:
            for line in self.process.stdout:
                handle.write(line)
                handle.flush()

    def segment_reuse(self) -> list[str]:
        """Every "N/M segments reused" the server has logged, in order."""
        if not self.log.is_file():
            return []
        return re.findall(
            r"cache='(\d+/\d+) segments reused'", self.log.read_text(encoding="utf-8")
        )

    def _read_url(self) -> str:
        assert self.process.stdout is not None
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                if self.process.poll() is not None:
                    raise RuntimeError("the server exited before printing its URL")
                continue
            match = re.search(r"(http://127\.0\.0\.1:\d+/\?token=\S+)", line)
            if match:
                return match.group(1)
        raise RuntimeError("the server did not print its URL")

    @property
    def base(self) -> str:
        return self.url.split("?", 1)[0]

    def kill(self) -> None:
        """A hard kill: no handler, no finally, nothing recorded."""
        self.process.kill()
        self.process.wait(timeout=15)


def environment(root: Path, library: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["PYTHONUNBUFFERED"] = "1"
    env["VOXFRAME_CONFIG_DIR"] = str(root / "config")
    env["VOXFRAME_OUTPUT_PATH"] = str(root / "out")
    env["VOXFRAME_TRANSCRIBE_MODEL"] = "base"
    env["VOXFRAME_LANGUAGES"] = "en,fr"
    # Info level, so the log records how many segments each render reused --
    # the only honest measure of "only the changed scenes are rendered again"
    # when the cache is already warm from earlier runs.
    env["VOXFRAME_LOG_LEVEL"] = "info"
    if library is not None:
        env["VOXFRAME_LIBRARY_PATH"] = str(library.resolve())
    for name in SECRET_ENVIRONMENT:
        env[name] = ""
    return env


def through_to_settings(page, url: str, audio: Path, title: str) -> None:
    page.goto(url, wait_until="networkidle")
    consent = page.get_by_text("Should Voxframe look for images online?")
    if consent.count():
        page.click("text=Not now")
    page.wait_for_selector("text=Turn a recording into a video")
    page.set_input_files("input[type=file]", str(audio))
    page.wait_for_selector("text=How should it look?", timeout=120_000)
    page.click("text=Draft")
    page.fill("#title", title)


def wait_for_result(page, failures: list[str], timeout: int = 900_000) -> bool:
    page.get_by_text("Your video is ready").or_(page.locator(".notice-error")).first.wait_for(
        timeout=timeout
    )
    if page.locator(".notice-error").count():
        failures.append(
            f"render failed: {page.locator('.notice-error').first.inner_text()[:200]}"
        )
        return False
    return True


def plan_on_disk(root: Path) -> dict:
    plans = sorted((root / "out").rglob("*.plan.json"), key=lambda p: p.stat().st_mtime)
    return json.loads(plans[-1].read_text(encoding="utf-8"))


def scenario_editing(playwright, audio: Path, library: Path, failures: list[str]) -> None:
    print("== editing ==")
    root = Path(tempfile.mkdtemp(prefix="vf-edit-"))
    server = Server(environment(root, library), root / "server.log")
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    try:
        through_to_settings(page, server.url, audio, "What Am I Here For")
        page.click("text=Make the video")
        started = time.monotonic()
        if not wait_for_result(page, failures):
            return
        first_seconds = time.monotonic() - started
        print(f"   first render: {first_seconds:.0f}s")

        # The result screen says when plain scenes are one click away (D-138).
        one_click = page.get_by_text("one click from an image")
        if one_click.count() == 0:
            failures.append("the result screen did not mention the close matches")
        page.screenshot(path=str(SHOTS / "phase-8-17-one-click.png"), full_page=True)
        print("   one-click notice captured")

        page.click("text=See how it was made")
        page.wait_for_selector("text=The scene plan", timeout=30_000)
        page.wait_for_timeout(2500)

        use_anyway = page.get_by_role("button", name="Use this image anyway")
        if use_anyway.count() == 0:
            failures.append("no near miss was offered on any scene")
            page.screenshot(path=str(SHOTS / "phase-8-FAILED-editing.png"), full_page=True)
            return

        # The filmstrip opens on the first scene with close matches.
        page.screenshot(path=str(SHOTS / "phase-8-09-near-miss.png"), full_page=True)
        print("   near-miss scene captured")

        page.locator("details.scene-details summary").click()
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "phase-8-10-details.png"), full_page=True)
        page.locator("details.scene-details summary").click()
        print("   details captured")

        # A candidate already shown in another scene is offered but listed
        # after the unused ones, so a one-click choice is not a repeat (D-127).
        flags = page.locator(".candidates").first.locator(".candidate").evaluate_all(
            "items => items.map(i => i.dataset.used)"
        )
        repeats = page.locator(".candidate-note").count()
        print(f"   close matches: {len(flags)}, already used elsewhere: {repeats}")
        if "false" in flags and flags[0] != "false":
            failures.append("a repeated image was offered ahead of an unused one")

        scene_heading = page.locator("#scene-heading").inner_text()
        use_anyway.first.click()
        page.wait_for_selector(".pending-bar", timeout=15_000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(SHOTS / "phase-8-11-edited.png"), full_page=True)
        print(f"   used a near miss on {scene_heading}; pending bar shown")

        edited = [
            scene["index"]
            for scene in plan_on_disk(root)["scenes"]
            if scene.get("asset_source") == "user"
        ]
        if len(edited) != 1:
            failures.append(f"expected one edited scene on disk, found {edited}")

        # --- correct a misheard word on the same scene ------------------
        heard = page.locator(".scene-detail-text").first.inner_text()
        corrected = heard.replace("people goals", "people's goals", 1)
        if corrected == heard:
            corrected = heard + " (corrected)"
        page.get_by_role("button", name="Edit caption").click()
        page.fill("textarea", corrected)
        page.get_by_role("button", name="Save caption").click()
        page.get_by_text("Originally heard as").wait_for(timeout=15_000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / "phase-8-15-caption.png"), full_page=True)
        print(f"   corrected a caption: {heard[:40]!r} -> {corrected[:40]!r}")
        if "2 changes" not in page.locator(".pending-bar").inner_text():
            failures.append("the pending bar does not count the caption edit")

        saved = next(
            scene for scene in plan_on_disk(root)["scenes"]
            if scene.get("caption_text")
        )
        edited_scene = saved

        page.click("text=Update the video")
        started = time.monotonic()
        if not wait_for_result(page, failures):
            return
        rerender_seconds = time.monotonic() - started
        print(
            f"   re-render: {rerender_seconds:.0f}s "
            f"(first render {first_seconds:.0f}s)"
        )
        page.wait_for_timeout(500)

        # The correction must reach the exported subtitles...
        job_id = json.loads(
            (root / "out" / "web" / "jobs.json").read_text(encoding="utf-8")
        )["jobs"][0]["id"]
        srt = page.request.get(f"{server.base}api/jobs/{job_id}/artifacts/srt").text()
        # Subtitles wrap lines, so compare with whitespace collapsed.
        needle = "people's goals" if "people's goals" in corrected else "(corrected)"
        if needle not in " ".join(srt.split()):
            failures.append(f"{needle!r} is not in the exported subtitles")
        else:
            print(f"   {needle!r} is in the exported subtitles")

        # ...and the burned-in captions, which only a frame can show.
        video = root / "edited.mp4"
        video.write_bytes(
            page.request.get(f"{server.base}api/jobs/{job_id}/artifacts/video").body()
        )
        fps = plan_on_disk(root)["fps"]
        middle = (edited_scene["start_frame"] + edited_scene["end_frame"]) / 2 / fps
        frame = SHOTS / "phase-8-16-corrected-frame.png"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{middle:.2f}", "-i", str(video),
             "-frames:v", "1", "-y", str(frame)],
            check=True,
        )
        print(f"   frame at {middle:.1f}s saved for inspection")

        reuse = server.segment_reuse()
        print(f"   segments reused, per render: {reuse}")
        if len(reuse) >= 2:
            reused, total = (int(n) for n in reuse[-1].split("/"))
            if total - reused > 3:
                failures.append(
                    f"a one-scene edit re-rendered {total - reused} of {total} "
                    f"segments"
                )

        page.wait_for_timeout(1000)
        page.click("text=See how it was made")
        page.wait_for_selector("text=The scene plan", timeout=30_000)
        page.wait_for_timeout(2500)
        if page.locator(".pending-bar").count():
            failures.append("the pending bar is still shown after re-rendering")
        if not page.get_by_text("your choice").count():
            failures.append("the edited scene is not marked as the person's choice")
        page.screenshot(path=str(SHOTS / "phase-8-12-after-update.png"), full_page=True)
        print("   after-update filmstrip captured")

        if errors:
            failures.append(f"browser errors: {errors[:3]}")
    finally:
        browser.close()
        server.kill()


def scenario_interrupted(playwright, audio: Path, failures: list[str]) -> None:
    print("== interrupted ==")
    root = Path(tempfile.mkdtemp(prefix="vf-kill-"))
    env = environment(root, None)
    server = Server(env, root / "server-1.log")
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        through_to_settings(page, server.url, audio, "Interrupted")
        page.click("text=Make the video")
        page.wait_for_selector("text=Making your video", timeout=30_000)
        # Kill it once the render is under way, not before it starts.
        page.wait_for_timeout(4000)
        jobs = json.loads((root / "out" / "web" / "jobs.json").read_text(encoding="utf-8"))
        state_before = jobs["jobs"][0]["state"]
        print(f"   state on disk before the kill: {state_before}")
        if state_before != "running":
            failures.append(f"the job was not running when killed ({state_before})")

        server.kill()
        print("   server killed mid-render")
    finally:
        browser.close()

    server = Server(env, root / "server-2.log")
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        page.goto(server.url, wait_until="networkidle")
        notice = page.get_by_text("A render was interrupted.")
        notice.wait_for(timeout=15_000)
        page.screenshot(path=str(SHOTS / "phase-8-13-interrupted.png"), full_page=True)
        print("   the restarted server offers to resume")

        page.click("text=Resume it")
        if wait_for_result(page, failures):
            page.wait_for_timeout(800)
            page.screenshot(path=str(SHOTS / "phase-8-14-resumed.png"), full_page=True)
            print("   resumed and finished")
    finally:
        browser.close()
        server.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--only", choices=("editing", "interrupted"), default=None)
    arguments = parser.parse_args()

    from playwright.sync_api import sync_playwright

    failures: list[str] = []
    with sync_playwright() as playwright:
        if arguments.only in (None, "editing"):
            scenario_editing(playwright, arguments.audio, arguments.library, failures)
        if arguments.only in (None, "interrupted"):
            scenario_interrupted(playwright, arguments.audio, failures)

    print()
    if failures:
        print("FAILURES:")
        for failure in failures:
            print("  -", failure)
        return 1
    print("editing and recovery verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
