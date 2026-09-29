"""The API's routes and its guards, exercised through a real client.

The unit tests in ``test_api_security`` pin each defence in isolation. These
check that the defences are actually *installed* — a correct Host check that no
request passes through protects nothing, and that mistake is invisible until
someone finds it.

No render runs here: the pipeline is exercised by the integration suite and by
the phase demos. These tests are about the HTTP surface.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient  # noqa: E402

from voxframe.api.app import ApiContext, create_app  # noqa: E402
from voxframe.api.security import SessionToken  # noqa: E402
from voxframe.config.settings import Settings  # noqa: E402
from voxframe.jobs.store import JobStore  # noqa: E402


@pytest.fixture
def context(tmp_path: Path) -> ApiContext:
    """An API context confined to a temporary directory.

    Built against tmp_path rather than the real settings so a test can never
    read, write or render into the user's actual library.
    """
    root = tmp_path / "web"
    library = tmp_path / "library"
    library.mkdir()
    settings = Settings(
        library_path=library,
        cache_path=tmp_path / "cache",
        output_path=tmp_path / "out",
    )
    return ApiContext(
        settings=settings,
        store=JobStore(root),
        token=SessionToken("test-token-value"),
        allowed_paths=(root.resolve(), library.resolve()),
    )


#: TestClient defaults to ``Host: testserver``, which the Host guard refuses --
#: correctly, since that is exactly the shape of a DNS rebinding request. Every
#: client here is therefore built against a loopback base URL.
LOOPBACK = "http://127.0.0.1:8765"


def _client(context: ApiContext, *, with_token: bool = True) -> TestClient:
    """A client addressing the app as a browser on this machine would."""
    test_client = TestClient(create_app(context), base_url=LOOPBACK)
    if with_token:
        test_client.headers.update({"x-voxframe-token": context.token.value})
    return test_client


@pytest.fixture
def client(context: ApiContext) -> TestClient:
    """A client that presents the right token and a loopback Host."""
    return _client(context)


class TestHealth:
    def test_health_needs_no_token(self, context: ApiContext) -> None:
        """A launcher polls this to know when to open a browser."""
        bare = _client(context, with_token=False)

        assert bare.get("/api/health").status_code == 200

    def test_health_reveals_nothing(self, context: ApiContext) -> None:
        bare = _client(context, with_token=False)

        body = bare.get("/api/health").json()

        assert set(body) == {"status", "app"}
        assert context.token.value not in str(body)


class TestTokenGuard:
    """Other local processes can reach the port but cannot read the terminal."""

    def test_a_request_without_a_token_is_refused(self, context: ApiContext) -> None:
        bare = _client(context, with_token=False)

        assert bare.get("/api/jobs").status_code == 401

    def test_a_request_with_a_wrong_token_is_refused(
        self, context: ApiContext
    ) -> None:
        bare = _client(context, with_token=False)

        response = bare.get("/api/jobs", headers={"x-voxframe-token": "wrong"})

        assert response.status_code == 401

    def test_the_right_token_in_a_header_is_accepted(self, client: TestClient) -> None:
        assert client.get("/api/jobs").status_code == 200

    def test_the_right_token_in_a_query_string_is_accepted(
        self, context: ApiContext
    ) -> None:
        """The first navigation carries it in the URL, before any JS can run."""
        bare = _client(context, with_token=False)

        response = bare.get(f"/api/jobs?token={context.token.value}")

        assert response.status_code == 200

    def test_every_api_route_is_guarded(self, context: ApiContext) -> None:
        """A route added later without the guard would be unprotected.

        The guard is middleware for exactly this reason, and this test is what
        notices if someone moves it to a per-route dependency.
        """
        bare = _client(context, with_token=False)
        unguarded = []

        for route in bare.app.routes:  # type: ignore[attr-defined]
            path = getattr(route, "path", "")
            if not path.startswith("/api") or path == "/api/health":
                continue
            # Substitute any path parameter with a value that cannot exist.
            concrete = path.replace("{job_id}", "missing").replace("{name}", "missing")
            for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
                response = bare.request(method, concrete)
                if response.status_code != 401:
                    unguarded.append(f"{method} {concrete} -> {response.status_code}")

        assert not unguarded, f"routes reachable without a token: {unguarded}"


class TestHostGuard:
    def test_a_foreign_host_is_refused(self, context: ApiContext) -> None:
        """The DNS rebinding case: the attacker's name resolving to loopback."""
        bare = _client(context, with_token=False)

        response = bare.get(
            "/api/health", headers={"host": "attacker.example.com"}
        )

        assert response.status_code == 400

    def test_the_host_check_runs_before_the_token_check(
        self, context: ApiContext
    ) -> None:
        """A rebound request must be refused even holding a valid token.

        Order matters: checking the token first would let a rebinding attack
        that has somehow learned the token through.
        """
        bare = _client(context, with_token=False)

        response = bare.get(
            "/api/jobs",
            headers={
                "host": "attacker.example.com",
                "x-voxframe-token": context.token.value,
            },
        )

        assert response.status_code == 400

    def test_loopback_host_passes(self, client: TestClient) -> None:
        response = client.get("/api/health", headers={"host": "127.0.0.1:8765"})

        assert response.status_code == 200


