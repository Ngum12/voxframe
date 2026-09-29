"""Transitions must not disturb the frame grid (D-097).

The arithmetic tests matter most. A crossfade overlaps its inputs, so a chain
of them silently shortens the video — and a shorter video means every caption
after the first transition drifts against the audio. That is the failure D-025
exists to catch, and it is invisible until someone watches to the end.
"""

from __future__ import annotations

import pytest

from voxframe.config.style import TransitionKind
from voxframe.plan import PlanAsset, PlannedScene, PlanWord, ScenePlan
from voxframe.render.compose.transitions import (
    MAX_SCENE_FRACTION,
    MIN_TRANSITION_FRAMES,
    TransitionPlan,
    crossfade_filter,
    padded_durations,
    plan_transitions,
    total_frames_after,
)


def _asset(identifier: str) -> PlanAsset:
    return PlanAsset(
        id=identifier,
        path=f"{identifier}.jpg",
        width=3000,
        height=2000,
        license_name="CC0-1.0",
        license_author="Someone",
        license_source="test",
    )


def _scene(
    index: int,
    start: int,
    end: int,
    *,
    asset: str | None = None,
    first_word: float | None = None,
    last_word: float | None = None,
) -> PlannedScene:
    words: tuple[PlanWord, ...] = ()
    if first_word is not None and last_word is not None:
        words = (
            PlanWord(text="a", start=first_word, end=first_word + 0.2),
            PlanWord(text="b", start=last_word - 0.2, end=last_word),
        )

    return PlannedScene(
        index=index,
        start_frame=start,
        end_frame=end,
        text="some words here",
        words=words,
        asset=_asset(asset) if asset else None,
    )


def _plan(scenes: tuple[PlannedScene, ...], fps: float = 30.0) -> ScenePlan:
    return ScenePlan(
        audio_path="a.wav",
        audio_sha256="0" * 64,
        audio_duration=scenes[-1].end_frame / fps,
        fps=fps,
        total_frames=scenes[-1].end_frame,
        scenes=scenes,
    )


class TestFrameGrid:
    """The property everything else is subordinate to."""

    def test_padding_restores_the_lost_frames(self) -> None:
        scene_frames = [150, 200, 180]
        transitions = [
            TransitionPlan(0, TransitionKind.CROSSFADE, 12),
            TransitionPlan(1, TransitionKind.CROSSFADE, 12),
        ]

        padded = padded_durations(scene_frames, transitions)

        assert total_frames_after(padded, transitions) == sum(scene_frames)

    def test_without_padding_the_video_is_short(self) -> None:
        """The bug this exists to prevent, asserted so it cannot return."""
        scene_frames = [150, 200, 180]
        transitions = [
            TransitionPlan(0, TransitionKind.CROSSFADE, 12),
            TransitionPlan(1, TransitionKind.CROSSFADE, 12),
        ]

        assert total_frames_after(scene_frames, transitions) < sum(scene_frames)

    def test_only_outgoing_segments_are_padded(self) -> None:
        """The last scene gives nothing away and must not grow."""
        padded = padded_durations(
            [100, 100, 100], [TransitionPlan(0, TransitionKind.CROSSFADE, 10)]
        )

        assert padded == [110, 100, 100]

    def test_cuts_add_no_padding(self) -> None:
        transitions = [TransitionPlan(0, TransitionKind.CUT, 0)]

        assert padded_durations([100, 100], transitions) == [100, 100]

    @pytest.mark.parametrize("blend", [4, 8, 12, 20])
    def test_the_grid_holds_at_any_blend_length(self, blend: int) -> None:
        scene_frames = [200, 200, 200, 200]
        transitions = [
            TransitionPlan(i, TransitionKind.CROSSFADE, blend) for i in range(3)
        ]

        padded = padded_durations(scene_frames, transitions)
        assert total_frames_after(padded, transitions) == sum(scene_frames)


