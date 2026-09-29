"""Tests for the scene plan.

The plan is the only input to rendering, so its invariants matter more than
most. In particular it must reject a hand-edited file whose scenes no longer
tile the timeline, with a message that says where the problem is.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from voxframe.config.settings import AspectRatio
from voxframe.plan import (
    PLAN_VERSION,
    MotionKind,
    PlanAsset,
    PlanError,
    PlannedScene,
    ScenePlan,
)


def _asset(asset_id: str = "a1") -> PlanAsset:
    return PlanAsset(
        id=asset_id,
        path=f"img/{asset_id}.jpg",
        width=1920,
        height=1080,
        license_name="CC0-1.0",
        license_author="Someone",
        license_source="local",
    )


def _plan(scenes: tuple[PlannedScene, ...], total_frames: int) -> ScenePlan:
    return ScenePlan(
        audio_path="audio.wav",
        audio_sha256="a" * 64,
        audio_duration=total_frames / 30.0,
        fps=30.0,
        total_frames=total_frames,
        scenes=scenes,
    )


class TestPlanInvariants:
    def test_valid_plan_accepted(self) -> None:
        plan = _plan(
            (
                PlannedScene(index=0, start_frame=0, end_frame=90),
                PlannedScene(index=1, start_frame=90, end_frame=180),
            ),
            180,
        )
        assert len(plan.scenes) == 2

    def test_gap_rejected(self) -> None:
        """A hand-edited plan with a gap would render a black hole."""
        with pytest.raises((PlanError, ValidationError), match="gap or overlap"):
            _plan(
                (
                    PlannedScene(index=0, start_frame=0, end_frame=90),
                    PlannedScene(index=1, start_frame=100, end_frame=180),
                ),
                180,
            )

    def test_overlap_rejected(self) -> None:
        with pytest.raises((PlanError, ValidationError), match="gap or overlap"):
            _plan(
                (
                    PlannedScene(index=0, start_frame=0, end_frame=100),
                    PlannedScene(index=1, start_frame=90, end_frame=180),
                ),
                180,
            )

    def test_must_start_at_zero(self) -> None:
        with pytest.raises((PlanError, ValidationError), match="must start at frame 0"):
            _plan((PlannedScene(index=0, start_frame=10, end_frame=180),), 180)

    def test_must_end_at_total_frames(self) -> None:
        """The plan must cover the whole audio (D-013, D-032)."""
        with pytest.raises((PlanError, ValidationError), match="must end at frame 180"):
            _plan((PlannedScene(index=0, start_frame=0, end_frame=170),), 180)

    def test_empty_plan_rejected(self) -> None:
        with pytest.raises((PlanError, ValidationError), match="at least one scene"):
            _plan((), 180)

    def test_zero_length_scene_rejected(self) -> None:
        with pytest.raises((PlanError, ValidationError), match="not after"):
            PlannedScene(index=0, start_frame=90, end_frame=90)


class TestCredits:
    def test_credits_projected_from_scenes(self) -> None:
        """Credits describe what was rendered, not a separate list (D-012)."""
        plan = _plan(
            (
                PlannedScene(index=0, start_frame=0, end_frame=90, asset=_asset("a1")),
                PlannedScene(index=1, start_frame=90, end_frame=180, asset=_asset("a2")),
            ),
            180,
        )
        lines = plan.credits()
        assert len(lines) == 1  # same author and license, deduplicated
        assert "Someone" in lines[0]

    def test_unmatched_scenes_contribute_nothing(self) -> None:
        plan = _plan((PlannedScene(index=0, start_frame=0, end_frame=180),), 180)
        assert plan.credits() == ()

    def test_attribution_names_external_source(self) -> None:
        asset = PlanAsset(
            id="x",
            path="p.jpg",
            width=100,
            height=100,
            license_name="Pexels",
            license_author="Photographer",
            license_source="pexels",
        )
        assert "via pexels" in asset.attribution()


class TestRoundTrip:
    def test_save_and_load(self, tmp_path: Path) -> None:
        plan = _plan(
            (
                PlannedScene(
                    index=0,
                    start_frame=0,
                    end_frame=180,
                    text="Hello world",
                    queries=("hello",),
                    asset=_asset(),
                    motion=MotionKind.KEN_BURNS,
                ),
            ),
            180,
        )
        path = plan.save(tmp_path / "plan.json")
        loaded = ScenePlan.load(path)

        assert loaded.scenes[0].text == "Hello world"
        assert loaded.scenes[0].asset is not None
        assert loaded.scenes[0].asset.id == "a1"

    def test_json_is_human_editable(self, tmp_path: Path) -> None:
        """The plan is meant to be edited by hand, so it must be indented."""
        plan = _plan((PlannedScene(index=0, start_frame=0, end_frame=180),), 180)
        path = plan.save(tmp_path / "plan.json")
        assert "\n  " in path.read_text(encoding="utf-8")

    def test_missing_file_reports_clearly(self, tmp_path: Path) -> None:
        with pytest.raises(PlanError, match="not found"):
            ScenePlan.load(tmp_path / "nope.json")

    def test_malformed_json_names_the_location(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(PlanError, match="line"):
            ScenePlan.load(path)

    def test_version_mismatch_refused(self, tmp_path: Path) -> None:
        """An old plan must be refused, not misread."""
        plan = _plan((PlannedScene(index=0, start_frame=0, end_frame=180),), 180)
        path = plan.save(tmp_path / "plan.json")

        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = PLAN_VERSION + 99
        path.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(PlanError, match="plan version"):
            ScenePlan.load(path)

    def test_edited_plan_with_a_gap_is_caught_on_load(self, tmp_path: Path) -> None:
        """The most likely hand-edit mistake."""
        plan = _plan(
            (
                PlannedScene(index=0, start_frame=0, end_frame=90),
                PlannedScene(index=1, start_frame=90, end_frame=180),
            ),
            180,
        )
        path = plan.save(tmp_path / "plan.json")

        data = json.loads(path.read_text(encoding="utf-8"))
        data["scenes"][1]["start_frame"] = 100
        path.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises((PlanError, ValidationError), match="gap or overlap"):
            ScenePlan.load(path)


class TestReporting:
    def test_counts(self) -> None:
        plan = _plan(
            (
                PlannedScene(index=0, start_frame=0, end_frame=60, asset=_asset("a1")),
                PlannedScene(index=1, start_frame=60, end_frame=120, asset=_asset("a1")),
                PlannedScene(index=2, start_frame=120, end_frame=180),
            ),
            180,
        )
        assert plan.matched_scenes == 2
        assert plan.unique_assets == 1

    def test_seconds_derived_from_frames(self) -> None:
        scene = PlannedScene(index=0, start_frame=30, end_frame=120)
        assert scene.start_seconds(30.0) == pytest.approx(1.0)
        assert scene.duration_seconds(30.0) == pytest.approx(3.0)

    def test_aspect_round_trips(self, tmp_path: Path) -> None:
        plan = ScenePlan(
            audio_path="a.wav",
            audio_sha256="a" * 64,
            audio_duration=6.0,
            fps=30.0,
            total_frames=180,
            aspect=AspectRatio.VERTICAL,
            scenes=(PlannedScene(index=0, start_frame=0, end_frame=180),),
        )
        loaded = ScenePlan.load(plan.save(tmp_path / "p.json"))
        assert loaded.aspect is AspectRatio.VERTICAL
