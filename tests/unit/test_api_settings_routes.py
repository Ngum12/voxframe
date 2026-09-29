"""The settings, consent and key-check routes (D-116).

A browser user has no `.env`, so the app has to take keys from a form. The rule
that matters: a key goes to the user's own config directory, is never sent back
in full, and never reaches a log, a plan or a response body.
"""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient  # noqa: E402

from voxframe.api.app import ApiContext, create_app  # noqa: E402
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
    test_client = TestClient(create_app(context), base_url=LOOPBACK)
    test_client.headers.update({"x-voxframe-token": context.token.value})
    return test_client


class TestConsent:
    def test_a_fresh_install_has_not_been_asked(self, client: TestClient) -> None:
        """The consent screen appears exactly once, so this state must exist."""
        body = client.get("/api/settings").json()

        assert body["sourcing"]["has_been_asked"] is False
        assert body["sourcing"]["consent"] is None

    def test_declining_is_remembered(self, client: TestClient) -> None:
        """Declining must not make the screen reappear every launch."""
        client.put("/api/settings", json={"sourcing_consent": False})

        body = client.get("/api/settings").json()

        assert body["sourcing"]["has_been_asked"] is True
        assert body["sourcing"]["consent"] is False

    def test_accepting_is_remembered(self, client: TestClient) -> None:
        client.put("/api/settings", json={"sourcing_consent": True})

        assert client.get("/api/settings").json()["sourcing"]["consent"] is True

    def test_consent_alone_does_not_enable_sourcing(
        self, client: TestClient
    ) -> None:
        """Openverse on its own was measured as insufficient (D-077)."""
        client.put("/api/settings", json={"sourcing_consent": True})

        assert client.get("/api/settings").json()["sourcing"]["enabled"] is False

    def test_consent_with_a_key_enables_sourcing(self, client: TestClient) -> None:
        client.put(
            "/api/settings",
            json={"sourcing_consent": True, "api_keys": {"pexels": "abc123"}},
        )

        assert client.get("/api/settings").json()["sourcing"]["enabled"] is True


class TestKeys:
    def test_a_stored_key_comes_back_masked(self, client: TestClient) -> None:
        secret = "pexels-secret-value-987654"
        client.put("/api/settings", json={"api_keys": {"pexels": secret}})

        response = client.get("/api/settings")

        assert secret not in response.text
        assert response.json()["api_keys"]["pexels"].endswith("7654")

    def test_a_key_is_never_echoed_by_the_write(self, client: TestClient) -> None:
        secret = "pexels-secret-value-987654"

        response = client.put("/api/settings", json={"api_keys": {"pexels": secret}})

        assert secret not in response.text

    def test_an_empty_value_clears_a_key(self, client: TestClient) -> None:
        client.put("/api/settings", json={"api_keys": {"pexels": "abc"}})
        client.put("/api/settings", json={"api_keys": {"pexels": ""}})

        assert client.get("/api/settings").json()["api_keys"] == {}

    def test_omitting_a_key_leaves_it_alone(self, client: TestClient) -> None:
        """Changing consent must not require sending the key back up."""
        client.put("/api/settings", json={"api_keys": {"pexels": "abc"}})
        client.put("/api/settings", json={"sourcing_consent": True})

        assert "pexels" in client.get("/api/settings").json()["api_keys"]

    def test_an_unknown_adapter_is_refused(self, client: TestClient) -> None:
        """A crafted request must not write arbitrary configuration."""
        response = client.put("/api/settings", json={"api_keys": {"evil": "x"}})

        assert response.status_code == 422

    def test_keys_are_written_outside_the_repository(
        self, client: TestClient
    ) -> None:
        """The one rule that cannot bend: no key in a committable file."""
        from voxframe.config.userprefs import config_path

        client.put("/api/settings", json={"api_keys": {"pexels": "abc"}})

        repository = Path(__file__).resolve().parents[2]
        assert repository not in config_path().resolve().parents

    def test_an_environment_key_is_reported_as_such(self, tmp_path: Path) -> None:
        """The UI disables the field, so it has to know."""
        root = tmp_path / "web"
        context = ApiContext(
            settings=Settings(
                library_path=tmp_path / "lib",
                cache_path=tmp_path / "cache",
                output_path=tmp_path / "out",
                pexels_api_key="from-environment",
            ),
            store=JobStore(root),
            token=SessionToken("t"),
            allowed_paths=(root.resolve(),),
        )
        client = TestClient(create_app(context), base_url=LOOPBACK)
        client.headers.update({"x-voxframe-token": "t"})

        body = client.get("/api/settings").json()

        assert "pexels" in body["environment_keys"]
        assert "from-environment" not in str(body)


class TestKeyCheck:
    def test_an_unknown_adapter_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/settings/check-key", json={"adapter": "evil", "key": "x"}
        )

        assert response.status_code == 422

    def test_no_key_at_all_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/settings/check-key", json={"adapter": "pexels", "key": ""}
        )

        assert response.status_code == 422

    def test_a_bad_key_reports_invalid_without_echoing_it(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An upstream error can quote the query string it was given."""
        secret = "obviously-wrong-key-123456"  # made up, not a key; gitleaks:allow

        def explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError(f"401 for key={secret}")

        monkeypatch.setattr(
            "voxframe.sourcing.pexels.PexelsAdapter.search", explode
        )

        response = client.post(
            "/api/settings/check-key", json={"adapter": "pexels", "key": secret}
        )

        assert response.status_code == 200
        assert response.json()["valid"] is False
        assert secret not in response.text

    def test_a_working_key_reports_valid(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "voxframe.sourcing.pexels.PexelsAdapter.search", lambda *a, **k: ()
        )

        response = client.post(
            "/api/settings/check-key", json={"adapter": "pexels", "key": "good-key"}
        )

        assert response.json()["valid"] is True


class TestFrontend:
    def test_the_built_app_is_served_at_the_root(self, client: TestClient) -> None:
        """Serving the shell must not require a token: no JS has run yet."""
        from voxframe.api.app import static_root

        if not (static_root() / "index.html").is_file():
            pytest.skip("frontend not built")

        response = client.get("/")

        assert response.status_code == 200
        assert "<!doctype html>" in response.text.lower()

    def test_an_unknown_path_returns_the_app_shell(
        self, client: TestClient
    ) -> None:
        """A client-side route must survive a reload."""
        from voxframe.api.app import static_root

        if not (static_root() / "index.html").is_file():
            pytest.skip("frontend not built")

        assert client.get("/some/deep/route").status_code == 200

    def test_the_catch_all_does_not_serve_arbitrary_files(
        self, client: TestClient
    ) -> None:
        """A path from a client must never select a file."""
        from voxframe.api.app import static_root

        if not (static_root() / "index.html").is_file():
            pytest.skip("frontend not built")

        response = client.get("/../../../DECISIONS.md")

        assert "D-001" not in response.text
