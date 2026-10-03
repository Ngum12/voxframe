"""The studio in a real browser (D-180, D-182).

Makes a video from the public-domain sonnet, then edits it the way a person
would: the player stays in view while every tab is used; the keyboard
shortcuts work; clicking a word or a scene moves the video there; an edit is
counted and previewed, undone and redone; and "Update video" makes it again
without leaving the studio.

Skipped cleanly when Playwright, its browser, or the built web app is absent.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
pytest.importorskip("fastapi", reason="web extra not installed")

from tests.browser.first_run import to_upload_screen  # noqa: E402
from voxframe.api.app import static_root  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SONNET = REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator:
    if not (static_root() / "index.html").is_file():
        pytest.skip("frontend not built (cd web && npm run build)")
    import os

    from voxframe.api.serve import build_server, serve
    from voxframe.config.settings import get_settings

    work = tmp_path_factory.mktemp("studio")
    os.environ["VOXFRAME_CONFIG_DIR"] = str(work / "config")
    settings = get_settings().model_copy(
        update={
            "transcribe_model": "base",
            "cache_path": work / "cache",
            "output_path": work / "out",
            "library_path": work / "library",
        }
    )
    handle = build_server(port=_free_port(), settings=settings)
    thread = threading.Thread(target=serve, args=(handle,), daemon=True)
    thread.start()
    import http.client

    for _ in range(120):
        try:
            connection = http.client.HTTPConnection(handle.host, handle.port, timeout=2)
            connection.request("GET", "/api/health")
            ready = connection.getresponse().status == 200
            connection.close()
            if ready:
                break
        except OSError:
            time.sleep(0.25)
    else:
        pytest.skip("server did not start")
    yield handle, work
    handle.store.shutdown()


@pytest.fixture(scope="module")
def page(server) -> Iterator:  # type: ignore[no-untyped-def]
    try:
        with playwright_api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium unavailable: {str(exc)[:80]}")
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page._voxframe_errors = []
            page.on("pageerror", lambda exc: page._voxframe_errors.append(str(exc)))
            yield page
            context.close()
            browser.close()
    except Exception as exc:
        pytest.skip(f"playwright unavailable: {str(exc)[:80]}")


@pytest.fixture(scope="module")
def studio(server, page):  # type: ignore[no-untyped-def]
    """The sonnet, made into a video through the app, open in the studio."""
    handle, _ = server
    page.goto(handle.url, wait_until="networkidle")
    to_upload_screen(page)
    page.set_input_files("input[type=file]", str(SONNET))
    page.wait_for_selector("text=How should it look?", timeout=60_000)
    page.click("text=Draft")
    # A title card, so word times after it are checked on the video's clock.
    page.fill("#title", "A Calendar of Sonnets")
    page.click("text=Make the video")
    page.get_by_text("Your video is ready").or_(page.locator(".notice-error")).first.wait_for(
        timeout=900_000
    )
    if page.locator(".notice-error").count():
        pytest.fail(f"render failed: {page.locator('.notice-error').first.inner_text()[:300]}")
    page.locator(".studio video").wait_for(timeout=15_000)
    page.wait_for_function("document.querySelector('.studio video').readyState >= 1", timeout=30_000)
    return page


def _video_box(page) -> dict:  # type: ignore[no-untyped-def,type-arg]
    return page.locator(".studio video").bounding_box()


def _current_time(page) -> float:  # type: ignore[no-untyped-def]
    return float(page.evaluate("document.querySelector('.studio video').currentTime"))


def test_the_player_stays_in_view_while_editing(studio) -> None:  # type: ignore[no-untyped-def]
    before = _video_box(studio)
    viewport = studio.viewport_size

    for tab in ("Captions", "Sound", "Style", "Scenes"):
        studio.get_by_role("tab", name=tab).click()
        studio.locator(".studio-tabpanel").evaluate("panel => panel.scrollTo(0, panel.scrollHeight)")
        box = _video_box(studio)
        assert box == before, tab  # it never moves
        assert box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"], tab
    assert studio.evaluate("document.scrollingElement.scrollTop") == 0  # the page itself never scrolls


def test_the_shortcuts(studio) -> None:  # type: ignore[no-untyped-def]
    studio.locator(".studio-time").click()  # focus on the page, not a control
    scene_before = studio.locator(".studio-scene-name").inner_text()

    studio.keyboard.press("ArrowRight")
    assert studio.locator(".studio-scene-name").inner_text() != scene_before
    start = _current_time(studio)
    studio.keyboard.press(".")
    assert _current_time(studio) == pytest.approx(start + 1, abs=0.1)
    studio.keyboard.press(",")
    assert _current_time(studio) == pytest.approx(start, abs=0.1)

    studio.keyboard.press(" ")
    studio.wait_for_function("!document.querySelector('.studio video').paused", timeout=5_000)
    studio.keyboard.press(" ")
    studio.wait_for_function("document.querySelector('.studio video').paused", timeout=5_000)

    studio.keyboard.press("3")
    studio.wait_for_function(
        "document.getElementById('tab-sound')?.getAttribute('aria-selected') === 'true'",
        timeout=5_000,
    )
    assert studio.get_by_role("tab", name="Sound").get_attribute("aria-selected") == "true"
    studio.keyboard.press("1")
    studio.keyboard.press("?")
    assert studio.locator(".studio-dialog").is_visible()
    studio.keyboard.press("Escape")
    assert not studio.locator(".studio-dialog").is_visible()


def test_a_word_or_a_scene_moves_the_video_there(studio) -> None:  # type: ignore[no-untyped-def]
    word = studio.locator(".timeline-word").nth(20)
    label = word.get_attribute("aria-label")
    word.click()

    minutes, seconds = re.search(r"at (\d+):(\d+)$", label or "").groups()  # type: ignore[union-attr]
    assert _current_time(studio) == pytest.approx(int(minutes) * 60 + int(seconds), abs=1.0)
    exact = float(studio.evaluate(
        "() => { const p = document.querySelector('.timeline-playhead'); return parseFloat(p.style.left); }"
    ))
    assert exact > 0

    clip = studio.locator(".timeline-clip").nth(2)
    clip.click()
    assert clip.get_attribute("aria-current") == "true"
    assert studio.get_by_role("tab", name="Scenes").get_attribute("aria-selected") == "true"


def test_words_are_where_the_captions_put_them(studio) -> None:  # type: ignore[no-untyped-def]
    """D-189: after a title card, a word is at its time in the plan, not later."""
    job = re.search(r"/api/jobs/([0-9a-f]+)/", studio.locator(".studio video").get_attribute("src") or "")
    assert job
    plan = studio.evaluate(
        "id => fetch(`/api/jobs/${id}/plan`, {credentials: 'same-origin'}).then(r => r.json())",
        job.group(1),
    )
    assert any(scene["card_kind"] == "title" for scene in plan["scenes"])
    spoken = next(scene for scene in plan["scenes"] if not scene["card_kind"])
    first = spoken["words"][0]

    studio.locator(".timeline-clip").first.click()  # back to the start: words are drawn in view
    studio.locator(".timeline-word").first.click()

    assert _current_time(studio) == pytest.approx(first["start"], abs=0.1)


def test_an_edit_is_counted_previewed_undone_and_redone(studio) -> None:  # type: ignore[no-untyped-def]
    status = studio.locator(".studio-status")
    studio.locator(".timeline-clip").nth(1).click()
    studio.get_by_role("tab", name="Captions").click()
    studio.get_by_role("button", name="Edit caption").click()
    field = studio.locator(".studio-tabpanel textarea")
    original = field.input_value()
    field.fill(original + " (corrected)")
    studio.get_by_role("button", name="Save caption").click()

    status.get_by_text("1 change not yet in the video").wait_for(timeout=10_000)
    assert studio.get_by_text("Preview · not yet in the video").count() == 1
    assert studio.locator(".timeline-changed").count() == 1

    studio.locator(".studio-time").click()
    studio.keyboard.press("Control+z")
    status.get_by_text("Your video is ready").wait_for(timeout=10_000)
    assert "(corrected)" not in studio.locator(".studio-tabpanel").inner_text()

    studio.keyboard.press("Control+Shift+z")
    status.get_by_text("1 change not yet in the video").wait_for(timeout=10_000)
    assert "(corrected)" in studio.locator(".studio-tabpanel").inner_text()


def test_updating_stays_in_the_studio(studio) -> None:  # type: ignore[no-untyped-def]
    studio.get_by_role("button", name="Update video").click()

    studio.locator(".studio-status").get_by_text("Updating your video").wait_for(timeout=30_000)
    assert studio.locator(".studio").count() == 1  # never sent to the progress screen
    studio.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=600_000)
    assert studio.get_by_text("Preview · not yet in the video").count() == 0
    assert studio.get_by_role("button", name="Update video").is_disabled()


def test_the_download_menu_lists_the_files(studio) -> None:  # type: ignore[no-untyped-def]
    studio.locator(".studio-menu summary").click()

    links = studio.locator(".studio-menu-list a")
    assert {links.nth(i).inner_text() for i in range(links.count())} >= {"Video (MP4)", "Captions (SRT)"}
    assert studio._voxframe_errors == []


def _panel_width(page) -> float:  # type: ignore[no-untyped-def]
    return float(page.locator(".studio-panel").bounding_box()["width"])


def test_the_panels_fold_away_and_resize(studio) -> None:  # type: ignore[no-untyped-def]
    """D-184: more room for the player, by button, shortcut or drag."""
    studio.locator(".studio-time").click()
    narrow = _video_box(studio)

    studio.keyboard.press("[")
    assert studio.locator(".studio-panel").count() == 0
    studio.keyboard.press("]")
    assert studio.locator(".studio-timeline").count() == 0
    roomy = _video_box(studio)
    assert roomy["width"] * roomy["height"] > narrow["width"] * narrow["height"]
    studio.get_by_role("button", name="Panel", exact=True).click()
    studio.get_by_role("button", name="Timeline", exact=True).click()
    assert studio.locator(".studio-panel").count() == studio.locator(".studio-timeline").count() == 1

    # Dragging the panel's edge, and the same handle from the keyboard.
    handle = studio.get_by_role("separator", name="Resize the side panel")
    box = handle.bounding_box()
    before = _panel_width(studio)
    studio.mouse.move(box["x"] + 3, box["y"] + 200)
    studio.mouse.down()
    studio.mouse.move(box["x"] - 117, box["y"] + 200, steps=6)
    studio.mouse.up()
    assert _panel_width(studio) == pytest.approx(before + 120, abs=3)
    handle.focus()
    studio.keyboard.press("ArrowRight")
    assert _panel_width(studio) == pytest.approx(before + 96, abs=3)

    lanes = studio.get_by_role("separator", name="Resize the timeline")
    lanes.focus()
    tall = studio.locator(".timeline-scenes").bounding_box()["height"]
    studio.keyboard.press("ArrowUp")
    assert studio.locator(".timeline-scenes").bounding_box()["height"] == pytest.approx(tall + 24, abs=2)
    assert studio.evaluate("document.scrollingElement.scrollTop") == 0  # still no page scroll


def test_the_layout_is_kept_and_recent_videos_open_the_studio(studio, server) -> None:  # type: ignore[no-untyped-def]
    handle, _ = server
    width = _panel_width(studio)
    studio.locator(".studio-time").click()
    studio.keyboard.press("]")  # the timeline folded away

    studio.goto(handle.url, wait_until="networkidle")
    to_upload_screen(studio)  # "Not now" asks again next time
    recent = studio.locator(".recent-video").first
    recent.wait_for(timeout=15_000)
    assert "en_sonnet_january_45s.wav" in recent.inner_text()  # the person's own name for it
    assert "scenes" in recent.inner_text()
    recent.click()

    studio.locator(".studio video").wait_for(timeout=15_000)
    assert _panel_width(studio) == pytest.approx(width, abs=2)
    assert studio.locator(".studio-timeline").count() == 0
    studio.get_by_role("button", name="Timeline", exact=True).click()
    assert studio.locator(".studio-timeline").count() == 1
    assert studio._voxframe_errors == []
