"""Browser-facing security: cookies, headers, CORS (D-117).

Step 1 secured the API against other *processes*. These tests cover the
additional surface a browser brings: a credential that lives in a page, and
other pages that would like to use it.

Each test names the attack it forecloses. A defence whose purpose is not
written down gets removed by someone who cannot see what it was for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient  # noqa: E402

from voxframe.api.app import (  # noqa: E402
    SECURITY_HEADERS,
    SESSION_COOKIE,
    ApiContext,
    create_app,
)
from voxframe.api.security import SessionToken  # noqa: E402
from voxframe.config.settings import Settings  # noqa: E402
from voxframe.jobs.store import JobStore  # noqa: E402

LOOPBACK = "http://127.0.0.1:8765"


@pytest.fixture
def context(tmp_path: Path) -> ApiContext:
    root = tmp_path / "web"
    library = tmp_path / "library"
    library.mkdir()
    return ApiContext(
        settings=Settings(
            library_path=library,
            cache_path=tmp_path / "cache",
            output_path=tmp_path / "out",
        ),
        store=JobStore(root),
        token=SessionToken("token-under-test"),
        allowed_paths=(root.resolve(), library.resolve()),
    )


@pytest.fixture
def client(context: ApiContext) -> TestClient:
    """A client with no credential, addressing the app as a browser would."""
    return TestClient(create_app(context), base_url=LOOPBACK)


class TestSessionExchange:
    """The launch token must not stay in the URL (owner's decision 2)."""

    def test_a_valid_token_sets_a_cookie(
        self, client: TestClient, context: ApiContext
    ) -> None:
        response = client.post(
            "/api/session", headers={"x-voxframe-token": context.token.value}
        )

        assert response.status_code == 200
        assert response.cookies.get(SESSION_COOKIE)

    def test_the_token_works_from_the_query_string(
        self, client: TestClient, context: ApiContext
    ) -> None:
        """The first navigation carries it there; no JS has run yet."""
        response = client.post(f"/api/session?token={context.token.value}")

        assert response.status_code == 200

    def test_an_invalid_token_is_refused(self, client: TestClient) -> None:
        response = client.post("/api/session", headers={"x-voxframe-token": "no"})

        assert response.status_code == 401
        assert not response.cookies.get(SESSION_COOKIE)

    def test_the_cookie_is_httponly(
        self, client: TestClient, context: ApiContext
    ) -> None:
        """A script — including an injected one — must not be able to read it."""
        response = client.post(
            "/api/session", headers={"x-voxframe-token": context.token.value}
        )

        assert "httponly" in response.headers["set-cookie"].lower()

    def test_the_cookie_is_samesite_strict(
        self, client: TestClient, context: ApiContext
    ) -> None:
        """A request from any other site must carry no credential at all."""
        response = client.post(
            "/api/session", headers={"x-voxframe-token": context.token.value}
        )

        assert "samesite=strict" in response.headers["set-cookie"].lower()

    def test_the_cookie_then_authenticates(
        self, client: TestClient, context: ApiContext
    ) -> None:
        client.post("/api/session", headers={"x-voxframe-token": context.token.value})

        # No token header on this one: the cookie alone must carry it.
        assert client.get("/api/capabilities").status_code == 200

    def test_a_forged_cookie_does_not_authenticate(self, client: TestClient) -> None:
        client.cookies.set(SESSION_COOKIE, "not-the-token")

        assert client.get("/api/capabilities").status_code == 401


class TestSecurityHeaders:
    def test_every_header_is_present(self, client: TestClient) -> None:
        response = client.get("/api/health")

        for header in SECURITY_HEADERS:
            assert header in response.headers, header

    def test_the_referrer_policy_is_no_referrer(self, client: TestClient) -> None:
        """The first URL carries the token; it must not leak anywhere."""
        assert client.get("/api/health").headers["Referrer-Policy"] == "no-referrer"

    def test_the_csp_confines_scripts_to_this_origin(
        self, client: TestClient
    ) -> None:
        policy = client.get("/api/health").headers["Content-Security-Policy"]

        scripts = policy.split("script-src")[1].split(";")[0]
        assert scripts.split() == ["'self'", "'wasm-unsafe-eval'"]
        assert "'unsafe-inline'" not in scripts and "'unsafe-eval'" not in scripts

    def test_the_csp_forbids_framing(self, client: TestClient) -> None:
        """No frame means a clickjacking overlay has nothing to cover."""
        policy = client.get("/api/health").headers["Content-Security-Policy"]

        assert "frame-ancestors 'none'" in policy

    def test_nosniff_is_set(self, client: TestClient) -> None:
        """An uploaded file must never be guessed into a script."""
        response = client.get("/api/health")

        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_headers_are_on_failures_too(self, client: TestClient) -> None:
        """A 401 is still a response a browser will act on."""
        response = client.get("/api/jobs")

        assert response.status_code == 401
        assert response.headers["Referrer-Policy"] == "no-referrer"

    def test_headers_are_on_a_rejected_host(self, client: TestClient) -> None:
        response = client.get("/api/health", headers={"host": "evil.test"})

        assert response.status_code == 400
        assert "Content-Security-Policy" in response.headers


class TestCors:
    """No CORS, deliberately: adding it could only loosen the default."""

    def test_no_allow_origin_header_is_sent(
        self, client: TestClient, context: ApiContext
    ) -> None:
        client.post("/api/session", headers={"x-voxframe-token": context.token.value})

        response = client.get("/api/capabilities")

        assert "access-control-allow-origin" not in {
            key.lower() for key in response.headers
        }

    def test_a_foreign_origin_is_refused(
        self, client: TestClient, context: ApiContext
    ) -> None:
        """Even holding a valid credential: another page must not use it."""
        client.post("/api/session", headers={"x-voxframe-token": context.token.value})

        response = client.get(
            "/api/capabilities", headers={"origin": "https://evil.example.com"}
        )

        assert response.status_code == 403

    def test_our_own_origin_passes(
        self, client: TestClient, context: ApiContext
    ) -> None:
        client.post("/api/session", headers={"x-voxframe-token": context.token.value})

        response = client.get(
            "/api/capabilities", headers={"origin": "http://127.0.0.1:8765"}
        )

        assert response.status_code == 200

    def test_a_request_without_an_origin_passes(
        self, client: TestClient, context: ApiContext
    ) -> None:
        """A same-origin fetch often omits it entirely, as does curl."""
        client.post("/api/session", headers={"x-voxframe-token": context.token.value})

        assert client.get("/api/capabilities").status_code == 200
