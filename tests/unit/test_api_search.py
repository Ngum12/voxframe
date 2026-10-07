"""Searching online for one scene, by hand (D-142).

The image services are replaced with fakes: these tests are about what the
routes allow, what they reveal and what they record. The real services are
exercised by the browser verification.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient

from voxframe.api.app import ApiContext, create_app
from voxframe.api.security import SessionToken
from voxframe.config.settings import Settings
from voxframe.jobs.store import JobStore
from voxframe.models.asset import LicenseInfo
from voxframe.plan.scene_plan import MotionKind, PlannedScene, ScenePlan
from voxframe.sourcing import manual
from voxframe.sourcing.base import Candidate

LOOPBACK = "http://127.0.0.1:8765"

RIVER = Candidate(
    url="https://images.example/river-full.jpg",
    preview_url="https://images.example/river-small.jpg",
    license=LicenseInfo(
        name="Pexels License", author="Ada Photographer", source="pexels",
        source_url="https://www.pexels.com/photo/river-1/",
    ),
    width=4000,
    height=2600,
    title="A river at dawn",
)


@pytest.fixture
def context(tmp_path: Path) -> ApiContext:
    root = tmp_path / "web"
    library = tmp_path / "library"
    library.mkdir()
    return ApiContext(
        settings=Settings(
            library_path=library, cache_path=tmp_path / "cache",
            output_path=tmp_path / "out",
        ),
        store=JobStore(root, reap_interval=None),
        token=SessionToken("t"),
        allowed_paths=(root.resolve(), library.resolve()),
    )


@pytest.fixture
def client(context: ApiContext) -> TestClient:
    test_client = TestClient(create_app(context), base_url=LOOPBACK)
    test_client.headers.update({"x-voxframe-token": "t"})
    return test_client


@pytest.fixture
def search_on(client: TestClient) -> None:
    client.put(
        "/api/settings",
        json={"sourcing_consent": True, "api_keys": {"pexels": "abc123"}},
    )


def _finished_job(context: ApiContext) -> str:
    job = context.store.create(audio_name="a.wav", options={"height": 480})
    directory = context.store.job_directory(job.id)
    plan_path = directory / "source.plan.json"
    ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64, audio_duration=6.0,
        fps=30.0, total_frames=180,
        scenes=(
            PlannedScene(
                index=0, start_frame=0, end_frame=180, text="a river at dawn",
                motion=MotionKind.NONE,
            ),
        ),
    ).save(plan_path)
    context.store.submit(job, lambda _job: None)
    assert job.future is not None
    job.future.result(timeout=60)
    context.store.record_result(job, artifacts={"plan": plan_path}, warnings=(), summary={})
    return job.id


@pytest.fixture
def job_id(context: ApiContext) -> str:
    return _finished_job(context)


@pytest.fixture
def fake_services(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Search returns the river; fetching writes a real small JPEG."""
    queries: list[str] = []

    def search(query: str, plan: object, settings: object) -> tuple[list[Candidate], list[str]]:
        queries.append(query)
        return [RIVER], []

    def write_jpeg(path: Path) -> None:
        from PIL import Image

        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 40), "steelblue").save(path, "JPEG")

    def fetch_preview(candidate: Candidate, directory: Path, token: str) -> Path:
        target = directory / f"{token}.jpg"
        write_jpeg(target)
        return target

    def fetch_choice(
        candidate: Candidate, directory: Path, query: str, settings: object
    ) -> tuple[Path, int, int]:
        target = directory / "chosen" / "pexels_0000.jpg"
        write_jpeg(target)
        return target, 64, 40

    monkeypatch.setattr(manual, "search", search)
    monkeypatch.setattr(manual, "fetch_preview", fetch_preview)
    monkeypatch.setattr(manual, "fetch_choice", fetch_choice)
    return queries


class TestSearching:
    def test_refused_while_online_search_is_off(
        self, client: TestClient, job_id: str, fake_services: list[str]
    ) -> None:
        """The settings screen must mean what it says (D-116)."""
        response = client.post(f"/api/jobs/{job_id}/scenes/0/search", json={"query": "river"})

        assert response.status_code == 409
        assert "Settings" in response.json()["detail"]
        assert fake_services == [], "nothing may be sent to a service"

    def test_results_come_back_under_tokens_never_urls(
        self, client: TestClient, job_id: str, search_on: None, fake_services: list[str]
    ) -> None:
        """The browser names what it was shown; it never sends an address the
        server would then fetch."""
        response = client.post(f"/api/jobs/{job_id}/scenes/0/search", json={"query": "river"})

        assert response.status_code == 200
        result = response.json()["results"][0]
        assert result["token"]
        assert result["author"] == "Ada Photographer"
        assert result["license"] == "Pexels License"
        assert "example" not in response.text

    def test_an_empty_query_is_refused_in_plain_words(
        self, client: TestClient, job_id: str, search_on: None
    ) -> None:
        response = client.post(f"/api/jobs/{job_id}/scenes/0/search", json={"query": "   "})

        assert response.status_code == 422
        assert "Type what you would like to see" in response.json()["detail"]

    def test_a_scene_that_does_not_exist(
        self, client: TestClient, job_id: str, search_on: None, fake_services: list[str]
    ) -> None:
        response = client.post(f"/api/jobs/{job_id}/scenes/9/search", json={"query": "river"})

        assert response.status_code == 404


