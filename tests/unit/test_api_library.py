"""The Library screen's routes (D-146).

The embedding model is replaced with a fake that gives every image a distinct
vector: these tests are about what the routes accept, record, reveal and
delete. The real model is exercised by the browser verification.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient
from PIL import Image

from voxframe.api.app import ApiContext, create_app
from voxframe.api.security import SessionToken
from voxframe.config.settings import Settings
from voxframe.config.userprefs import load_preferences
from voxframe.jobs.store import JobStore
from voxframe.library import manage
from voxframe.library.db import AssetLibrary
from voxframe.plan.scene_plan import PlanAsset, PlannedScene, ScenePlan

LOOPBACK = "http://127.0.0.1:8765"


class FakeEmbedder:
    model_id = "fake/model"

    def __init__(self) -> None:
        self.calls = 0

    def embed_images(self, paths: list[Path]) -> list[list[float]]:
        vectors = []
        for _ in paths:
            self.calls += 1
            vector = [0.0] * 512
            vector[self.calls % 512] = 1.0
            vectors.append(vector)
        return vectors


@pytest.fixture
def library(tmp_path: Path) -> Path:
    return tmp_path / "library"


@pytest.fixture
def context(tmp_path: Path, library: Path) -> ApiContext:
    root = tmp_path / "web"
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
def client(context: ApiContext, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    embedder = FakeEmbedder()
    monkeypatch.setattr(manage, "shared_embedder", lambda settings: embedder)
    test_client = TestClient(create_app(context), base_url=LOOPBACK)
    test_client.headers.update({"x-voxframe-token": "t"})
    return test_client


def _photo(colour: str, size: tuple[int, int] = (320, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "JPEG")
    return buffer.getvalue()


def _upload(client: TestClient, *photos: tuple[str, bytes], author: str = "Ada Lovelace"):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/library",
        files=[("files", (name, data, "image/jpeg")) for name, data in photos],
        data={"author": author, "license": "Own work"},
    )


class TestUploading:
    def test_photos_are_added_with_the_person_credited(
        self, client: TestClient, library: Path
    ) -> None:
        response = _upload(client, ("a.jpg", _photo("red")), ("b.jpg", _photo("blue")))

        assert response.status_code == 200
        assert response.json()["added"] == 2
        assets = AssetLibrary(library).all_assets()
        assert {asset.license.author for asset in assets} == {"Ada Lovelace"}
        assert {asset.license.name for asset in assets} == {"Own work"}
        assert {asset.license.source for asset in assets} == {"local"}

    def test_credits_read_as_own_work_without_via(
        self, client: TestClient, library: Path
    ) -> None:
        _upload(client, ("a.jpg", _photo("red")))
        asset = AssetLibrary(library).all_assets()[0]

        assert PlanAsset.from_asset(asset).attribution() == "Ada Lovelace (Own work)"

    def test_the_name_is_remembered(self, client: TestClient) -> None:
        _upload(client, ("a.jpg", _photo("red")))

        assert load_preferences().library_author == "Ada Lovelace"
        assert client.get("/api/library").json()["author"] == "Ada Lovelace"

    def test_a_name_is_required(self, client: TestClient) -> None:
        """A blank would credit nobody (D-012)."""
        response = _upload(client, ("a.jpg", _photo("red")), author="   ")

        assert response.status_code == 422
        assert "whose work" in response.json()["detail"]

    def test_the_same_photo_twice_is_kept_once(
        self, client: TestClient, library: Path
    ) -> None:
        _upload(client, ("a.jpg", _photo("red")))
        second = _upload(client, ("again.jpg", _photo("red")))

        assert second.json()["already_there"] == 1
        assert len(AssetLibrary(library).all_assets()) == 1
        assert len(list((library / "own").rglob("*.jpg"))) == 1

    def test_something_that_is_not_media_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/library",
            files=[("files", ("notes.txt", b"hello", "text/plain"))],
            data={"author": "Ada"},
        )

        assert response.status_code == 415

    def test_a_renamed_file_is_refused(self, client: TestClient, library: Path) -> None:
        """A file named .jpg must be an image before it reaches FFmpeg."""
        response = client.post(
            "/api/library",
            files=[("files", ("fake.jpg", b"not an image at all", "image/jpeg"))],
            data={"author": "Ada"},
        )

        assert response.status_code == 415
        assert not list((library / "own").rglob("*.jpg"))


class TestBrowsing:
    def test_paths_are_never_sent(self, client: TestClient, library: Path) -> None:
        _upload(client, ("a.jpg", _photo("red")))

        body = client.get("/api/library").text

        assert str(library) not in body
        assert "own" not in client.get("/api/library").json()["assets"][0].get("path", "")

    def test_each_asset_has_a_thumbnail(self, client: TestClient) -> None:
        _upload(client, ("a.jpg", _photo("red")))
        asset_id = client.get("/api/library").json()["assets"][0]["id"]

        response = client.get(f"/api/library/{asset_id}/thumbnail")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"

    def test_filtering_by_source(self, client: TestClient) -> None:
        _upload(client, ("a.jpg", _photo("red")))

        assert client.get("/api/library?source=local").json()["total"] == 1
        assert client.get("/api/library?source=pexels").json()["total"] == 0

    def test_videos_that_show_an_asset_are_listed(
        self, client: TestClient, context: ApiContext, library: Path
    ) -> None:
        _upload(client, ("a.jpg", _photo("red")))
        asset = AssetLibrary(library).all_assets()[0]
        job = context.store.create(audio_name="talk.m4a", options={"title": "My Talk"})
        plan_path = context.store.job_directory(job.id) / "source.plan.json"
        ScenePlan(
            audio_path="a.wav", audio_sha256="0" * 64, audio_duration=3.0,
            fps=30.0, total_frames=90,
            scenes=(
                PlannedScene(
                    index=0, start_frame=0, end_frame=90, text="red",
                    asset=PlanAsset.from_asset(asset),
                ),
            ),
        ).save(plan_path)
        context.store.submit(job, lambda _job: None)
        assert job.future is not None
        job.future.result(timeout=60)
        context.store.record_result(job, artifacts={"plan": plan_path}, warnings=(), summary={})

        used_in = client.get("/api/library").json()["assets"][0]["used_in"]

        assert used_in == [{"job_id": job.id, "title": "My Talk"}]


class TestDeleting:
    def test_an_upload_is_deleted_with_its_file(
        self, client: TestClient, library: Path
    ) -> None:
        _upload(client, ("a.jpg", _photo("red")))
        asset = AssetLibrary(library).all_assets()[0]

        response = client.delete(f"/api/library/{asset.id}")

        assert response.json() == {"removed": True, "file_deleted": True}
        assert AssetLibrary(library).get(asset.id) is None
        assert not Path(asset.path).exists()

    def test_a_file_the_app_did_not_add_is_kept(
        self, client: TestClient, library: Path, tmp_path: Path
    ) -> None:
        """A folder the person ingested themselves is theirs, not the app's to empty."""
        from voxframe.models.asset import Asset, AssetKind, LicenseInfo

        mine = tmp_path / "my-photos" / "beach.jpg"
        mine.parent.mkdir()
        mine.write_bytes(_photo("yellow"))
        library.mkdir(parents=True, exist_ok=True)
        AssetLibrary(library).add(
            Asset(
                id="beach", path=mine, kind=AssetKind.IMAGE, sha256="1" * 64,
                width=320, height=200,
                license=LicenseInfo(name="CC0-1.0", author="Me", source="local"),
            ),
            embedding=[1.0] + [0.0] * 511,
            embed_model="fake/model",
        )

        response = client.delete("/api/library/beach")

        assert response.json() == {"removed": True, "file_deleted": False}
        assert mine.is_file()

    def test_an_unknown_asset(self, client: TestClient, library: Path) -> None:
        library.mkdir(parents=True, exist_ok=True)

        assert client.delete("/api/library/nothing").status_code == 404
