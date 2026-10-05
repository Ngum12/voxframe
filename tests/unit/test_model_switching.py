"""Switch downloaded profiles and search the same real library after each switch."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from voxframe import model_downloads
from voxframe.api.app import ApiContext, create_app
from voxframe.api.security import SessionToken
from voxframe.config.settings import Settings
from voxframe.config.userprefs import load_preferences
from voxframe.jobs.store import JobStore
from voxframe.library import manage
from voxframe.library.db import AssetLibrary
from voxframe.library.embeddings import EMBEDDING_MODELS
from voxframe.models.asset import Asset, AssetKind, LicenseInfo


def model_id(key: str) -> str:
    return "/".join(EMBEDDING_MODELS[key][:2])


class ImageEmbedder:
    def __init__(self, key: str) -> None:
        self.model_id = model_id(key)

    def embed_images(self, paths: list[Path]) -> list[list[float]]:
        for path in paths:
            with Image.open(path) as image:
                image.verify()
        return [[1.0] + [0.0] * 511 for _ in paths]


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("VOXFRAME_PROFILE", raising=False)
    monkeypatch.delenv("VOXFRAME_EMBED_MODEL", raising=False)
    settings = Settings(library_path=tmp_path / "actual-library", _env_file=None)
    library = AssetLibrary(settings.library_path)
    image = settings.library_path / "photo.jpg"
    Image.new("RGB", (32, 32), "red").save(image)
    asset = Asset(
        id="photo", path=image, kind=AssetKind.IMAGE, sha256="a" * 64,
        width=32, height=32, license=LicenseInfo(name="CC0", author="Ada", source="local"),
    )
    library.add(asset, embedding=[1.0] + [0.0] * 511, embed_model=model_id("lite"))
    context = ApiContext(
        settings=settings, store=JobStore(tmp_path / "jobs", reap_interval=None),
        token=SessionToken("test"), allowed_paths=(tmp_path,),
    )
    monkeypatch.setattr(model_downloads, "is_ready", lambda need: True)
    monkeypatch.setattr(model_downloads, "_fetch", lambda need: pytest.fail("Already downloaded"))
    monkeypatch.setattr(manage, "shared_embedder", lambda s: ImageEmbedder(s.resolved_embed_model))
    with TestClient(create_app(context), base_url="http://127.0.0.1:8765") as client:
        client.headers.update({"x-voxframe-token": "test"})
        yield client, context, library, image
    context.store.shutdown()


def wait(client: TestClient) -> dict:  # type: ignore[type-arg]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get("/api/setup/models").json()
        if status["download"]["state"] not in {"downloading", "updating"}:
            return status
        time.sleep(0.01)
    pytest.fail("Model change did not finish")


def test_switching_both_ways_reembeds_the_actual_library(setup):  # type: ignore[no-untyped-def]
    client, _, library, image = setup
    for profile, key in (("standard", "default"), ("lite", "lite"), ("standard", "default")):
        assert client.post("/api/setup/models/download", json={"profile": profile}).status_code == 202
        status = wait(client)
        assert status["ready"] and status["profile"] == profile
        assert status["download"]["state"] == "done"
        assert status["download"]["completed_assets"] == 1
        assert library.embedding_models() == {model_id(key)}
        assert load_preferences().model_profile == profile
        assert library.get("photo").path == image
        assert library.search([1.0] + [0.0] * 511, embed_model=model_id(key))


def test_downloaded_models_are_not_ready_with_an_incompatible_library(setup):  # type: ignore[no-untyped-def]
    client, _, _, _ = setup
    status = client.get("/api/setup/models").json()
    assert all(choice["ready"] for choice in status["choices"])
    assert not status["ready"]


def test_status_uses_one_worker_snapshot_when_update_finishes(setup, monkeypatch):  # type: ignore[no-untyped-def]
    client, context, _, _ = setup
    client.post("/api/setup/models/download", json={"profile": "standard"})
    assert wait(client)["ready"]
    download = context.downloads
    original = download.snapshot
    with download._lock:
        download.state = "updating"

    def finish_after_snapshot():
        snapshot = original()
        with download._lock:
            download.state = "done"
        return snapshot

    monkeypatch.setattr(download, "snapshot", finish_after_snapshot)
    status = client.get("/api/setup/models").json()
    assert status["download"]["state"] == "updating"
    assert not status["ready"]
    status = client.get("/api/setup/models").json()
    assert status["download"]["state"] == "done"
    assert status["ready"]


def test_missing_original_is_reported_and_can_be_retried(setup):  # type: ignore[no-untyped-def]
    client, _, library, image = setup
    image.unlink()
    client.post("/api/setup/models/download", json={"profile": "standard"})
    status = wait(client)
    assert not status["ready"]
    assert status["download"]["state"] == "failed"
    assert "original pictures and clips" in status["download"]["message"]
    assert library.embedding_models() == {model_id("lite")}
    Image.new("RGB", (32, 32), "red").save(image)
    client.post("/api/setup/models/download", json={"profile": "standard"})
    assert wait(client)["ready"]


def test_render_and_second_switch_wait_for_library_update(setup, monkeypatch):  # type: ignore[no-untyped-def]
    client, _, _, _ = setup
    entered, release = threading.Event(), threading.Event()
    original = ImageEmbedder.embed_images
    def blocked(self, paths):  # type: ignore[no-untyped-def]
        entered.set()
        assert release.wait(5)
        return original(self, paths)
    monkeypatch.setattr(ImageEmbedder, "embed_images", blocked)
    try:
        client.post("/api/setup/models/download", json={"profile": "standard"})
        assert entered.wait(5)
        status = client.get("/api/setup/models").json()
        assert status["download"]["state"] == "updating"
        assert status["download"]["total_assets"] == 1 and not status["ready"]
        assert client.post("/api/jobs", json={}).status_code == 409
        assert client.post("/api/setup/models/download", json={"profile": "lite"}).status_code == 409
        assert load_preferences().model_profile == "standard"
    finally:
        release.set()
    assert wait(client)["ready"]


def test_active_render_prevents_switching(setup):  # type: ignore[no-untyped-def]
    client, context, _, _ = setup
    context.store.create(audio_name="recording.wav", options={})
    response = client.post("/api/setup/models/download", json={"profile": "lite"})
    assert response.status_code == 409
    assert load_preferences().model_profile == ""


def test_clips_use_a_picture_frame_when_switching(setup):  # type: ignore[no-untyped-def]
    client, _, library, image = setup
    clip = image.parent / "clip.mp4"
    clip.write_bytes(b"video data; the cached frame is used")
    frame = image.parent / ".frames" / f"{'b' * 16}.jpg"
    frame.parent.mkdir()
    Image.new("RGB", (32, 32), "blue").save(frame)
    asset = Asset(
        id="clip", path=clip, kind=AssetKind.VIDEO, sha256="b" * 64,
        width=32, height=32, duration=2,
        license=LicenseInfo(name="CC0", author="Ada", source="local"),
    )
    library.add(asset, embedding=[1.0] + [0.0] * 511, embed_model=model_id("lite"))
    client.post("/api/setup/models/download", json={"profile": "standard"})
    assert wait(client)["ready"]
    assert library.get("clip").kind == AssetKind.VIDEO
    assert library.embedding_models() == {model_id("default")}


def test_missing_clip_frame_is_recreated(setup, monkeypatch):  # type: ignore[no-untyped-def]
    from voxframe.library import ingest

    client, _, library, image = setup
    clip = image.parent / "clip.mp4"
    clip.write_bytes(b"clip")
    asset = Asset(
        id="clip", path=clip, kind=AssetKind.VIDEO, sha256="c" * 64,
        width=32, height=32, duration=2,
        license=LicenseInfo(name="CC0", author="Ada", source="local"),
    )
    library.add(asset, embedding=[1.0] + [0.0] * 511, embed_model=model_id("lite"))
    calls = []
    def extract(path, destination, duration):  # type: ignore[no-untyped-def]
        calls.append((path, duration))
        Image.new("RGB", (32, 32), "blue").save(destination)
        return destination
    monkeypatch.setattr(ingest, "_extract_clip_frame", extract)
    client.post("/api/setup/models/download", json={"profile": "standard"})
    assert wait(client)["ready"]
    assert calls == [(clip, 2)]
    assert library.embedding_models() == {model_id("default")}


def test_new_uploads_use_the_selected_profile(setup):  # type: ignore[no-untyped-def]
    client, _, library, image = setup
    client.post("/api/setup/models/download", json={"profile": "lite"})
    assert wait(client)["ready"]
    response = client.post(
        "/api/library", files=[("files", ("new.jpg", image.read_bytes(), "image/jpeg"))],
        data={"author": "Ada", "license": "Own work"},
    )
    assert response.status_code == 200
    assert library.embedding_models() == {model_id("lite")}
