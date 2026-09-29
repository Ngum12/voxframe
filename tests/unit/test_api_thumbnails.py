"""The scene thumbnail route (D-121).

The filmstrip needs to show what each scene chose, which means serving files
from the library. The plan is a file a **user may have edited by hand**, so the
paths inside it are untrusted input and get the same treatment as anything a
client sends.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient  # noqa: E402

from voxframe.api.app import ApiContext, create_app  # noqa: E402
from voxframe.api.security import SessionToken  # noqa: E402
from voxframe.config.settings import Settings  # noqa: E402
from voxframe.jobs.store import JobStore  # noqa: E402

LOOPBACK = "http://127.0.0.1:8765"


def _plan(scenes: list[dict]) -> dict:
    return {
        "version": 2,
        "audio_path": "a.wav",
        "audio_sha256": "0" * 64,
        "audio_duration": 10.0,
        "fps": 30.0,
        "total_frames": 300,
        "scenes": scenes,
    }


def _scene(index: int, asset_path: str | None = None, **extra: object) -> dict:
    scene: dict = {
        "index": index,
        "start_frame": index * 100,
        "end_frame": (index + 1) * 100,
        "text": "spoken words",
        "asset": None,
    }
    if asset_path is not None:
        scene["asset"] = {
            "id": f"asset-{index}",
            "path": asset_path,
            "width": 1200,
            "height": 800,
            "kind": "image",
            "license_name": "CC0",
            "license_author": "Someone",
            "license_source": "Test",
        }
    scene.update(extra)
    return scene


@pytest.fixture
def library(tmp_path: Path) -> Path:
    directory = tmp_path / "library"
    directory.mkdir()
    return directory


@pytest.fixture
def context(tmp_path: Path, library: Path) -> ApiContext:
    root = tmp_path / "web"
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


def _job_with_plan(context: ApiContext, scenes: list[dict]) -> str:
    job = context.store.create(audio_name="a.wav", options={})
    plan_path = context.store.job_directory(job.id) / "plan.json"
    plan_path.write_text(json.dumps(_plan(scenes)), encoding="utf-8")
    context.store.record_result(
        job, artifacts={"plan": plan_path}, warnings=(), summary={}
    )
    return job.id


class TestMissingCases:
    def test_no_job_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/jobs/nope/scenes/0/thumbnail").status_code == 404

    def test_a_scene_out_of_range_is_a_404(
        self, client: TestClient, context: ApiContext
    ) -> None:
        job_id = _job_with_plan(context, [_scene(0)])

        assert client.get(f"/api/jobs/{job_id}/scenes/9/thumbnail").status_code == 404

    def test_a_negative_index_is_refused(
        self, client: TestClient, context: ApiContext
    ) -> None:
        job_id = _job_with_plan(context, [_scene(0)])

        assert client.get(f"/api/jobs/{job_id}/scenes/-1/thumbnail").status_code in {
            404,
            422,
        }

    def test_a_scene_with_no_asset_is_a_404(
        self, client: TestClient, context: ApiContext
    ) -> None:
        """A gradient scene has nothing to show, and says so rather than 500."""
        job_id = _job_with_plan(context, [_scene(0)])

        assert client.get(f"/api/jobs/{job_id}/scenes/0/thumbnail").status_code == 404

    def test_a_missing_file_is_a_404(
        self, client: TestClient, context: ApiContext, library: Path
    ) -> None:
        """The library may have been cleaned since the render."""
        job_id = _job_with_plan(context, [_scene(0, str(library / "gone.jpg"))])

        assert client.get(f"/api/jobs/{job_id}/scenes/0/thumbnail").status_code == 404


class TestSandbox:
    """A plan is editable, so its paths are untrusted (D-115)."""

    def test_a_path_outside_the_library_is_refused(
        self, client: TestClient, context: ApiContext, tmp_path: Path
    ) -> None:
        secret = tmp_path / "id_rsa"
        secret.write_text("PRIVATE KEY", encoding="utf-8")
        job_id = _job_with_plan(context, [_scene(0, str(secret))])

        response = client.get(f"/api/jobs/{job_id}/scenes/0/thumbnail")

        assert response.status_code == 403
        assert "PRIVATE KEY" not in response.text

    def test_a_traversing_path_is_refused(
        self, client: TestClient, context: ApiContext, library: Path
    ) -> None:
        job_id = _job_with_plan(
            context, [_scene(0, str(library / ".." / ".." / "secret.txt"))]
        )

        assert client.get(f"/api/jobs/{job_id}/scenes/0/thumbnail").status_code == 403

    def test_the_refusal_does_not_reveal_the_path(
        self, client: TestClient, context: ApiContext, tmp_path: Path
    ) -> None:
        """A refusal should not hand over the machine's layout (D-115)."""
        secret = tmp_path / "very-private-name.jpg"
        secret.write_text("x", encoding="utf-8")
        job_id = _job_with_plan(context, [_scene(0, str(secret))])

        response = client.get(f"/api/jobs/{job_id}/scenes/0/thumbnail")

        assert "very-private-name" not in response.text

    def test_the_route_needs_a_token(self, context: ApiContext) -> None:
        job_id = _job_with_plan(context, [_scene(0)])
        bare = TestClient(create_app(context), base_url=LOOPBACK)

        assert bare.get(f"/api/jobs/{job_id}/scenes/0/thumbnail").status_code == 401


