"""The route for a scene's layout, and its preview (D-197)."""

from __future__ import annotations

from io import BytesIO

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi.testclient import TestClient
from PIL import Image

from tests.unit.test_api_footage import _finished_job, _plan, _saved, _video, client, context
from voxframe.api.app import ApiContext
from voxframe.plan.scene_layout import InsetShape, LayoutKind

__all__ = ["client", "context"]  # the fixtures, shared


def _job(context: ApiContext, *, real: bool = False) -> str:
    recording = context.store.root / "uploads" / "r" / "source.mp4"
    if real:
        _video(recording)
    else:
        recording.parent.mkdir(parents=True)
        recording.write_bytes(b"x")
    plan = _plan(recording, context.settings.library_path)
    if real:
        Image.new("RGB", (800, 450), (220, 20, 20)).save(context.settings.library_path / "river.jpg")
    return _finished_job(context, plan)


class TestLayout:
    def test_a_scene_can_be_split(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        response = client.put(
            f"/api/jobs/{job}/scenes/1/layout", json={"kind": "split", "split": 0.4}
        )
        assert response.status_code == 200
        layout = _saved(context, job).scenes[1].layout
        assert layout.kind is LayoutKind.SPLIT and layout.split == 0.4
        assert response.json()["undo_label"] == "a split screen"

    def test_or_inset(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        client.put(
            f"/api/jobs/{job}/scenes/1/layout",
            json={"kind": "inset", "inset_shape": "rounded", "inset_size": 0.3},
        )
        layout = _saved(context, job).scenes[1].layout
        assert layout.kind is LayoutKind.INSET and layout.inset_shape is InsetShape.ROUNDED

    def test_and_back_to_full(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        client.put(f"/api/jobs/{job}/scenes/1/layout", json={"kind": "split"})
        client.put(f"/api/jobs/{job}/scenes/1/layout", json={"kind": "full"})
        assert _saved(context, job).scenes[1].layout.is_full

    def test_out_of_range_is_refused(self, client: TestClient, context: ApiContext) -> None:
        job = _job(context)
        response = client.put(
            f"/api/jobs/{job}/scenes/1/layout", json={"kind": "inset", "inset_size": 0.95}
        )
        assert response.status_code == 422
        assert _saved(context, job).scenes[1].layout.is_full

    def test_a_video_from_sound_only_cannot_split(
        self, client: TestClient, context: ApiContext
    ) -> None:
        job = _job(context)
        path = context.store.artifact_path(job, "plan")
        assert path is not None
        plan = _saved(context, job)
        sound_only = plan.model_copy(update={
            "footage": None,
            "scenes": tuple(
                s.model_copy(update={"shot": "picture", "footage_start": None}) for s in plan.scenes
            ),
        })
        sound_only.save(path)
        response = client.put(f"/api/jobs/{job}/scenes/1/layout", json={"kind": "split"})
        assert response.status_code == 422
        assert "sound only" in response.json()["detail"]

    def test_the_preview_puts_the_parts_together(
        self, client: TestClient, context: ApiContext
    ) -> None:
        job = _job(context, real=True)
        client.put(f"/api/jobs/{job}/scenes/1/layout", json={"kind": "split", "split": 0.5})
        response = client.get(f"/api/jobs/{job}/scenes/1/thumbnail")
        assert response.status_code == 200
        still = Image.open(BytesIO(response.content)).convert("RGB")
        width, height = still.size
        # The plan is landscape: the picture on the left, the speaker right.
        red, green, blue = still.getpixel((width // 4, height // 2))
        assert red > 150 and green < 90 and blue < 90
        assert still.getpixel((3 * width // 4, height // 2)) != (red, green, blue)


class TestYourOwnClip:
    def test_a_clip_of_your_own_plays_in_the_scene(
        self, client: TestClient, context: ApiContext, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        from voxframe.models.asset import AssetKind

        job = _job(context)
        clip = _video(tmp_path / "flood.mp4")
        with clip.open("rb") as handle:
            response = client.post(
                f"/api/jobs/{job}/scenes/1/image/own", files={"file": ("flood.mp4", handle)}
            )
        assert response.status_code == 200
        asset = _saved(context, job).scenes[1].asset
        assert asset is not None and asset.kind is AssetKind.VIDEO
        assert asset.duration is not None and 3.5 < asset.duration < 4.5
        assert asset.license_source == "Your own video"

    def test_a_file_that_is_not_a_video_is_refused(
        self, client: TestClient, context: ApiContext
    ) -> None:
        job = _job(context)
        response = client.post(
            f"/api/jobs/{job}/scenes/1/image/own", files={"file": ("flood.mp4", b"not a video")}
        )
        assert response.status_code == 415
        assert not list((context.store.job_directory(job) / "own").glob("*"))
