"""Getting the models before the first video (D-157).

The real libraries' downloads are replaced with writes into a temporary cache:
these tests are about what is measured, reported and remembered.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from voxframe import model_downloads as downloads
from voxframe.config.settings import ModelProfile, Settings


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    folder = tmp_path / "hub"
    folder.mkdir()
    monkeypatch.setattr(downloads, "hub_cache", lambda: folder)
    return folder


def _hold(hub: Path, repo: str, megabytes: float) -> None:
    blob = hub / f"models--{repo.replace('/', '--')}" / "blobs" / "weights"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"\0" * int(megabytes * 1_000_000))


def _lite() -> Settings:
    return Settings(profile=ModelProfile.LITE, _env_file=None)  # type: ignore[call-arg]


class TestMeasuring:
    def test_nothing_held_is_not_ready(self, hub: Path) -> None:
        assert not any(downloads.is_ready(n) for n in downloads.model_needs(_lite()))

    def test_a_full_download_is_ready(self, hub: Path) -> None:
        whisper = downloads.model_needs(_lite())[0]
        _hold(hub, whisper.repos[0], whisper.megabytes)

        assert downloads.is_ready(whisper)

    def test_a_partial_download_is_not(self, hub: Path) -> None:
        """An abandoned download leaves a folder behind; that is not a model."""
        whisper = downloads.model_needs(_lite())[0]
        _hold(hub, whisper.repos[0], whisper.megabytes * 0.5)

        assert not downloads.is_ready(whisper)

    def test_a_linked_file_is_counted_once(self, hub: Path) -> None:
        """The cache links blobs into snapshots; counting both doubled sizes."""
        repo = hub / "models--Systran--faster-whisper-base"
        _hold(hub, "Systran/faster-whisper-base", 1)
        link = repo / "snapshots" / "abc" / "model.bin"
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(repo / "blobs" / "weights")
        except OSError:
            pytest.skip("this machine does not allow symbolic links")

        assert downloads._repo_bytes("Systran/faster-whisper-base") == 1_000_000

    def test_the_profiles_add_up_to_their_published_totals(self) -> None:
        for profile in ModelProfile:
            needs = downloads.model_needs(Settings(profile=profile, _env_file=None))  # type: ignore[call-arg]
            assert sum(n.megabytes for n in needs) == profile.approximate_download_mb


def _wait(download: downloads.Download, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while download.state == "downloading" and time.monotonic() < deadline:
        time.sleep(0.05)


class TestDownloading:
    def test_missing_models_are_fetched_and_measured(
        self, hub: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fetched: list[str] = []

        def fetch(need: downloads.ModelNeed) -> None:
            fetched.append(need.key)
            for repo in need.repos:
                _hold(hub, repo, need.megabytes / len(need.repos))

        monkeypatch.setattr(downloads, "_fetch", fetch)
        download = downloads.start_download(_lite(), downloads.Download())
        _wait(download)

        assert download.state == "done"
        assert fetched == ["whisper:base", "clip:lite"]
        assert download.snapshot()["received_mb"] == download.snapshot()["total_mb"] == 750

    def test_what_is_already_here_is_not_fetched_again(
        self, hub: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        whisper = downloads.model_needs(_lite())[0]
        _hold(hub, whisper.repos[0], whisper.megabytes)
        fetched: list[str] = []
        monkeypatch.setattr(downloads, "_fetch", lambda need: fetched.append(need.key))

        download = downloads.start_download(_lite(), downloads.Download())
        _wait(download)

        assert fetched == ["clip:lite"]
        assert download.total_mb == 605

    def test_a_failure_says_what_to_do_and_can_be_retried(
        self, hub: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(need: downloads.ModelNeed) -> None:
            raise ConnectionError("https://huggingface.co/... reset by peer")

        monkeypatch.setattr(downloads, "_fetch", fail)
        download = downloads.start_download(_lite(), downloads.Download())
        _wait(download)

        assert download.state == "failed"
        assert "try again" in download.message
        assert "huggingface" not in download.message

        monkeypatch.setattr(downloads, "_fetch", lambda need: _hold(hub, need.repos[0], need.megabytes))
        downloads.start_download(_lite(), download)
        _wait(download)
        assert download.state == "done"


# --- the routes -------------------------------------------------------------------

pytest.importorskip("fastapi", reason="web extra not installed")

from fastapi.testclient import TestClient  # noqa: E402

from voxframe.api.app import ApiContext, create_app  # noqa: E402
from voxframe.api.security import SessionToken  # noqa: E402
from voxframe.config.userprefs import load_preferences  # noqa: E402
from voxframe.jobs.store import JobStore  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, hub: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("VOXFRAME_PROFILE", raising=False)
    monkeypatch.setattr(downloads, "_fetch", lambda need: _hold(hub, need.repos[0], need.megabytes))
    root = tmp_path / "web"
    context = ApiContext(
        settings=Settings(
            library_path=tmp_path / "library", cache_path=tmp_path / "cache",
            output_path=tmp_path / "out", _env_file=None,  # type: ignore[call-arg]
        ),
        store=JobStore(root, reap_interval=None),
        token=SessionToken("t"),
        allowed_paths=(root.resolve(),),
    )
    test_client = TestClient(create_app(context), base_url="http://127.0.0.1:8765")
    test_client.headers.update({"x-voxframe-token": "t"})
    return test_client


class TestTheRoutes:
    def test_a_first_run_is_not_ready_and_says_how_big(self, client: TestClient) -> None:
        body = client.get("/api/setup/models").json()

        assert body["ready"] is False
        sizes = {choice["profile"]: choice["to_download_mb"] for choice in body["choices"]}
        assert sizes == {"standard": 3085, "lite": 750}

    def test_downloading_remembers_the_choice(self, client: TestClient) -> None:
        response = client.post("/api/setup/models/download", json={"profile": "lite"})

        assert response.status_code == 202
        assert load_preferences().model_profile == "lite"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            body = client.get("/api/setup/models").json()
            if body["download"]["state"] != "downloading":
                break
            time.sleep(0.05)
        assert body["ready"] is True and body["profile"] == "lite"

    def test_the_environment_wins(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOXFRAME_PROFILE", "standard")

        client.post("/api/setup/models/download", json={"profile": "lite"})

        assert load_preferences().model_profile == ""

    def test_only_known_profiles(self, client: TestClient) -> None:
        response = client.post("/api/setup/models/download", json={"profile": "huge"})

        assert response.status_code == 422
