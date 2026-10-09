"""The Sound card in a real browser (D-171).

Makes a video from the public-domain sonnet with a generated music track, the
way a person would, then uses the Sound card: moving a slider plays a preview
and warns when the music gets close to the voice, and applying a new
destination updates the video with its sound re-mixed to that loudness.

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
pytest.importorskip("soundfile")

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

    work = tmp_path_factory.mktemp("sound-panel")
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
            context = browser.new_context()
            page = context.new_page()
            page._voxframe_errors = []
            page.on("pageerror", lambda exc: page._voxframe_errors.append(str(exc)))
            yield page
            context.close()
            browser.close()
    except Exception as exc:
        pytest.skip(f"playwright unavailable: {str(exc)[:80]}")


@pytest.fixture(scope="module")
def made(server, page):  # type: ignore[no-untyped-def]
    """A video with music, made through the app."""
    from tests.integration.test_music_directed_render import generated_track

    handle, work = server
    track = work / "track.wav"
    generated_track(track, bars=24)

    page.goto(handle.url, wait_until="networkidle")
    to_upload_screen(page)
    page.set_input_files("input[type=file]", str(SONNET))
    page.wait_for_selector("text=How should it look?", timeout=60_000)
    page.click("text=Draft")
    page.get_by_text("Use my own track").click()
    page.set_input_files("#music-file", str(track))
    page.wait_for_selector(f"text={track.name}", timeout=30_000)
    page.click("text=Make the video")
    page.get_by_text("Your video is ready").or_(page.locator(".notice-error")).first.wait_for(
        timeout=900_000
    )
    if page.locator(".notice-error").count():
        pytest.fail(f"render failed: {page.locator('.notice-error').first.inner_text()[:300]}")
    # The Sound card lives in the studio's Sound tab (D-180).
    page.get_by_role("tab", name="Sound").click()
    return page


def test_the_sound_card_shows_the_checks(made) -> None:  # type: ignore[no-untyped-def]
    card = made.locator(".sound-panel")

    card.wait_for(timeout=15_000)
    assert card.get_by_text("Sound checks passed").count() == 1
    assert "aiming for -14" in card.inner_text()


def test_a_close_setting_warns_and_plays_a_preview(made) -> None:  # type: ignore[no-untyped-def]
    card = made.locator(".sound-panel")

    card.locator("#music-under-voice").fill("8")

    card.get_by_text(re.compile(r"only 8 dB under the voice")).wait_for(timeout=5_000)
    card.locator("audio").wait_for(timeout=10_000)
    assert card.locator("audio").get_attribute("src", timeout=5_000)


def test_polished_and_original_are_compared_in_the_preview(made) -> None:  # type: ignore[no-untyped-def]
    card = made.locator(".sound-panel")
    polished = card.get_by_role("button", name="Polished", exact=True)
    original = card.get_by_role("button", name="Original", exact=True)
    assert polished.get_attribute("aria-pressed") == "true"
    assert card.get_by_text("already has music").count() == 0  # the sonnet has none
    before = card.locator("audio").get_attribute("src")

    with made.expect_request(
        lambda request: request.url.endswith("/mix/preview")
        and '"voice_polish":false' in (request.post_data or "")
    ):
        original.click()

    card.get_by_text("Exactly as recorded, nothing changed.").wait_for(timeout=5_000)
    assert original.get_attribute("aria-pressed") == "true"
    made.wait_for_function(
        "before => document.querySelector('.sound-panel audio')?.src !== before", arg=before
    )
    polished.click()
    card.get_by_text("Gentle noise reduction").wait_for(timeout=5_000)


def test_applying_a_destination_remixes_to_its_loudness(made) -> None:  # type: ignore[no-untyped-def]
    card = made.locator(".sound-panel")
    card.locator("#music-under-voice").fill("15")
    card.locator("#destination").select_option("podcast")

    card.get_by_role("button", name="Apply to the video").click()
    made.wait_for_selector("text=Updating your video", timeout=30_000)
    made.get_by_text("Your video is ready").or_(made.locator(".notice-error")).first.wait_for(
        timeout=600_000
    )

    assert made.locator(".notice-error").count() == 0, made.locator(".notice-error").all_inner_texts()
    card = made.locator(".sound-panel")
    card.get_by_text("Sound checks passed").wait_for(timeout=15_000)
    assert "aiming for -16" in card.inner_text()
    assert made._voxframe_errors == []


def test_a_track_of_your_own_is_added_heard_switched_and_removed(made, server) -> None:  # type: ignore[no-untyped-def]
    """D-184: any time after the video is made, re-rendering only the sound."""
    from tests.integration.test_music_directed_render import generated_track

    _, work = server
    second = work / "second song.wav"
    generated_track(second, bars=16)
    card = made.locator(".sound-panel")

    with made.expect_request(
        lambda request: request.url.endswith("/mix/preview")
        and '"music_upload_id":"' in (request.post_data or "")
    ):
        card.get_by_label("Choose a music track", exact=True).set_input_files(str(second))
    chip = card.get_by_role("button", name="Your track: second song.wav")
    assert chip.get_attribute("aria-pressed") == "true"
    card.locator("#track-credit").fill("Second song by the test")

    with made.expect_request(
        lambda request: request.url.endswith("/mix/preview")
        and '"voice_only":true' in (request.post_data or "")
    ):
        card.get_by_role("button", name="No music", exact=True).click()
    chip.click()
    assert card.locator("#track-credit").input_value() == "Second song by the test"

    card.get_by_role("button", name="Apply to the video").click()
    made.wait_for_selector("text=Updating your video", timeout=30_000)
    made.get_by_text("Your video is ready").or_(made.locator(".notice-error")).first.wait_for(
        timeout=600_000
    )
    assert made.locator(".notice-error").count() == 0, made.locator(".notice-error").all_inner_texts()
    card = made.locator(".sound-panel")
    chip = card.get_by_role("button", name="Your track: second song.wav")
    chip.wait_for(timeout=15_000)
    assert chip.get_attribute("aria-pressed") == "true"
    assert card.locator("#track-credit").input_value() == "Second song by the test"

    card.get_by_role("button", name="Remove this track").click()
    assert card.get_by_role("button", name="Your track: second song.wav").count() == 0
    assert card.get_by_role("button", name="No music", exact=True).get_attribute("aria-pressed") == "true"
    assert card.get_by_role("button", name="Add your own track…").count() == 1
    assert made._voxframe_errors == []
