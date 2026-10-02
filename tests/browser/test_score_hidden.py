"""Without its sounds, the generated score is not offered (D-187).

The installed 0.2.0 app has no score sample pack yet, so "Let Voxframe score
it" must not appear at all, rather than as a choice that cannot be made. The
server's answer is changed in the browser to say the sounds are missing.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
pytest.importorskip("fastapi", reason="web extra not installed")

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

    os.environ["VOXFRAME_CONFIG_DIR"] = str(tmp_path_factory.mktemp("score-hidden-config"))
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


def _without_score_sounds(route) -> None:  # type: ignore[no-untyped-def]
    response = route.fetch()
    body = response.json()
    body["score"] = {**body.get("score", {}), "ready": False, "reason": "not installed"}
    route.fulfill(response=response, body=json.dumps(body))


def test_the_score_is_not_offered_without_its_sounds(server, page) -> None:  # type: ignore[no-untyped-def]
    page.route("**/api/capabilities", _without_score_sounds)
    page.goto(server.url, wait_until="networkidle")
    if page.get_by_role("button", name="Not now").count():
        page.get_by_role("button", name="Not now").click()
    page.set_input_files("input[type=file]", str(SONNET))
    page.wait_for_selector("text=How should it look?", timeout=60_000)

    assert page.get_by_text("Use my own track").count() == 1
    assert page.get_by_text("Let Voxframe score it").count() == 0
    assert page._voxframe_errors == []