class TestPreviews:
    def test_a_result_has_a_preview(
        self, client: TestClient, job_id: str, search_on: None, fake_services: list[str]
    ) -> None:
        token = client.post(
            f"/api/jobs/{job_id}/scenes/0/search", json={"query": "river"}
        ).json()["results"][0]["token"]

        response = client.get(f"/api/jobs/{job_id}/search/{token}/preview")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"

    def test_an_unknown_token_is_a_404(
        self, client: TestClient, job_id: str, fake_services: list[str]
    ) -> None:
        assert client.get(f"/api/jobs/{job_id}/search/nothing/preview").status_code == 404

    def test_a_token_belongs_to_its_own_job(
        self,
        client: TestClient,
        context: ApiContext,
        job_id: str,
        search_on: None,
        fake_services: list[str],
    ) -> None:
        token = client.post(
            f"/api/jobs/{job_id}/scenes/0/search", json={"query": "river"}
        ).json()["results"][0]["token"]
        other = _finished_job(context)

        assert client.get(f"/api/jobs/{other}/search/{token}/preview").status_code == 404


class TestUsingAResult:
    def test_the_scene_gets_the_image_with_its_provenance(
        self,
        client: TestClient,
        context: ApiContext,
        job_id: str,
        search_on: None,
        fake_services: list[str],
    ) -> None:
        token = client.post(
            f"/api/jobs/{job_id}/scenes/0/search", json={"query": "river"}
        ).json()["results"][0]["token"]

        response = client.post(
            f"/api/jobs/{job_id}/scenes/0/search/{token}", json={"query": "river"}
        )

        assert response.status_code == 200
        scene = response.json()["scene"]
        assert scene["asset"]["license_author"] == "Ada Photographer"
        assert scene["asset"]["license_source"] == "pexels"
        assert scene["asset"]["license_url"] == "https://www.pexels.com/photo/river-1/"
        assert scene["asset_source"] == "user"
        assert response.json()["pending_edits"] == 1

        plan_path = context.store.artifact_path(job_id, "plan")
        assert plan_path is not None
        saved = ScenePlan.load(plan_path).scenes[0]
        assert saved.asset is not None
        assert Path(saved.asset.path).is_file()

    def test_the_chosen_image_has_a_thumbnail(
        self, client: TestClient, job_id: str, search_on: None, fake_services: list[str]
    ) -> None:
        """It lives with the job, inside the sandbox, so the filmstrip can show it."""
        token = client.post(
            f"/api/jobs/{job_id}/scenes/0/search", json={"query": "river"}
        ).json()["results"][0]["token"]
        client.post(f"/api/jobs/{job_id}/scenes/0/search/{token}", json={"query": "river"})

        assert client.get(f"/api/jobs/{job_id}/scenes/0/thumbnail").status_code == 200

    def test_an_expired_result_says_to_search_again(
        self, client: TestClient, job_id: str, search_on: None, fake_services: list[str]
    ) -> None:
        response = client.post(
            f"/api/jobs/{job_id}/scenes/0/search/gone", json={"query": "river"}
        )

        assert response.status_code == 404
        assert "Search again" in response.json()["detail"]


class TestWhatReachesTheBrowser:
    def test_a_failed_service_is_named_and_nothing_more(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pixabay's request URL carries the key, and an adapter's error can
        quote it. Only the service's name may leave the server."""
        secret = "pixabay-key-that-must-not-leak"

        def failing(adapters: object, request: object) -> tuple[list[Candidate], list[tuple[str, str]]]:
            return [], [("pixabay", f"HTTP 400 for https://pixabay.com/api/?key={secret}")]

        monkeypatch.setattr(manual, "search_adapters", failing)
        monkeypatch.setattr(manual, "build_adapters", lambda settings, policy: [])
        plan = ScenePlan(
            audio_path="a.wav", audio_sha256="0" * 64, audio_duration=3.0,
            fps=30.0, total_frames=90,
            scenes=(PlannedScene(index=0, start_frame=0, end_frame=90, text="river"),),
        )

        found, failed = manual.search("river", plan, Settings())

        assert found == []
        assert failed == ["Pixabay"]
        assert secret not in repr(failed)

    def test_results_expire_oldest_first(self) -> None:
        results = manual.SearchResults()
        first = results.remember("job", [RIVER])[0]
        for _ in range(manual.REMEMBERED_PER_JOB):
            results.remember("job", [RIVER])

        assert results.get("job", first) is None
