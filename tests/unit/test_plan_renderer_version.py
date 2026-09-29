"""Plans record the renderer that made them (D-147).

Plans from before the first release are unsupported: their word timings may
sit on the recording's clock rather than the video's (D-144). Recording the
renderer's version in every plan means a future format change can be detected
and warned about instead of misread.
"""

from __future__ import annotations

import json
from pathlib import Path

from voxframe.jobs.pipeline import PRE_RELEASE_PLAN_WARNING, _plan_age_warnings
from voxframe.plan.scene_plan import MotionKind, PlannedScene, ScenePlan
from voxframe.render.version import RENDERER_VERSION


def _plan() -> ScenePlan:
    return ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64, audio_duration=3.0,
        fps=30.0, total_frames=90,
        scenes=(
            PlannedScene(
                index=0, start_frame=0, end_frame=90, text="hello",
                motion=MotionKind.NONE,
            ),
        ),
    )


def test_a_new_plan_records_the_current_renderer(tmp_path: Path) -> None:
    path = _plan().save(tmp_path / "p.plan.json")

    assert json.loads(path.read_text(encoding="utf-8"))["renderer_version"] == RENDERER_VERSION
    assert not ScenePlan.load(path).is_pre_release


def test_a_plan_without_one_is_pre_release(tmp_path: Path) -> None:
    path = _plan().save(tmp_path / "p.plan.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["renderer_version"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    plan = ScenePlan.load(path)

    assert plan.is_pre_release
    assert _plan_age_warnings(plan) == [PRE_RELEASE_PLAN_WARNING]


def test_editing_keeps_the_renderer_that_made_it(tmp_path: Path) -> None:
    """An edit does not make an old plan's timings new."""
    from voxframe.plan.editing import correct_caption

    old = _plan().model_copy(update={"renderer_version": 1})

    assert correct_caption(old, 0, "hello there").renderer_version == 1


def test_a_current_plan_gets_no_warning() -> None:
    assert _plan_age_warnings(_plan()) == []
