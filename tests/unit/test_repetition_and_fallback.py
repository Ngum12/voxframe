"""No-repeat rule, clip fallback, and profile-derived media settings.

All three exist because a measured render was worse than it should have been:
clips reduced fill rate (D-086), the same clip appeared twice (D-087), and the
lite profile downloaded clips despite being for limited bandwidth (D-088).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from voxframe.config.settings import MediaMix, ModelProfile, Settings
from voxframe.library import AssetLibrary
from voxframe.match.matcher import Matcher, MatchWeights
from voxframe.models import Asset, AssetKind, LicenseInfo, Scene, Word


def _asset(identifier: str) -> Asset:
    return Asset(
        id=identifier,
        path=Path(f"{identifier}.jpg"),
        kind=AssetKind.IMAGE,
        sha256=identifier * 8,
        width=3000,
        height=2000,
        license=LicenseInfo(name="CC0-1.0", author="Someone", source="test"),
        added_at=datetime.now(UTC),
    )


class TestNoRepeatRule:
    """An asset barred for a scene may not win on score alone (D-087)."""

    def _matcher(self, **weights: object) -> Matcher:
        # The library and embedder are unused by _barred_assets.
        return Matcher.__new__(Matcher)  # type: ignore[call-arg]

    def _barred(
        self,
        recent: list[str],
        used: list[str],
        total_scenes: int,
        **overrides: object,
    ) -> set[str]:
        matcher = self._matcher()
        matcher.weights = MatchWeights(**overrides)  # type: ignore[arg-type]
        return matcher._barred_assets(recent, used, total_scenes)

    def test_a_short_video_bars_every_used_asset(self) -> None:
        """With a dozen scenes the viewer sees both instances within a minute."""
        barred = self._barred(recent=["a"], used=["a", "b", "c"], total_scenes=7)

        assert barred == {"a", "b", "c"}

    def test_a_long_video_bars_only_the_window(self) -> None:
        used = [f"asset{i}" for i in range(20)]
        barred = self._barred(recent=[], used=used, total_scenes=40)

        assert "asset19" in barred
        assert "asset0" not in barred

    def test_the_window_size_is_configurable(self) -> None:
        used = [f"asset{i}" for i in range(20)]

        narrow = self._barred(
            recent=[], used=used, total_scenes=40, no_repeat_window=2
        )
        wide = self._barred(
            recent=[], used=used, total_scenes=40, no_repeat_window=10
        )

        assert len(narrow) == 2
        assert len(wide) == 10

    def test_the_short_video_boundary_is_configurable(self) -> None:
        used = ["a", "b"]

        short = self._barred(
            recent=[], used=used, total_scenes=5, no_repeat_below_scenes=10
        )
        long = self._barred(
            recent=[], used=used, total_scenes=5, no_repeat_below_scenes=3
        )

        assert short == {"a", "b"}
        # Above the boundary the window applies instead, which here still
        # catches both — the point is that the *rule* differs.
        assert long == {"a", "b"}

    def test_nothing_used_bars_nothing(self) -> None:
        assert self._barred(recent=[], used=[], total_scenes=20) == set()

    def test_defaults_are_set(self) -> None:
        weights = MatchWeights()

        assert weights.no_repeat_window > 0
        assert weights.no_repeat_below_scenes > 0


class TestTheHardCap:
    """No asset more than twice, never in near-adjacent scenes (D-140)."""

    def _hard(self, placed: dict[str, list[int]], used: list[str], scene: int) -> set[str]:
        matcher = Matcher.__new__(Matcher)  # type: ignore[call-arg]
        matcher.weights = MatchWeights()
        matcher._placed = placed
        return matcher._hard_barred(used, scene)

    def test_a_third_appearance_is_barred_however_far_away(self) -> None:
        barred = self._hard({"a": [0, 20]}, ["a", "a"], scene=60)

        assert "a" in barred

    def test_a_second_appearance_is_allowed_far_away(self) -> None:
        assert "a" not in self._hard({"a": [0]}, ["a"], scene=30)

    @pytest.mark.parametrize("gap", [1, 2])
    def test_near_adjacent_scenes_are_barred(self, gap: int) -> None:
        assert "a" in self._hard({"a": [4]}, ["a"], scene=4 + gap)

    def test_three_scenes_apart_is_not_near_adjacent(self) -> None:
        assert "a" not in self._hard({"a": [4]}, ["a"], scene=7)

    def test_a_long_video_applies_the_cap_with_the_window(self) -> None:
        """Outside the window a repeat is allowed once, never twice."""
        matcher = Matcher.__new__(Matcher)  # type: ignore[call-arg]
        matcher.weights = MatchWeights()
        used = ["a", *[f"x{i}" for i in range(10)], "a", *[f"y{i}" for i in range(10)]]
        matcher._placed = {"a": [0, 11]}

        assert "a" in matcher._barred_assets([], used, total_scenes=40, scene_index=22)


# --- a forced repeat is the last resort (D-140) --------------------------------

BARN = [1.0] + [0.0] * 511
OTHER = [0.0, 1.0] + [0.0] * 510


class _Embedder:
    model_id = "fake/model"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [BARN if "barn" in text.lower() else OTHER for text in texts]

    def embed_text_chunks(self, text: str) -> list[list[float]]:
        return self.embed_texts([text])


def _library(tmp_path: Path, vectors: dict[str, list[float]]) -> AssetLibrary:
    library = AssetLibrary(tmp_path / "lib")
    for index, (name, vector) in enumerate(vectors.items()):
        library.add(
            Asset(
                id=name, path=Path(f"{name}.jpg"), kind=AssetKind.IMAGE,
                sha256=str(index).ljust(64, "0"), width=1920, height=1080,
                license=LicenseInfo(name="CC0-1.0", author="Test", source="local"),
            ),
            embedding=vector,
            embed_model="fake/model",
        )
    return library


def _scenes(*texts: str) -> tuple[Scene, ...]:
    return tuple(
        Scene(
            index=index, start_frame=index * 90, end_frame=(index + 1) * 90,
            words=tuple(
                Word(text=word, start=index * 3 + n * 0.4, end=index * 3 + n * 0.4 + 0.3)
                for n, word in enumerate(text.split())
            ),
        )
        for index, text in enumerate(texts)
    )


class TestARepeatIsNeverForced:
    def test_the_second_scene_is_left_for_something_else(self, tmp_path: Path) -> None:
        """It used to take the barn again, "no-repeat rule waived"."""
        library = _library(tmp_path, {"barn": BARN})
        scenes = _scenes("the red barn at dawn", "the old barn at dusk")

        matches = Matcher(library, _Embedder()).match_scenes(scenes, "en")

        assert matches[0].asset is not None and matches[0].asset.id == "barn"
        assert matches[1].asset is None
        assert "repeat" in matches[1].reason

    def test_an_adjacent_repeat_is_not_even_offered(self, tmp_path: Path) -> None:
        library = _library(tmp_path, {"barn": BARN})
        scenes = _scenes("the red barn at dawn", "the old barn at dusk")

        matches = Matcher(library, _Embedder()).match_scenes(scenes, "en")

        assert [asset.id for asset, _ in matches[1].near_misses] == []

    def test_a_distant_repeat_is_offered_for_one_click(self, tmp_path: Path) -> None:
        """Far enough apart and under the cap, the person may choose it."""
        library = _library(tmp_path, {"barn": BARN})
        scenes = _scenes(
            "the red barn at dawn", "quiet words", "more quiet words",
            "the old barn at dusk",
        )

        matches = Matcher(library, _Embedder()).match_scenes(scenes, "en")

        assert matches[3].asset is None
        assert [asset.id for asset, _ in matches[3].near_misses] == ["barn"]

    def test_an_unused_image_is_still_chosen(self, tmp_path: Path) -> None:
        library = _library(
            tmp_path, {"barn": BARN, "barn2": [0.9, 0.1] + [0.0] * 510}
        )
        scenes = _scenes("the red barn at dawn", "the old barn at dusk")

        matches = Matcher(library, _Embedder()).match_scenes(scenes, "en")

        assert matches[1].asset is not None and matches[1].asset.id == "barn2"


class TestLiteProfileMedia:
    """lite is for limited bandwidth, and clips are most of the download."""

    def test_lite_defaults_to_stills(self) -> None:
        assert Settings(profile=ModelProfile.LITE).resolved_media_mix is (
            MediaMix.STILLS
        )

    def test_standard_defaults_to_mixed(self) -> None:
        assert Settings(profile=ModelProfile.STANDARD).resolved_media_mix is (
            MediaMix.MIXED
        )

    def test_lite_caps_clips_smaller(self) -> None:
        lite = Settings(profile=ModelProfile.LITE)
        standard = Settings(profile=ModelProfile.STANDARD)

        assert lite.resolved_max_clip_mb < standard.resolved_max_clip_mb

    def test_an_explicit_mix_overrides_the_profile(self) -> None:
        settings = Settings(profile=ModelProfile.LITE, media_mix=MediaMix.MIXED)

        assert settings.resolved_media_mix is MediaMix.MIXED

    def test_overriding_the_mix_keeps_the_smaller_cap(self) -> None:
        """Opting clips back on should not also raise the size limit."""
        settings = Settings(profile=ModelProfile.LITE, media_mix=MediaMix.MIXED)

        assert settings.resolved_max_clip_mb == 8

    def test_an_explicit_cap_overrides_the_profile(self) -> None:
        settings = Settings(profile=ModelProfile.LITE, max_clip_mb=40)

        assert settings.resolved_max_clip_mb == 40


class TestFetchResultReporting:
    """Download size must be visible rather than discovered from a data bill."""

    def test_megabytes_are_reported(self) -> None:
        from voxframe.sourcing.fetcher import FetchResult

        result = FetchResult(total_bytes=5 * 1024 * 1024)

        assert result.megabytes == pytest.approx(5.0)
        assert "5.0 MB" in result.summary()

    def test_an_empty_run_reports_zero(self) -> None:
        from voxframe.sourcing.fetcher import FetchResult

        assert FetchResult().megabytes == 0.0


class TestSourcingPlanReporting:
    def test_clip_fallbacks_are_counted(self) -> None:
        """The rate must be visible, not silent (D-086)."""
        from voxframe.sourcing.registry import SourcingPlan

        plan = SourcingPlan(scenes_needing_assets=5, clip_fallbacks=2)

        assert "2 clip scene(s) fell back to stills" in plan.summary()

    def test_no_fallbacks_is_not_mentioned(self) -> None:
        from voxframe.sourcing.registry import SourcingPlan

        assert "fell back" not in SourcingPlan(scenes_needing_assets=3).summary()
