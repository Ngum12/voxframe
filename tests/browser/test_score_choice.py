"""Choosing "Let Voxframe score it" in a real browser (D-176).

The settings screen offers three music choices. Choosing a generated score
and a style makes a video with music composed for it: the Sound card treats
it as the video's music, and its checks pass.

Skipped cleanly without Playwright, its browser, the built web app, or the
score's samples (the setting VOXFRAME_SCORE_SAMPLES_PATH, or the pack).
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
pytest.importorskip("fastapi", reason="web extra not installed")
pytest.importorskip("scipy", reason="music tools not installed")

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

    work = tmp_path_factory.mktemp("score-choice")
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
    """A video with a calm score, made through the settings screen."""
    from voxframe.music.score import samples_dir
    from voxframe.music.score.instruments import SampleLibrary, SamplesMissing

    try:
        SampleLibrary(samples_dir()).check()
    except SamplesMissing as exc:
        pytest.skip(f"the score's samples are not installed: {exc}")
    handle, _ = server
    page.goto(handle.url, wait_until="networkidle")
    to_upload_screen(page)
    page.set_input_files("input[type=file]", str(SONNET))
    page.wait_for_selector("text=How should it look?", timeout=60_000)
    page.click("text=Draft")
    assert page.get_by_role("radio", name="No music").is_checked()  # the default (D-172)
    page.get_by_text("Let Voxframe score it").click()
    page.select_option("#score-style", "calm")
    page.get_by_text("Each video gets its own variation").wait_for(timeout=5_000)
    page.click("text=Make the video")
    page.get_by_text("Your video is ready").or_(page.locator(".notice-error")).first.wait_for(
        timeout=900_000
    )
    if page.locator(".notice-error").count():
        pytest.fail(f"render failed: {page.locator('.notice-error').first.inner_text()[:300]}")
    # The Sound card lives in the studio's Sound tab (D-180).
    page.get_by_role("tab", name="Sound").click()
    return page


def test_the_score_is_the_videos_music(made) -> None:  # type: ignore[no-untyped-def]
    card = made.locator(".sound-panel")
    card.wait_for(timeout=15_000)

    assert card.locator("#music-level").is_enabled()
    assert card.get_by_text("Sound checks passed").count() == 1
    assert card.get_by_role("button", name="Let Voxframe score it").get_attribute("aria-pressed") == "true"
    assert card.locator("#score-style-edit").input_value() == "calm"
    assert made._voxframe_errors == []


# --- Stage 2 (D-179): changing the score after the video is made ---------------------


def _music(page) -> dict:  # type: ignore[no-untyped-def,type-arg]
    """The video's music as the server has it."""
    return page.evaluate(
        """async () => {
            const jobs = await (await fetch('/api/jobs', {credentials: 'same-origin'})).json();
            const id = jobs.jobs[0].id;
            return (await (await fetch(`/api/jobs/${id}/mix`, {credentials: 'same-origin'})).json()).music;
        }"""
    )


def test_group_levels_are_heard_at_once(made) -> None:  # type: ignore[no-untyped-def]
    card = made.locator(".sound-panel")

    with made.expect_request(
        lambda request: request.url.endswith("/mix/preview")
        and '"percussion":-30' in (request.post_data or "")
    ):
        card.locator("#group-percussion").fill("-30")

    card.get_by_text("off").first.wait_for(timeout=5_000)
    card.get_by_role("button", name="Undo").click()  # back to as composed
    assert card.locator("#group-percussion").input_value() == "0"


def test_a_style_can_be_heard_before_choosing_it(made) -> None:  # type: ignore[no-untyped-def]
    card = made.locator(".sound-panel")
    card.locator("#score-style-edit").select_option("reflective")

    card.get_by_role("button", name="Hear this style").click()

    card.get_by_label("Style preview").wait_for(timeout=120_000)
    assert card.get_by_label("Style preview").get_attribute("src")
    card.get_by_text("composed when you apply it").wait_for(timeout=5_000)
    card.get_by_role("button", name="Undo").click()
    assert card.locator("#score-style-edit").input_value() == "calm"


def test_a_new_variation_is_applied_to_the_sound(made) -> None:  # type: ignore[no-untyped-def]
    before = _music(made)
    card = made.locator(".sound-panel")

    card.get_by_role("button", name="New variation").click()
    card.get_by_text("composed when you apply it").wait_for(timeout=5_000)
    card.get_by_role("button", name="Apply to the video").click()
    made.wait_for_selector("text=Updating your video", timeout=30_000)
    made.get_by_text("Your video is ready").or_(made.locator(".notice-error")).first.wait_for(
        timeout=900_000
    )

    assert made.locator(".notice-error").count() == 0
    after = _music(made)
    assert after["choice"] == "score" and after["style"] == "calm"
    assert after["seed"] != before["seed"]
    made.locator(".sound-panel").get_by_text("Sound checks passed").wait_for(timeout=15_000)
    assert made._voxframe_errors == []
