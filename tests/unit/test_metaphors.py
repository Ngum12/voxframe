"""Visual metaphors for abstract speech (D-136).

The dictionary is a plain data file contributors edit, so part of this checks
the file itself; the rest pins the two rules that keep it honest -- only for
abstract scenes, and never ahead of a literal match.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.library import AssetLibrary
from voxframe.match import Matcher
from voxframe.match.metaphors import (
    DEFAULT_PATH,
    is_abstract,
    load_metaphors,
    metaphor_queries,
    themes_in,
)
from voxframe.models import Asset, AssetKind, LicenseInfo, Scene, Word

REQUIRED_THEMES = {
    "purpose", "calling", "goals", "hope", "faith", "growth", "success",
    "struggle", "learning", "community", "time", "change",
}


class TestTheDataFile:
    def test_every_requested_theme_is_present(self) -> None:
        names = {theme.name for theme in load_metaphors().themes}

        assert names >= REQUIRED_THEMES

    @pytest.mark.parametrize("language", ["en", "fr"])
    def test_every_theme_triggers_in_both_languages(self, language: str) -> None:
        for theme in load_metaphors().themes:
            assert theme.words[language], f"{theme.name} has no {language} words"

    def test_every_theme_has_concrete_queries(self) -> None:
        for theme in load_metaphors().themes:
            assert theme.queries, theme.name
            for query in theme.queries:
                assert len(query.split()) >= 1

    def test_no_query_asks_for_printed_words(self) -> None:
        """Captions are drawn over the image (D-082)."""
        banned = {"text", "word", "words", "sign", "letters", "quote", "poster"}
        for theme in load_metaphors().themes:
            for query in theme.queries:
                assert not banned & set(query.lower().split()), query

    def test_it_ships_inside_the_package(self) -> None:
        assert DEFAULT_PATH.is_file()
        assert DEFAULT_PATH.parent.name == "match"

    def test_a_theme_without_queries_fails_loudly(self, tmp_path: Path) -> None:
        """A contributor's typo should fail a test, not quietly drop a theme."""
        broken = tmp_path / "m.toml"
        broken.write_text('[themes.hope]\nen = ["hope"]\nfr = ["espoir"]\n', encoding="utf-8")

        with pytest.raises(ValueError, match="needs at least one query"):
            load_metaphors(broken)

    def test_a_malformed_word_list_fails_loudly(self, tmp_path: Path) -> None:
        broken = tmp_path / "m.toml"
        broken.write_text(
            '[themes.hope]\nen = "hope"\nfr = []\nqueries = ["sunrise"]\n', encoding="utf-8"
        )

        with pytest.raises(ValueError, match="must be a list"):
            load_metaphors(broken)


class TestWhenTheyApply:
    def test_abstract_speech_gets_metaphors(self) -> None:
        # Helen Keller, Optimism (1903).
        text = (
            "Optimism is the faith that leads to achievement. Nothing can be done "
            "without hope and confidence."
        )

        assert metaphor_queries(text, ["Optimism", "faith", "leads"], "en")

    def test_concrete_speech_gets_none(self) -> None:
        assert not metaphor_queries(
            "Coral reefs are dying in warm oceans", ["Coral reefs", "warm oceans"], "en"
        )

    def test_a_poem_about_winter_gets_none(self) -> None:
        """Concrete imagery must be matched as itself."""
        text = "A winter, frozen pulse and heart of fire."

        assert not metaphor_queries(text, ["winter frozen pulse", "heart", "fire"], "en")

    def test_french_speech_finds_its_theme(self) -> None:
        text = "Quel est le but de notre vie sur terre ?"

        assert [t.name for t in themes_in(text, "fr")][:1] == ["purpose"]

    def test_accents_are_ignored(self) -> None:
        """Transcripts vary in how they accent: "espérance" and "esperance"."""
        assert themes_in("une grande espérance", "fr")
        assert themes_in("une grande esperance", "fr")

    def test_several_themes_each_get_one_idea(self) -> None:
        """Spread across themes before going deep into one."""
        text = "Our faith gives us hope for the future."
        queries = metaphor_queries(text, ["faith", "hope", "future"], "en", limit=3)
        themes = load_metaphors().themes
        owner = {
            query: next(t.name for t in themes if query in t.queries) for query in queries
        }

        assert len(set(owner.values())) == 3

    def test_mostly_concrete_terms_are_not_abstract(self) -> None:
        assert not is_abstract(["red barn", "wheat field", "goal"], "en")


# --- the matcher --------------------------------------------------------------

SENTENCE = [1.0] + [0.0] * 511
SUMMIT = [0.0, 1.0] + [0.0] * 510
PRAYER = [0.0, 0.0, 1.0] + [0.0] * 509


class FakeEmbedder:
    """Routes on keywords, so which text found the image is observable."""

    model_id = "fake/model"

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_text_chunks(self, text: str) -> list[list[float]]:
        return [self.embed_text(text)]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            lowered = text.lower()
            if "summit" in lowered:
                out.append(SUMMIT)
            elif "god" in lowered:
                out.append(PRAYER)
            else:
                out.append(SENTENCE)
        return out


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


def _scene(text: str) -> Scene:
    words = tuple(
        Word(text=word, start=index * 0.4, end=index * 0.4 + 0.35)
        for index, word in enumerate(text.split())
    )
    return Scene(index=0, start_frame=0, end_frame=90, words=words)


class TestTheMatcher:
    def test_an_abstract_scene_reaches_for_its_metaphor(self, tmp_path: Path) -> None:
        library = _library(tmp_path, {"summit": SUMMIT})
        scene = _scene("what is your purpose and your goal in life")

        match = Matcher(library, FakeEmbedder()).match_scenes((scene,), "en")[0]

        assert match.asset is not None and match.asset.id == "summit"
        assert match.reason.startswith("visual metaphor")

    def test_a_literal_match_is_never_displaced(self, tmp_path: Path) -> None:
        """A praying silhouette for "under God" must stay, not a generic summit."""
        library = _library(tmp_path, {"summit": SUMMIT, "prayer": PRAYER})
        # Abraham Lincoln, the Gettysburg Address (1863).
        scene = _scene("that this nation, under God, shall have a new birth of freedom")

        match = Matcher(library, FakeEmbedder()).match_scenes((scene,), "en")[0]

        assert match.asset is not None and match.asset.id == "prayer"
        assert not match.reason.startswith("visual metaphor")

    def test_a_concrete_scene_never_uses_a_metaphor(self, tmp_path: Path) -> None:
        library = _library(tmp_path, {"summit": SUMMIT})
        scene = _scene("the red barn stands in a wheat field")

        match = Matcher(library, FakeEmbedder()).match_scenes((scene,), "en")[0]

        assert match.asset is None
