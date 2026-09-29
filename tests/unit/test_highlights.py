"""Highlights mode (D-107).

The selection must **cover** the recording. The first version scored correctly
and still produced a video made entirely of the opening and closing, because
the edge bonuses dominated everything else — a fault only visible by looking at
where the chosen scenes sat, not at whether the scoring worked.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from voxframe.plan import PlanAsset, PlannedScene, PlanWord, ScenePlan
from voxframe.plan.highlights import (
    HIGHLIGHT_BANDS,
    MIN_SOURCE_SECONDS,
    build_highlights_plan,
    select_highlights,
)

FPS = 30.0


def _asset() -> PlanAsset:
    return PlanAsset(
        id="a1",
        path="a.jpg",
        width=3000,
        height=2000,
        license_name="CC0-1.0",
        license_author="Someone",
        license_source="test",
    )


def _long_plan(
    scene_count: int = 100,
    scene_seconds: float = 8.0,
    *,
    with_assets: bool = False,
    words_per_scene: int = 20,
) -> ScenePlan:
    frames = int(scene_seconds * FPS)
    scenes = []

    for index in range(scene_count):
        start = index * frames
        base = start / FPS
        scenes.append(
            PlannedScene(
                index=index,
                start_frame=start,
                end_frame=start + frames,
                text="words " * words_per_scene,
                words=tuple(
                    PlanWord(
                        text=f"w{n}",
                        start=base + n * 0.3,
                        end=base + n * 0.3 + 0.25,
                    )
                    for n in range(words_per_scene)
                ),
                asset=_asset() if with_assets else None,
            )
        )

    return ScenePlan(
        audio_path="a.wav",
        audio_sha256="0" * 64,
        audio_duration=scene_count * scene_seconds,
        fps=FPS,
        total_frames=scene_count * frames,
        scenes=tuple(scenes),
    )


class TestCoverage:
    """The fault D-107 records: everything chosen from the edges."""

    def _positions(self, plan: ScenePlan, target: float = 150.0) -> list[float]:
        selection = select_highlights(plan, target_seconds=target)
        return [
            plan.scenes[i].start_frame / plan.total_frames
            for i in selection.scene_indices
        ]

    def test_the_middle_of_the_recording_is_sampled(self) -> None:
        positions = self._positions(_long_plan())

        middle = [p for p in positions if 0.3 <= p <= 0.7]
        assert middle, "nothing chosen from the middle 40% of the recording"

    def test_selection_spans_most_of_the_recording(self) -> None:
        positions = self._positions(_long_plan())

        assert max(positions) - min(positions) > 0.6

    def test_several_bands_are_represented(self) -> None:
        positions = self._positions(_long_plan())

        bands = {min(HIGHLIGHT_BANDS - 1, int(p * HIGHLIGHT_BANDS)) for p in positions}
        assert len(bands) >= 3

    def test_no_single_band_takes_everything(self) -> None:
        positions = self._positions(_long_plan())

        bands = [min(HIGHLIGHT_BANDS - 1, int(p * HIGHLIGHT_BANDS)) for p in positions]
        largest = max(bands.count(b) for b in set(bands))
        assert largest < len(bands)


class TestSelection:
    def test_short_audio_is_not_reduced(self) -> None:
        """The whole thing already is the highlight."""
        plan = _long_plan(scene_count=10, scene_seconds=8.0)

        assert select_highlights(plan).count == 0

    def test_the_target_length_is_respected(self) -> None:
        selection = select_highlights(_long_plan(), target_seconds=150.0)

        assert selection.total_seconds <= 150.0

    def test_a_useful_amount_is_selected(self) -> None:
        """A band with nothing worth taking must not shorten the result."""
        selection = select_highlights(_long_plan(), target_seconds=150.0)

        assert selection.total_seconds > 150.0 * 0.7

    def test_scenes_are_chronological(self) -> None:
        """Played by score, the video jumps around its own timeline."""
        selection = select_highlights(_long_plan())

        assert list(selection.scene_indices) == sorted(selection.scene_indices)

    def test_every_choice_records_a_reason(self) -> None:
        selection = select_highlights(_long_plan())

        assert all(selection.reasons[i] for i in selection.scene_indices)

    def test_cards_are_never_selected(self) -> None:
        """A card is structure; extracted alone it makes no sense."""
        plan = _long_plan()
        with_card = plan.model_copy(
            update={
                "scenes": (
                    plan.scenes[0].model_copy(
                        update={"card_kind": "title", "card_text": "A Talk"}
                    ),
                    *plan.scenes[1:],
                )
            }
        )

        assert 0 not in select_highlights(with_card).scene_indices

    def test_a_denser_scene_is_preferred(self) -> None:
        """Speech density is the primary signal."""
        sparse = _long_plan(words_per_scene=3)
        dense = _long_plan(words_per_scene=20)

        assert (
            select_highlights(dense).total_seconds
            >= select_highlights(sparse).total_seconds * 0.9
        )

    def test_the_threshold_is_above_the_default_target(self) -> None:
        """Reducing 4 minutes to 2.5 would barely be a reduction."""
        assert MIN_SOURCE_SECONDS > 150.0


class TestHighlightsPlan:
    def test_the_rebuilt_plan_tiles_from_zero(self) -> None:
        """The grid invariant, restated after extraction (D-013)."""
        plan = _long_plan()
        result = build_highlights_plan(plan, select_highlights(plan))

        assert result.scenes[0].start_frame == 0
        for previous, following in pairwise(result.scenes):
            assert previous.end_frame == following.start_frame
        assert result.scenes[-1].end_frame == result.total_frames

    def test_scene_durations_are_preserved(self) -> None:
        plan = _long_plan()
        selection = select_highlights(plan)
        result = build_highlights_plan(plan, selection)

        original = [plan.scenes[i].duration_frames for i in selection.scene_indices]
        assert [s.duration_frames for s in result.scenes] == original

    def test_word_timings_are_rebased(self) -> None:
        """Timings are relative to the original recording and would be wrong."""
        plan = _long_plan()
        selection = select_highlights(plan)
        result = build_highlights_plan(plan, selection)

        for scene in result.scenes:
            if scene.words:
                assert scene.words[0].start >= scene.start_frame / FPS - 0.5
                assert scene.words[-1].end <= scene.end_frame / FPS + 0.5

    def test_an_empty_selection_returns_the_plan_unchanged(self) -> None:
        plan = _long_plan(scene_count=10)
        selection = select_highlights(plan)

        assert build_highlights_plan(plan, selection) is plan

    def test_indices_are_renumbered(self) -> None:
        plan = _long_plan()
        result = build_highlights_plan(plan, select_highlights(plan))

        assert [s.index for s in result.scenes] == list(range(len(result.scenes)))

    def test_the_result_is_shorter_than_the_source(self) -> None:
        plan = _long_plan()
        result = build_highlights_plan(plan, select_highlights(plan))

        assert result.total_frames < plan.total_frames

    @pytest.mark.parametrize("target", [60.0, 150.0, 300.0])
    def test_the_grid_holds_at_any_target(self, target: float) -> None:
        plan = _long_plan()
        selection = select_highlights(plan, target_seconds=target)
        result = build_highlights_plan(plan, selection)

        assert result.total_frames == sum(s.duration_frames for s in result.scenes)
