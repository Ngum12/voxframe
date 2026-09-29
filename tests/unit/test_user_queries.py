"""Tests that user edits to scene queries take effect and survive (D-052).

A user who rewrites a query in the plan has made a decision. Two things must
follow: the edited query drives the search for that scene, and regenerating the
plan never overwrites it. Either failure makes the plan's editability a lie.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxframe.library import AssetLibrary
from voxframe.match import Matcher
from voxframe.models import Asset, AssetKind, LicenseInfo, Scene, Word
from voxframe.plan import PlannedScene, QuerySource, ScenePlan

ALPHA = [1.0] + [0.0] * 511
BETA = [0.0, 1.0] + [0.0] * 510


class FakeEmbedder:
    """Embeds on a keyword, so which text was searched is observable.

    A real encoder would make the assertions depend on model behaviour rather
    than on the matcher's routing, which is what these tests are about.
    """

    model_id = "fake/model"

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_text_chunks(self, text: str) -> list[list[float]]:
        return [self.embed_text(text)]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [BETA if "beta" in t.lower() else ALPHA for t in texts]


@pytest.fixture
def library(tmp_path: Path) -> AssetLibrary:
    lib = AssetLibrary(tmp_path / "lib")
    licence = LicenseInfo(name="CC0-1.0", author="Test", source="local")

    for index, vector in enumerate((ALPHA, BETA)):
        lib.add(
            Asset(
                id=f"a{index}",
                path=Path(f"a{index}.jpg"),
                kind=AssetKind.IMAGE,
                sha256=str(index).ljust(64, "0"),
                width=1920,
                height=1080,
                license=licence,
            ),
            embedding=vector,
            embed_model="fake/model",
        )
    return lib


def _scene(text: str = "alpha subject here") -> Scene:
    words = tuple(
        Word(text=word, start=index * 0.4, end=index * 0.4 + 0.35)
        for index, word in enumerate(text.split())
    )
    return Scene(index=0, start_frame=0, end_frame=60, words=words)


class TestUserQueryOverride:
    def test_automatic_search_uses_the_scene_text(
        self, library: AssetLibrary
    ) -> None:
        matcher = Matcher(library, FakeEmbedder())
        match = matcher.match_scenes((_scene("alpha subject here"),), "en")[0]

        assert match.asset is not None
        assert match.asset.id == "a0"

    def test_user_query_replaces_the_scene_text(
        self, library: AssetLibrary
    ) -> None:
        """The core requirement: the edit must change the result."""
        matcher = Matcher(library, FakeEmbedder())

        match = matcher.match_scenes(
            (_scene("alpha subject here"),), "en", user_queries={0: ("beta",)}
        )[0]

        assert match.asset is not None
        assert match.asset.id == "a1", "user query did not override the scene text"

    def test_override_is_recorded_in_the_reason(
        self, library: AssetLibrary
    ) -> None:
        """A user reading the plan should see why this scene matched."""
        matcher = Matcher(library, FakeEmbedder())
        match = matcher.match_scenes(
            (_scene(),), "en", user_queries={0: ("beta",)}
        )[0]

        assert "user query" in match.reason

    def test_empty_override_falls_back_to_the_text(
        self, library: AssetLibrary
    ) -> None:
        """Blank queries are not an instruction to search for nothing."""
        matcher = Matcher(library, FakeEmbedder())

        match = matcher.match_scenes(
            (_scene("alpha subject"),), "en", user_queries={0: ("", "   ")}
        )[0]

        assert match.asset is not None
        assert match.asset.id == "a0"

    def test_override_applies_only_to_the_named_scene(
        self, library: AssetLibrary
    ) -> None:
        matcher = Matcher(library, FakeEmbedder())

        scenes = (
            _scene("alpha one"),
            Scene(
                index=1,
                start_frame=60,
                end_frame=120,
                words=(Word(text="alpha", start=2.0, end=2.4),),
            ),
        )
        matches = matcher.match_scenes(scenes, "en", user_queries={0: ("beta",)})

        assert matches[0].asset is not None and matches[0].asset.id == "a1"
        assert matches[1].asset is not None and matches[1].asset.id == "a0"

    def test_several_user_queries_are_combined(
        self, library: AssetLibrary
    ) -> None:
        matcher = Matcher(library, FakeEmbedder())
        match = matcher.match_scenes(
            (_scene(),), "en", user_queries={0: ("beta one", "beta two")}
        )[0]

        assert match.asset is not None
        assert match.asset.id == "a1"
        assert len(match.queries) == 2


class TestPlanMarksUserEdits:
    def _plan(self, source: QuerySource) -> ScenePlan:
        return ScenePlan(
            audio_path="a.wav",
            audio_sha256="a" * 64,
            audio_duration=2.0,
            fps=30.0,
            total_frames=60,
            scenes=(
                PlannedScene(
                    index=0,
                    start_frame=0,
                    end_frame=60,
                    text="the original scene text",
                    queries=("edited query",),
                    query_source=source,
                ),
            ),
        )

    def test_default_is_automatic(self) -> None:
        assert self._plan(QuerySource.AUTOMATIC).scenes[0].query_source is (
            QuerySource.AUTOMATIC
        )

    def test_user_edits_are_listed(self) -> None:
        """Regeneration needs to know which scenes to leave alone."""
        assert self._plan(QuerySource.USER).user_edited_scenes == (0,)
        assert self._plan(QuerySource.AUTOMATIC).user_edited_scenes == ()

    def test_marker_survives_a_round_trip(self, tmp_path: Path) -> None:
        """An edit must not be lost by saving and reloading the plan."""
        path = self._plan(QuerySource.USER).save(tmp_path / "plan.json")
        loaded = ScenePlan.load(path)

        assert loaded.scenes[0].query_source is QuerySource.USER
        assert loaded.scenes[0].queries == ("edited query",)

    def test_marker_is_visible_in_the_json(self, tmp_path: Path) -> None:
        """A person editing the file by hand must be able to set it."""
        path = self._plan(QuerySource.USER).save(tmp_path / "plan.json")
        data = json.loads(path.read_text(encoding="utf-8"))

        assert data["scenes"][0]["query_source"] == "user"

    def test_hand_written_marker_is_accepted(self, tmp_path: Path) -> None:
        """The realistic path: someone edits the JSON in a text editor."""
        path = self._plan(QuerySource.AUTOMATIC).save(tmp_path / "plan.json")

        data = json.loads(path.read_text(encoding="utf-8"))
        data["scenes"][0]["queries"] = ["a different subject"]
        data["scenes"][0]["query_source"] = "user"
        path.write_text(json.dumps(data), encoding="utf-8")

        loaded = ScenePlan.load(path)
        assert loaded.user_edited_scenes == (0,)
        assert loaded.scenes[0].queries == ("a different subject",)
