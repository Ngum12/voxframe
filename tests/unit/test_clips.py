"""Video clips must fit the frame grid without looping (D-085).

The strategy tests matter most: a clip shorter than its scene has to be covered
somehow, and a visible loop is the one option that always reads as a fault.
"""

from __future__ import annotations

import pytest

from voxframe.config.settings import MediaMix, Settings
from voxframe.render.compose.clips import (
    MAX_SLOWDOWN,
    clip_filter_chain,
    plan_short_clip,
)
from voxframe.sourcing.registry import _scenes_wanting_clips


class TestShortClipStrategy:
    def test_a_long_clip_is_trimmed(self) -> None:
        strategy = plan_short_clip(clip_seconds=12.0, scene_seconds=5.0)

        assert strategy.kind == "trim"
        assert strategy.speed == 1.0
        assert strategy.held_seconds == 0.0

    def test_a_clip_that_just_covers_the_scene_is_trimmed(self) -> None:
        """A 2% shortfall is not worth re-timing for."""
        strategy = plan_short_clip(clip_seconds=4.95, scene_seconds=5.0)
        assert strategy.kind == "trim"

    def test_a_slightly_short_clip_is_slowed(self) -> None:
        strategy = plan_short_clip(clip_seconds=3.0, scene_seconds=5.0)

        assert strategy.kind == "slow"
        assert strategy.speed == pytest.approx(5.0 / 3.0)
        assert strategy.held_seconds == 0.0

    def test_slowing_exactly_covers_the_scene(self) -> None:
        """clip * speed must equal the scene, or the segment is short."""
        clip, scene = 4.0, 8.0
        strategy = plan_short_clip(clip, scene)

        assert clip * strategy.speed == pytest.approx(scene)

    def test_a_very_short_clip_is_slowed_then_held(self) -> None:
        strategy = plan_short_clip(clip_seconds=1.5, scene_seconds=8.0)

        assert strategy.kind == "slow_and_hold"
        assert strategy.speed == MAX_SLOWDOWN

    def test_slow_and_hold_covers_the_scene_exactly(self) -> None:
        clip, scene = 1.5, 8.0
        strategy = plan_short_clip(clip, scene)

        covered = clip * strategy.speed + strategy.held_seconds
        assert covered == pytest.approx(scene)

    def test_slowdown_never_exceeds_the_limit(self) -> None:
        """Past 2.5x people move at visibly impossible speeds."""
        for clip in (0.5, 1.0, 2.0, 3.0):
            assert plan_short_clip(clip, 30.0).speed <= MAX_SLOWDOWN

    def test_no_strategy_ever_loops(self) -> None:
        """The property the whole design exists to guarantee.

        Asserted on the filter chain rather than the strategy name: a loop
        would appear as a `loop` filter or a concatenated input, and checking
        the chain is what actually rules that out.
        """
        for clip in (0.5, 1.5, 3.0, 7.0, 12.0, 45.0):
            strategy = plan_short_clip(clip, 8.0)
            assert strategy.kind in {"trim", "slow", "slow_and_hold"}

            chain = clip_filter_chain(1280, 720, 30.0, strategy)
            assert "loop" not in chain
            assert "concat" not in chain

    def test_unknown_duration_falls_back_to_trim(self) -> None:
        """A clip whose length the plan does not record must still render."""
        assert plan_short_clip(0.0, 5.0).kind == "trim"

    def test_every_strategy_records_a_reason(self) -> None:
        for clip in (1.0, 4.0, 20.0):
            assert plan_short_clip(clip, 6.0).reason


class TestClipFilterChain:
    def _chain(self, clip: float, scene: float) -> str:
        return clip_filter_chain(1280, 720, 30.0, plan_short_clip(clip, scene))

    def test_scale_then_crop_rather_than_squash(self) -> None:
        """scale=w:h alone would distort a clip of a different aspect."""
        chain = self._chain(12.0, 5.0)

        assert "force_original_aspect_ratio=increase" in chain
        assert "crop=1280:720" in chain

    def test_frame_rate_is_normalised(self) -> None:
        """A 24fps clip in a 30fps render must be resampled to the grid."""
        assert "fps=30.0" in self._chain(12.0, 5.0)

    def test_square_pixels_are_forced(self) -> None:
        assert "setsar=1" in self._chain(12.0, 5.0)

    def test_a_trimmed_clip_rebases_without_changing_speed(self) -> None:
        assert "setpts=PTS-STARTPTS" in self._chain(12.0, 5.0)

    def test_a_slowed_clip_has_setpts(self) -> None:
        assert "setpts" in self._chain(3.0, 5.0)

    def test_holding_clones_the_last_frame(self) -> None:
        """stop_mode=clone freezes; padding with black reads as a dropout."""
        chain = self._chain(1.5, 8.0)

        assert "tpad=stop_mode=clone" in chain

    def test_filters_are_ordered_retime_before_scale(self) -> None:
        """Re-timing then scaling; the reverse scales frames it then discards."""
        chain = self._chain(1.5, 8.0)

        assert chain.index("setpts") < chain.index("scale=")
        assert chain.index("tpad") < chain.index("scale=")


class TestMediaMix:
    def test_stills_only_requests_no_clips(self) -> None:
        settings = Settings(media_mix=MediaMix.STILLS)
        assert _scenes_wanting_clips(list(range(10)), settings) == set()

    def test_clips_only_requests_every_scene(self) -> None:
        settings = Settings(media_mix=MediaMix.CLIPS)
        assert _scenes_wanting_clips(list(range(5)), settings) == {0, 1, 2, 3, 4}

    def test_mixed_respects_the_ratio(self) -> None:
        settings = Settings(media_mix=MediaMix.MIXED, clip_ratio=0.4)
        chosen = _scenes_wanting_clips(list(range(10)), settings)

        assert len(chosen) == 4

    def test_clips_are_spread_not_clustered(self) -> None:
        """Clustering every clip at the front changes the video's character
        halfway through."""
        settings = Settings(media_mix=MediaMix.MIXED, clip_ratio=0.4)
        chosen = sorted(_scenes_wanting_clips(list(range(10)), settings))

        assert chosen != [0, 1, 2, 3]
        assert max(chosen) >= 5

    def test_no_scenes_means_no_clips(self) -> None:
        settings = Settings(media_mix=MediaMix.MIXED)
        assert _scenes_wanting_clips([], settings) == set()

    def test_a_zero_ratio_requests_no_clips(self) -> None:
        settings = Settings(media_mix=MediaMix.MIXED, clip_ratio=0.0)
        assert _scenes_wanting_clips(list(range(10)), settings) == set()

    def test_chosen_scenes_are_real_indices(self) -> None:
        """Targets are scene indices, which need not be 0..n."""
        settings = Settings(media_mix=MediaMix.MIXED, clip_ratio=0.5)
        targets = [3, 7, 11, 15]
        chosen = _scenes_wanting_clips(targets, settings)

        assert chosen <= set(targets)


class TestDownloadCaps:
    def test_defaults_are_set(self) -> None:
        """max_clip_mb is profile-derived, so read it through the accessor."""
        settings = Settings()

        assert settings.resolved_max_clip_mb > 0
        assert settings.max_download_mb > settings.resolved_max_clip_mb

    def test_caps_are_configurable(self) -> None:
        settings = Settings(max_clip_mb=10, max_download_mb=100)

        assert settings.resolved_max_clip_mb == 10
        assert settings.max_download_mb == 100
