"""Light and dark in a real browser (D-185).

Follows the computer by default, keeps a chosen look across a reload with no
flash of the other one, and switches at once from Settings. Needs no render.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
pytest.importorskip("fastapi", reason="web extra not installed")

from tests.browser.first_run import to_upload_screen  # noqa: E402
from voxframe.api.app import static_root  # noqa: E402

PAPER = "rgb(244, 241, 234)"  # --canvas, light
EMBER = "rgb(14, 15, 17)"  # --canvas, dark


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator:
    if not (static_root() / "index.html").is_file():
        pytest.skip("frontend not built (cd web && npm run build)")
    import os

    os.environ["VOXFRAME_CONFIG_DIR"] = str(tmp_path_factory.mktemp("theme-config"))
    from voxframe.api.serve import build_server, serve

    handle = build_server(port=_free_port())
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
    yield handle
    handle.store.shutdown()


@pytest.fixture(scope="module")
def browser() -> Iterator:
    try:
        with playwright_api.sync_playwright() as playwright:
            try:
                launched = playwright.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium unavailable: {str(exc)[:80]}")
            yield launched
            launched.close()
    except Exception as exc:
        pytest.skip(f"playwright unavailable: {str(exc)[:80]}")


def _open(browser, handle, scheme: str):  # type: ignore[no-untyped-def]
    context = browser.new_context(color_scheme=scheme)
    page = context.new_page()
    page._voxframe_errors = []
    page.on("pageerror", lambda exc: page._voxframe_errors.append(str(exc)))
    page.goto(handle.url, wait_until="networkidle")
    to_upload_screen(page)
    return page


def _background(page) -> str:  # type: ignore[no-untyped-def]
    return str(page.evaluate("getComputedStyle(document.body).backgroundColor"))


def test_it_follows_the_computer_by_default(browser, server) -> None:  # type: ignore[no-untyped-def]
    light = _open(browser, server, "light")
    dark = _open(browser, server, "dark")

    assert _background(light) == PAPER
    assert _background(dark) == EMBER
    light.emulate_media(color_scheme="dark")  # the computer changes: so does the app
    assert _background(light) == EMBER


def test_a_chosen_look_is_kept_and_never_flashes(browser, server) -> None:  # type: ignore[no-untyped-def]
    page = _open(browser, server, "dark")
    page.get_by_role("button", name="Settings", exact=True).click()
    group = page.get_by_role("radiogroup", name="Theme")

    group.get_by_role("radio", name="Light").click()
    assert _background(page) == PAPER  # at once

    page.wait_for_function(
        "() => fetch('/api/settings', {credentials: 'same-origin'}).then(r => r.json()).then(s => s.theme === 'light')"
    )
    # The page as served is already light: before any script has run.
    first_paint = page.context.new_page()
    first_paint.route("**/assets/*.js", lambda route: route.abort())
    first_paint.goto(server.url.split("?")[0])
    assert first_paint.evaluate("document.documentElement.dataset.theme") == "light"
    assert _background(first_paint) == PAPER

    page.reload(wait_until="networkidle")
    to_upload_screen(page)
    page.get_by_role("button", name="Settings", exact=True).click()
    group = page.get_by_role("radiogroup", name="Theme")
    assert group.get_by_role("radio", name="Light").get_attribute("aria-checked") == "true"
    group.get_by_role("radio", name="Dark").click()
    assert _background(page) == EMBER
    group.get_by_role("radio", name="Follow the system").click()
    assert _background(page) == EMBER  # this browser is dark
    assert page._voxframe_errors == []
