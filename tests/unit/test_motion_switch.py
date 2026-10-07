"""Turning a scene's camera movement on or off (D-154)."""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.models.asset import AssetKind
from voxframe.plan.editing import MOTION_OFF, EditError, choose_image, set_motion
from voxframe.plan.scene_plan import MotionKind, PlanAsset, PlannedScene, ScenePlan


def _asset(asset_id: str, kind: AssetKind = AssetKind.IMAGE) -> PlanAsset:
    return PlanAsset(
        id=asset_id, path=f"{asset_id}.jpg", width=1920, height=1080, kind=kind,
        duration=4.0 if kind is AssetKind.VIDEO else None,
        license_name="CC0", license_author="A", license_source="Test",
    )


def _plan(*scenes: PlannedScene) -> ScenePlan:
    return ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64, audio_duration=6.0,
        fps=30.0, total_frames=scenes[-1].end_frame, scenes=scenes,
    )


def _still(index: int = 0, source: str = "") -> PlannedScene:
    return PlannedScene(
        index=index, start_frame=index * 90, end_frame=(index + 1) * 90,
        text="a river", asset=_asset("river"),
        alternatives=(_asset("lake"),), asset_source=source,
    )


def test_off_holds_the_image_still() -> None:
    scene = set_motion(_plan(_still()), 0, on=False).scenes[0]

    assert scene.motion is MotionKind.NONE
    assert scene.motion_reason == MOTION_OFF


def test_on_is_the_usual_ken_burns() -> None:
    off = set_motion(_plan(_still()), 0, on=False)

    assert set_motion(off, 0, on=True).scenes[0].motion is MotionKind.KEN_BURNS


def test_off_survives_choosing_another_image() -> None:
    """It is a choice about the scene, not about the old image."""
    off = set_motion(_plan(_still()), 0, on=False)

    assert choose_image(off, 0, "lake").scenes[0].motion is MotionKind.NONE


def test_an_atmospheric_scene_stays_labelled() -> None:
    """The image did not change, so neither does where it came from (D-137)."""
    scene = set_motion(_plan(_still(source="atmospheric")), 0, on=False).scenes[0]

    assert scene.asset_source == "atmospheric"


def test_a_clip_moves_by_itself() -> None:
    clip = PlannedScene(
        index=0, start_frame=0, end_frame=90, text="waves",
        asset=_asset("waves", AssetKind.VIDEO), motion=MotionKind.NONE,
    )

    with pytest.raises(EditError, match="clip"):
        set_motion(_plan(clip), 0, on=True)


def test_a_plain_background_does_not_move() -> None:
    plain = PlannedScene(index=0, start_frame=0, end_frame=90, text="x", motion=MotionKind.NONE)

    with pytest.raises(EditError, match="plain background"):
        set_motion(_plan(plain), 0, on=True)


# --- the route --------------------------------------------------------------------

pytest.importorskip("fastapi", reason="web extra not installed")

from fastapi.testclient import TestClient  # noqa: E402

from voxframe.api.app import ApiContext, create_app  # noqa: E402
from voxframe.api.security import SessionToken  # noqa: E402
from voxframe.config.settings import Settings  # noqa: E402
from voxframe.jobs.store import JobStore  # noqa: E402


def test_the_route_saves_it_as_a_pending_edit(tmp_path: Path) -> None:
    root = tmp_path / "web"
    context = ApiContext(
        settings=Settings(
            library_path=tmp_path / "library", cache_path=tmp_path / "cache",
            output_path=tmp_path / "out",
        ),
        store=JobStore(root, reap_interval=None),
        token=SessionToken("t"),
        allowed_paths=(root.resolve(),),
    )
    job = context.store.create(audio_name="a.wav", options={})
    plan_path = context.store.job_directory(job.id) / "source.plan.json"
    _plan(_still()).save(plan_path)
    context.store.submit(job, lambda _job: None)
    assert job.future is not None
    job.future.result(timeout=60)
    context.store.record_result(job, artifacts={"plan": plan_path}, warnings=(), summary={})
    client = TestClient(create_app(context), base_url="http://127.0.0.1:8765")
    client.headers.update({"x-voxframe-token": "t"})

    response = client.put(f"/api/jobs/{job.id}/scenes/0/motion", json={"on": False})

    assert response.status_code == 200
    assert response.json()["scene"]["motion"] == "none"
    assert response.json()["pending_edits"] == 1
    assert ScenePlan.load(plan_path).scenes[0].motion is MotionKind.NONE
