"""Saved join choices preserve the plan's original clock and editing history."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.unit import test_transitions as fixtures
from voxframe.config.style import TransitionKind
from voxframe.config.transitions import TRANSITION_PRESETS, TransitionTreatment
from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.transition_studio import set_transition
from voxframe.render.compose.transitions import (
    has_saved_transitions,
    resolved_transitions,
    xfade_name,
)


@pytest.fixture
def plan() -> ScenePlan:
    return fixtures._plan((fixtures._scene(0, 0, 90, first_word=.2, last_word=2.5),
        fixtures._scene(1, 90, 180, first_word=3.2, last_word=5.5),
        fixtures._scene(2, 180, 240, first_word=6.2, last_word=7.5)))


@pytest.mark.parametrize("kind", list(TransitionKind))
def test_every_kind_survives_save_without_changing_timing(plan: ScenePlan, tmp_path: Path, kind: TransitionKind) -> None:
    chosen = TransitionTreatment(kind=kind, seconds=.4)
    edited = set_transition(plan, 0, chosen)
    edited.save(tmp_path / "plan.json")
    restored = ScenePlan.load(tmp_path / "plan.json")
    assert restored.scenes[0].transition_after == chosen
    assert restored.total_frames == plan.total_frames
    assert [s.words for s in restored.scenes] == [s.words for s in plan.scenes]
    assert resolved_transitions(restored)[0].kind == kind
    assert resolved_transitions(restored)[1].kind is TransitionKind.CUT  # pause stays automatic


def test_deliberate_choice_overrides_pause_and_cut_template(plan: ScenePlan) -> None:
    assert not has_saved_transitions(plan)
    chosen = set_transition(plan, 0, TRANSITION_PRESETS["zoom"])
    assert has_saved_transitions(chosen)
    assert resolved_transitions(chosen)[0].kind is TransitionKind.ZOOM


def test_global_choice_clears_scene_overrides_and_reset_restores_template(plan: ScenePlan) -> None:
    chosen = set_transition(plan, 1, TRANSITION_PRESETS["slide"])
    global_plan = set_transition(chosen, 0, TRANSITION_PRESETS["cinematic"], all_joins=True)
    assert all(s.transition_after is None for s in global_plan.scenes)
    assert all(t.kind is TransitionKind.DIP_TO_BLACK for t in resolved_transitions(global_plan))
    reset = set_transition(global_plan, 0, None, all_joins=True)
    assert not has_saved_transitions(reset)
    assert resolved_transitions(reset) == resolved_transitions(plan)


def test_last_scene_has_no_join(plan: ScenePlan) -> None:
    with pytest.raises(EditError):
        set_transition(plan, 2, TransitionTreatment())


def test_short_join_clamps_duration_and_tiny_join_cuts(plan: ScenePlan) -> None:
    chosen = set_transition(plan, 1, TransitionTreatment(seconds=1.5))
    assert resolved_transitions(chosen)[1].frames == 15
    tiny = chosen.model_copy(update={"scenes": (chosen.scenes[0],
        chosen.scenes[1].model_copy(update={"end_frame": 94}), chosen.scenes[2])})
    assert resolved_transitions(tiny)[1].kind is TransitionKind.CUT


@pytest.mark.parametrize("payload", [{"seconds": -1}, {"seconds": 5}, {"direction": "inject;"}, {"kind": "unknown"}])
def test_invalid_choices_are_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        TransitionTreatment(**payload)


@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
def test_direction_maps_to_a_native_effect(plan: ScenePlan, direction: str) -> None:
    chosen = set_transition(plan, 0, TransitionTreatment(kind="slide", direction=direction))
    assert xfade_name(resolved_transitions(chosen)[0]) == "cover" + direction