@pytest.mark.needs_ffmpeg
class TestGeneration:
    """Generating a real thumbnail, when FFmpeg is available."""

    @pytest.fixture
    def photo(self, library: Path) -> Path:
        from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
        from voxframe.render.ffpath import run_ffmpeg

        try:
            capabilities = probe_capabilities()
        except FFmpegNotFound:
            pytest.skip("FFmpeg not available")

        target = library / "photo.png"
        run_ffmpeg(
            capabilities.ffmpeg_path,
            [
                "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=size=1200x800:duration=1",
                "-frames:v", "1", "-y", str(target),
            ],
        )
        return target

    def test_a_thumbnail_is_served(
        self, client: TestClient, context: ApiContext, photo: Path
    ) -> None:
        job_id = _job_with_plan(context, [_scene(0, str(photo))])

        response = client.get(f"/api/jobs/{job_id}/scenes/0/thumbnail")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert len(response.content) > 500

    def test_the_thumbnail_is_scaled_down(
        self, client: TestClient, context: ApiContext, photo: Path
    ) -> None:
        """A 148-scene filmstrip must not download 148 full photographs.

        Asserted on dimensions rather than bytes: the route controls the size
        it scales to, while the byte count also depends on the content. A
        synthetic test pattern compresses far better as PNG than as JPEG, so a
        byte comparison fails here for reasons that have nothing to do with the
        route being correct.
        """
        import io

        from PIL import Image

        from voxframe.api.app import THUMBNAIL_PIXELS

        job_id = _job_with_plan(context, [_scene(0, str(photo))])

        response = client.get(f"/api/jobs/{job_id}/scenes/0/thumbnail")
        width, height = Image.open(io.BytesIO(response.content)).size

        assert max(width, height) <= THUMBNAIL_PIXELS
        assert width < 1200

    def test_the_second_request_is_served_from_cache(
        self, client: TestClient, context: ApiContext, photo: Path
    ) -> None:
        """Generating one costs an FFmpeg invocation; a filmstrip asks often."""
        job_id = _job_with_plan(context, [_scene(0, str(photo))])
        cache = context.store.job_directory(job_id) / "thumbnails"

        client.get(f"/api/jobs/{job_id}/scenes/0/thumbnail")
        first = sorted(cache.glob("*.jpg"))
        client.get(f"/api/jobs/{job_id}/scenes/0/thumbnail")

        assert len(first) == 1
        assert sorted(cache.glob("*.jpg")) == first

    def test_a_different_image_gets_a_different_thumbnail(
        self, client: TestClient, context: ApiContext, photo: Path, library: Path
    ) -> None:
        """After a swap the scene must show its NEW image (D-128).

        The cache was once keyed on scene index and mtime alone, and two stock
        images downloaded in the same second share an mtime -- so a swapped
        scene would have kept showing its old picture.
        """
        import os
        import shutil

        other = library / "other.png"
        shutil.copy(photo, other)
        # Same modification time, to reproduce the collision exactly.
        stamp = photo.stat().st_mtime_ns
        os.utime(other, ns=(stamp, stamp))
        other.write_bytes(other.read_bytes() + b"\0")  # different content...
        os.utime(other, ns=(stamp, stamp))  # ...same mtime

        first = _job_with_plan(context, [_scene(0, str(photo))])
        second = _job_with_plan(context, [_scene(0, str(other))])
        client.get(f"/api/jobs/{first}/scenes/0/thumbnail")
        client.get(f"/api/jobs/{second}/scenes/0/thumbnail")

        from voxframe.api.app import _thumbnail_for

        assert _thumbnail_for(context, first, photo) != _thumbnail_for(
            context, first, other
        )
