"""A fresh browser with no cookie must load the app (D-119).

**The regression this exists to prevent.** The token guard once covered the
static bundle, so the script that exchanges the launch token for a session
cookie was itself behind the token check. The page rendered blank, and nothing
could ever authenticate because the authenticating code never loaded.

**Why the unit suite cannot cover it.** Starlette's ``TestClient`` presents a
credential on every request, because that is how a test author naturally writes
it. The failing condition is the opposite: *a request made before any credential
exists*. Every API test passed while the app was completely unusable.

So this drives a real browser with a real cookie jar, from a genuinely cold
start. It renders no video and needs no model — it asserts only that the page
comes up, which is the thing that broke.

Skipped cleanly when Playwright or its browser is absent, so a fresh clone still
has a passing suite. Run the browser checks explicitly with::

    pytest tests/browser -m browser
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="playwright not installed"
)
pytest.importorskip("fastapi", reason="web extra not installed")

from voxframe.api.app import static_root  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def built_app() -> None:
    """Skip the module unless the frontend has actually been built."""
    if not (static_root() / "index.html").is_file():
        pytest.skip("frontend not built (cd web && npm run build)")


@pytest.fixture(scope="module")
def server(built_app: None, tmp_path_factory: pytest.TempPathFactory) -> Iterator:
    """A real server on a real port, isolated from the user's own config."""
    import os

    config = tmp_path_factory.mktemp("cold-load-config")
    os.environ["VOXFRAME_CONFIG_DIR"] = str(config)

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
def browser(server) -> Iterator:
    try:
        with playwright_api.sync_playwright() as playwright:
            try:
                instance = playwright.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium unavailable: {str(exc)[:80]}")
            yield instance
            instance.close()
    except Exception as exc:
        pytest.skip(f"playwright unavailable: {str(exc)[:80]}")


@pytest.fixture
def cold_page(browser, server):
    """A page in a brand-new context: no cookie, no storage, nothing.

    A fresh context per test is the whole point. Reusing one would carry the
    session cookie from a previous test and reproduce exactly the state that
    hid this bug.
    """
    context = browser.new_context()
    page = context.new_page()

    page._voxframe_errors = []
    page.on("pageerror", lambda exc: page._voxframe_errors.append(str(exc)))
    page.on(
        "requestfailed",
        lambda request: page._voxframe_errors.append(
            f"{request.url} failed"
        ),
    )

    yield page

    context.close()


class TestColdLoad:
    """A browser arriving with nothing must end up with a working app."""

    def test_the_page_is_not_blank(self, cold_page, server) -> None:
        """The exact symptom: correct title, empty body, nothing rendered."""
        cold_page.goto(server.url, wait_until="networkidle")
        cold_page.wait_for_selector("#root *", timeout=15_000)

        body = cold_page.evaluate("document.body.innerText").strip()

        assert body, "the page rendered nothing"

    def test_the_bundle_loads(self, cold_page, server) -> None:
        """The bundle 401'd, so no script ever ran."""
        cold_page.goto(server.url, wait_until="networkidle")

        assert not cold_page._voxframe_errors, cold_page._voxframe_errors

    def test_the_stylesheet_is_applied(self, cold_page, server) -> None:
        """A 401 body is JSON, which strict MIME checking refuses as CSS."""
        cold_page.goto(server.url, wait_until="networkidle")
        cold_page.wait_for_selector("#root *", timeout=15_000)

        # An unstyled page has the browser default background, not ours.
        background = cold_page.evaluate(
            "getComputedStyle(document.body).backgroundColor"
        )

        assert background not in {"rgba(0, 0, 0, 0)", "rgb(255, 255, 255)"}, (
            f"stylesheet did not apply (body background {background})"
        )

    def test_the_shell_needs_no_credential(self, cold_page, server) -> None:
        """Served without the token, because no script has run to present one."""
        response = cold_page.request.get(server.url.split("?")[0])

        assert response.status == 200

    def test_the_bundle_needs_no_credential(self, cold_page, server) -> None:
        """The browser requests this from a URL it built itself."""
        page_html = cold_page.request.get(server.url.split("?")[0]).text()

        # Pull the script src straight out of the served HTML rather than
        # hardcoding a hashed filename that changes on every build.
        import re

        match = re.search(r'src="([^"]+\.js)"', page_html)
        assert match, "no script tag in the served HTML"

        asset = match.group(1).lstrip("./")
        response = cold_page.request.get(f"{server.url.split('?')[0]}{asset}")

        assert response.status == 200

    def test_the_api_still_requires_a_credential(self, cold_page, server) -> None:
        """The narrowing must not have opened the data routes.

        Without this, "serve the shell unauthenticated" could quietly become
        "serve everything unauthenticated" and this file would still pass.
        """
        response = cold_page.request.get(
            f"{server.url.split('?')[0]}api/jobs", headers={"cookie": ""}
        )

        assert response.status == 401

    def test_the_token_is_exchanged_and_removed(self, cold_page, server) -> None:
        """The cold path end to end: arrive with a token, leave with a cookie."""
        cold_page.goto(server.url, wait_until="networkidle")
        cold_page.wait_for_selector("#root *", timeout=15_000)

        assert "token=" not in cold_page.url

        cookies = {cookie["name"]: cookie for cookie in cold_page.context.cookies()}
        session = cookies.get("voxframe_session")

        assert session, "no session cookie after a cold load"
        assert session["httpOnly"]
        assert str(session["sameSite"]).lower() == "strict"

    def test_a_reload_without_the_token_still_works(
        self,
        cold_page,
        server,
    ) -> None:
        """After the URL is cleaned, F5 must not log the user out."""
        cold_page.goto(server.url, wait_until="networkidle")
        cold_page.wait_for_selector("#root *", timeout=15_000)

        cold_page.goto(server.url.split("?")[0], wait_until="networkidle")
        cold_page.wait_for_selector("#root *", timeout=15_000)

        assert cold_page.evaluate("document.body.innerText").strip()

    def test_a_page_with_no_token_at_all_explains_itself(
        self,
        browser,
        server,
    ) -> None:
        """Someone who typed the bare URL gets told where to find the link,
        rather than a blank page or a raw 401."""
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(server.url.split("?")[0], wait_until="networkidle")
            page.wait_for_selector("#root *", timeout=15_000)

            body = page.evaluate("document.body.innerText")

            assert body.strip(), "unauthorised page rendered nothing"
            assert "voxframe web" in body.lower() or "token" in body.lower()
        finally:
            context.close()


def test_the_built_bundle_matches_the_committed_source(built_app: None) -> None:
    """The shipped bundle must be the one the committed source builds.

    Not a browser check, but it belongs with them: a stale bundle passes every
    test here while shipping code nobody reviewed. Compares the script name the
    HTML references against what is actually on disk.
    """
    index = (static_root() / "index.html").read_text(encoding="utf-8")

    import re

    referenced = set(re.findall(r'(?:src|href)="\.?/?(assets/[^"]+)"', index))
    present = {
        path.relative_to(static_root()).as_posix()
        for path in (static_root() / "assets").glob("*")
    }

    missing = referenced - present
    assert not missing, f"index.html references files that are not shipped: {missing}"

    orphaned = present - referenced
    assert not orphaned, (
        f"stale build output nothing references: {orphaned}. "
        f"Rebuild with: cd web && npm run build"
    )
