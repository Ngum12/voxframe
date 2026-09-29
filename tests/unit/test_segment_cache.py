"""Segment caching (D-101).

A cache is only useful if it invalidates correctly. Both directions are tested:
every input that changes the output must change the key, and inputs that do not
affect the output must not — a cache that misses on every render is merely
wasted disk, but one that hits when it should not serves the wrong frames.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.plan import PlanAsset, PlannedScene, PlanWord
from voxframe.render.compose.segment_cache import SegmentCache, segment_key


def _scene(**overrides: object) -> PlannedScene:
    defaults: dict[str, object] = {
        "index": 0,
        "start_frame": 0,
        "end_frame": 150,
        "text": "some words",
        "words": (PlanWord(text="some", start=0.0, end=0.5),),
        "asset": PlanAsset(
            id="asset-one",
            path="one.jpg",
            width=3000,
            height=2000,
            license_name="CC0-1.0",
            license_author="Someone",
            license_source="test",
        ),
    }
    defaults.update(overrides)
    return PlannedScene(**defaults)  # type: ignore[arg-type]


BASE: dict[str, object] = {
    "frames": 150,
    "width": 1280,
    "height": 720,
    "fps": 30.0,
    "motion_signature": "kb=True:i=0.500:px=True",
    "quality": "standard",
    "background": "0x141824",
}


def _key(scene: PlannedScene | None = None, **overrides: object) -> str:
    return segment_key(scene or _scene(), **{**BASE, **overrides})  # type: ignore[arg-type]


class TestKeyInvalidation:
    """Anything that changes the output must change the key."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("frames", 162),
            ("width", 1920),
            ("height", 1080),
            ("fps", 25.0),
            ("motion_signature", "kb=False:i=0.500:px=True"),
            ("quality", "high"),
            ("background", "0x000000"),
        ],
    )
    def test_render_inputs_change_the_key(
        self, field: str, value: object
    ) -> None:
        assert _key(**{field: value}) != _key()

    def test_a_different_asset_changes_the_key(self) -> None:
        other = _scene(
            asset=PlanAsset(
                id="asset-two",
                path="two.jpg",
                width=3000,
                height=2000,
                license_name="CC0-1.0",
                license_author="Someone",
                license_source="test",
            )
        )

        assert _key(other) != _key()

    def test_no_asset_differs_from_an_asset(self) -> None:
        assert _key(_scene(asset=None)) != _key()

    def test_card_text_changes_the_key(self) -> None:
        """A card's text is what it looks like."""
        first = _scene(asset=None, card_kind="title", card_text="One")
        second = _scene(asset=None, card_kind="title", card_text="Two")

        assert _key(first) != _key(second)


class TestKeyStability:
    """Inputs that do not affect the output must not change the key."""

    def test_renumbering_does_not_change_the_key(self) -> None:
        """Inserting a title card renumbers every later scene (D-099)."""
        assert _key(_scene(index=99)) == _key()

    def test_moving_a_scene_on_the_timeline_does_not_change_the_key(self) -> None:
        """A segment looks the same wherever it sits."""
        moved = _scene(start_frame=900, end_frame=1050)

        assert _key(moved) == _key()

    def test_the_same_inputs_give_the_same_key(self) -> None:
        assert _key() == _key()

    def test_the_asset_is_keyed_by_content_not_path(self) -> None:
        """The same image re-downloaded elsewhere should still hit."""
        moved = _scene(
            asset=PlanAsset(
                id="asset-one",
                path="somewhere/else/one.jpg",
                width=3000,
                height=2000,
                license_name="CC0-1.0",
                license_author="Someone",
                license_source="test",
            )
        )

        assert _key(moved) == _key()


class TestCacheStore:
    def test_a_disabled_cache_never_hits(self, tmp_path: Path) -> None:
        cache = SegmentCache(None)

        assert not cache.enabled
        assert not cache.fetch("any", tmp_path / "out.mp4")

    def test_a_stored_segment_is_returned(self, tmp_path: Path) -> None:
        cache = SegmentCache(tmp_path / "cache")
        source = tmp_path / "seg.mp4"
        source.write_bytes(b"pretend video")

        cache.store("k1", source)
        destination = tmp_path / "restored.mp4"

        assert cache.fetch("k1", destination)
        assert destination.read_bytes() == b"pretend video"

    def test_a_missing_key_is_a_miss(self, tmp_path: Path) -> None:
        cache = SegmentCache(tmp_path / "cache")

        assert not cache.fetch("absent", tmp_path / "out.mp4")
        assert cache.misses == 1

    def test_an_empty_entry_is_treated_as_a_miss(self, tmp_path: Path) -> None:
        """A zero-length file is a crashed render, not a cache entry."""
        cache = SegmentCache(tmp_path / "cache")
        (tmp_path / "cache").mkdir()
        empty = cache.path_for("broken")
        assert empty is not None
        empty.write_bytes(b"")

        assert not cache.fetch("broken", tmp_path / "out.mp4")
        assert not empty.exists()

    def test_hits_and_misses_are_counted(self, tmp_path: Path) -> None:
        cache = SegmentCache(tmp_path / "cache")
        source = tmp_path / "seg.mp4"
        source.write_bytes(b"x")
        cache.store("k", source)

        cache.fetch("k", tmp_path / "a.mp4")
        cache.fetch("absent", tmp_path / "b.mp4")

        assert cache.hits == 1
        assert cache.misses == 1
        assert "1/2" in cache.summary()

    def test_storing_a_missing_file_is_harmless(self, tmp_path: Path) -> None:
        cache = SegmentCache(tmp_path / "cache")

        cache.store("k", tmp_path / "does-not-exist.mp4")

        assert not cache.fetch("k", tmp_path / "out.mp4")

    def test_no_partial_files_are_left_behind(self, tmp_path: Path) -> None:
        """An interrupted copy must not look like a valid entry."""
        cache = SegmentCache(tmp_path / "cache")
        source = tmp_path / "seg.mp4"
        source.write_bytes(b"x")

        cache.store("k", source)

        assert not list((tmp_path / "cache").glob("*.partial"))