class TestPlacement:
    def test_a_pause_becomes_a_cut(self) -> None:
        """The silence already marks the break."""
        plan = _plan(
            (
                _scene(0, 0, 150, asset="a", first_word=0.0, last_word=4.0),
                _scene(1, 150, 300, asset="b", first_word=5.0, last_word=9.0),
            )
        )

        transitions = plan_transitions(plan)

        assert transitions[0].kind is TransitionKind.CUT
        assert "paused" in transitions[0].reason

    def test_continuous_speech_becomes_a_crossfade(self) -> None:
        plan = _plan(
            (
                _scene(0, 0, 150, asset="a", first_word=0.0, last_word=4.9),
                _scene(1, 150, 300, asset="b", first_word=5.0, last_word=9.0),
            )
        )

        transitions = plan_transitions(plan)

        assert transitions[0].kind is TransitionKind.CROSSFADE
        assert transitions[0].frames >= MIN_TRANSITION_FRAMES

    def test_the_same_asset_either_side_becomes_a_cut(self) -> None:
        """Crossfading an image into itself looks like a stall."""
        plan = _plan(
            (
                _scene(0, 0, 150, asset="same", first_word=0.0, last_word=4.9),
                _scene(1, 150, 300, asset="same", first_word=5.0, last_word=9.0),
            )
        )

        assert plan_transitions(plan)[0].kind is TransitionKind.CUT
        assert "same asset" in plan_transitions(plan)[0].reason

    def test_a_short_scene_becomes_a_cut(self) -> None:
        """A blend may not eat more than a quarter of the shorter scene."""
        plan = _plan(
            (
                _scene(0, 0, 12, asset="a", first_word=0.0, last_word=0.3),
                _scene(1, 12, 160, asset="b", first_word=0.4, last_word=5.0),
            )
        )

        assert plan_transitions(plan)[0].kind is TransitionKind.CUT

    def test_a_blend_never_exceeds_the_scene_fraction(self) -> None:
        plan = _plan(
            (
                _scene(0, 0, 60, asset="a", first_word=0.0, last_word=1.9),
                _scene(1, 60, 400, asset="b", first_word=2.0, last_word=12.0),
            )
        )

        transition = plan_transitions(plan)[0]

        if transition.is_blend:
            assert transition.frames <= 60 * MAX_SCENE_FRACTION

    def test_disabling_gives_every_boundary_a_cut(self) -> None:
        plan = _plan(
            (
                _scene(0, 0, 150, asset="a", first_word=0.0, last_word=4.9),
                _scene(1, 150, 300, asset="b", first_word=5.0, last_word=9.0),
            )
        )

        transitions = plan_transitions(plan, enabled=False)

        assert all(t.kind is TransitionKind.CUT for t in transitions)

    def test_one_scene_has_no_boundaries(self) -> None:
        plan = _plan((_scene(0, 0, 150, asset="a"),))

        assert plan_transitions(plan) == []

    def test_there_is_one_transition_per_boundary(self) -> None:
        plan = _plan(
            tuple(
                _scene(i, i * 150, (i + 1) * 150, asset=f"a{i}")
                for i in range(5)
            )
        )

        assert len(plan_transitions(plan)) == 4

    def test_every_transition_records_a_reason(self) -> None:
        plan = _plan(
            tuple(
                _scene(i, i * 150, (i + 1) * 150, asset=f"a{i}")
                for i in range(4)
            )
        )

        assert all(t.reason for t in plan_transitions(plan))


class TestFilterChain:
    """xfade is particular about its inputs (D-097)."""

    def _chain(self, blends: int = 2) -> str:
        from pathlib import Path

        segments = [Path(f"s{i}.mp4") for i in range(blends + 1)]
        transitions = [
            TransitionPlan(i, TransitionKind.CROSSFADE, 12) for i in range(blends)
        ]
        durations = [150] * len(segments)

        chain, _ = crossfade_filter(segments, transitions, 30.0, durations)
        return chain

    def test_every_input_is_normalised_to_square_pixels(self) -> None:
        """concat refuses inputs whose sample aspect ratio differs (D-125).

        A Ken Burns scale of one real photograph produced SAR 1843200:1843417,
        a hair off square, and the whole render failed at the final join. Every
        input passes through here, so every input must be normalised here.
        """
        chain = self._chain(blends=3)
        normalisers = [part for part in chain.split(";") if part.endswith(tuple(
            f"[n{index}]" for index in range(4)
        ))]

        assert len(normalisers) == 4
        assert all("setsar=1" in part for part in normalisers)

    def test_inputs_are_normalised_to_a_common_timebase(self) -> None:
        """Mismatched timebases fail with an unhelpful -22 Invalid argument."""
        chain = self._chain()

        assert "settb=AVTB" in chain
        assert "setpts=PTS-STARTPTS" in chain

    def test_offsets_accumulate_across_blends(self) -> None:
        """Computing each offset from a nominal start causes drift."""
        chain = self._chain(blends=2)

        offsets = [
            float(part.split("offset=")[1].split(",")[0].split("[")[0])
            for part in chain.split(";")
            if "offset=" in part
        ]

        assert len(offsets) == 2
        assert offsets[1] > offsets[0]

    def test_a_single_segment_needs_no_chain(self) -> None:
        from pathlib import Path

        chain, label = crossfade_filter([Path("a.mp4")], [], 30.0, [100])

        assert chain == ""
        assert label == "0:v"

    def test_cuts_use_concat_not_xfade(self) -> None:
        from pathlib import Path

        chain, _ = crossfade_filter(
            [Path("a.mp4"), Path("b.mp4")],
            [TransitionPlan(0, TransitionKind.CUT, 0)],
            30.0,
            [100, 100],
        )

        assert "concat" in chain
        assert "xfade" not in chain


class TestSquarePixels:
    """Still segments must come out with square pixels (D-125)."""

    @pytest.mark.parametrize(
        ("width", "height"),
        [(1280, 720), (1920, 1080), (1080, 1920), (1080, 1080), (854, 480)],
    )
    def test_the_ken_burns_chain_ends_square(self, width: int, height: int) -> None:
        from voxframe.render.motion.ken_burns import plan_move, zoompan_filter

        move = plan_move(3, "some-asset", 240)

        chain = zoompan_filter(move, width, height, 30.0)

        # Last, so nothing after it can reintroduce a non-square SAR.
        assert chain.rstrip().endswith("setsar=1")