class TestCapabilities:
    def test_capabilities_lists_styles(self, client: TestClient) -> None:
        body = client.get("/api/capabilities").json()

        names = {style["name"] for style in body["styles"]}
        assert "documentary" in names

    def test_capabilities_reports_an_empty_library(self, client: TestClient) -> None:
        """A first-run user must be told, not left to guess (decision 6)."""
        body = client.get("/api/capabilities").json()

        assert body["library"]["assets"] == 0

    def test_capabilities_never_includes_a_key(self, client: TestClient) -> None:
        """Sourcing status is names and availability only, never a secret."""
        body = client.get("/api/capabilities").json()

        text = str(body).lower()
        assert "api_key" not in text
        for adapter in body["sourcing"]["adapters"]:
            assert isinstance(adapter, str)


class TestUploads:
    def _upload(self, client: TestClient, name: str, payload: bytes = b"x" * 64):
        return client.post(
            "/api/uploads", files={"file": (name, io.BytesIO(payload), "audio/wav")}
        )

    def test_an_audio_file_is_accepted(self, client: TestClient) -> None:
        response = self._upload(client, "talk.wav")

        assert response.status_code == 200
        assert response.json()["upload_id"]

    def test_the_upload_id_is_not_guessable(self, client: TestClient) -> None:
        first = self._upload(client, "a.wav").json()["upload_id"]
        second = self._upload(client, "b.wav").json()["upload_id"]

        assert first != second
        assert len(first) >= 32

    def test_an_unsupported_type_is_refused(self, client: TestClient) -> None:
        assert self._upload(client, "notes.pdf").status_code == 415

    def test_the_client_filename_is_not_used_as_a_path(
        self, client: TestClient, context: ApiContext
    ) -> None:
        """A filename is client input; using it as a path is a traversal bug."""
        response = self._upload(client, "../../evil.wav")

        assert response.status_code == 200
        escaped = context.store.root.parent / "evil.wav"
        assert not escaped.exists()

    def test_the_stored_file_stays_inside_the_sandbox(
        self, client: TestClient, context: ApiContext
    ) -> None:
        upload_id = self._upload(client, "talk.wav").json()["upload_id"]

        stored = list((context.store.root / "uploads" / upload_id).glob("source.*"))

        assert len(stored) == 1
        assert context.store.root.resolve() in stored[0].resolve().parents


class TestJobs:
    def test_an_unknown_job_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/jobs/nope").status_code == 404

    def test_an_unknown_upload_is_refused(self, client: TestClient) -> None:
        response = client.post("/api/jobs", json={"upload_id": "nope"})

        assert response.status_code == 404

    def test_a_traversing_upload_id_is_refused(self, client: TestClient) -> None:
        """An id becomes a path component, so it is checked like one."""
        response = client.post(
            "/api/jobs", json={"upload_id": "../../../etc"}
        )

        assert response.status_code in {400, 404}

    def test_an_invalid_aspect_is_refused(self, client: TestClient) -> None:
        """A bad option is a 4xx, not a stack trace from inside the renderer."""
        upload_id = client.post(
            "/api/uploads", files={"file": ("a.wav", io.BytesIO(b"x"), "audio/wav")}
        ).json()["upload_id"]

        response = client.post(
            "/api/jobs", json={"upload_id": upload_id, "aspect": "3:2"}
        )

        assert response.status_code == 422
        assert "3:2" in response.json()["detail"]

    def test_cancelling_an_unknown_job_is_a_404(self, client: TestClient) -> None:
        assert client.post("/api/jobs/nope/cancel").status_code == 404

    def test_the_job_list_survives_a_new_store(
        self, context: ApiContext, tmp_path: Path
    ) -> None:
        """A closed tab is not a lost render (D-101)."""
        context.store.create(audio_name="talk.wav", options={})

        reopened = JobStore(context.store.root)

        assert [job.audio_name for job in reopened.all_jobs()] == ["talk.wav"]


class TestArtifacts:
    def test_an_unknown_artifact_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/jobs/x/artifacts/video").status_code == 404

    def test_an_artifact_outside_the_sandbox_is_refused(
        self, client: TestClient, context: ApiContext, tmp_path: Path
    ) -> None:
        """Even if the mapping is wrong, the path is re-checked before serving."""
        outside = tmp_path / "secret.txt"
        outside.write_text("secret", encoding="utf-8")

        job = context.store.create(audio_name="a.wav", options={})
        context.store.record_result(
            job, artifacts={"video": outside}, warnings=(), summary={}
        )

        response = client.get(f"/api/jobs/{job.id}/artifacts/video")

        assert response.status_code == 403
        assert "secret" not in response.text

    def test_an_artifact_inside_the_sandbox_is_served(
        self, client: TestClient, context: ApiContext
    ) -> None:
        inside = context.store.root / "ok.txt"
        inside.write_text("hello", encoding="utf-8")

        job = context.store.create(audio_name="a.wav", options={})
        context.store.record_result(
            job, artifacts={"video": inside}, warnings=(), summary={}
        )

        response = client.get(f"/api/jobs/{job.id}/artifacts/video")

        assert response.status_code == 200
        assert response.text == "hello"

    def test_a_missing_plan_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/jobs/x/plan").status_code == 404
