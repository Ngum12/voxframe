"""Web addresses never become search terms (D-139).

"read for Librevox .org" searched for "Librevox" and matched an image for it.
A domain names a website, and a website is never what a scene should show, so
addresses are removed in every language rather than LibriVox being special-cased.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.library import AssetLibrary
from voxframe.match import Matcher
from voxframe.match.queries import extract_queries, strip_web_addresses
from voxframe.models import Asset, AssetKind, LicenseInfo, Scene, Word


def _queries(text: str, language: str = "en") -> list[str]:
    return [query.text for query in extract_queries(text, language)]


def _words(text: str, language: str = "en") -> set[str]:
    return {word.lower() for query in _queries(text, language) for word in query.split()}


class TestTheLibriVoxPreamble:
    def test_the_sonnet_credit_line(self) -> None:
        """The line from the owner's measurement set, as transcribed."""
        words = _words("read for Librevox .org by Fox in the Stars of Shininghalf .com.")

        assert "librevox" not in words
        assert "org" not in words
        assert "shininghalf" not in words

    def test_the_fable_credit_line(self) -> None:
        words = _words(
            "rendez -vous sur Librivox .org. Enregistré par Esoua, fable de la Fontaine.",
            "fr",
        )

        assert "librivox" not in words
        assert "fable" in words, "the scene keeps its other words"


@pytest.mark.parametrize(
    "text",
    [
        "visit librivox.org for more",
        "visit Librivox .org for more",
        "visit www.librivox.org for more",
        "visit https://librivox.org/about for more",
        "visit librivox dot org for more",
        "visit example.co.uk/path for more",
        "write to reader@example.net for more",
    ],
)
def test_every_way_of_writing_an_address_is_removed(text: str) -> None:
    words = _words(text)

    assert not words & {"librivox", "example", "reader", "org", "com", "net", "www"}


@pytest.mark.parametrize(
    "text",
    ["rendez-vous sur librivox point org", "allez sur exemple.fr", "voir exemple .com"],
)
def test_french_addresses_are_removed(text: str) -> None:
    assert not _words(text, "fr") & {"librivox", "exemple", "org", "com"}


class TestWhatStays:
    def test_the_rest_of_the_scene_still_searches(self) -> None:
        assert "mountains" in _words("see example.com for mountains")

    def test_words_either_side_never_join_into_one_phrase(self) -> None:
        """The address is a break, not a gap to close."""
        queries = _queries("golden river example.com mountain valley")

        assert all("river" not in q or "mountain" not in q for q in queries)

    @pytest.mark.parametrize(
        "text",
        [
            "the ocean. Org charts hung on the wall",  # a sentence break, not ".org"
            "Mr. Smith walked to the harbour",
            "The U.S. army marched",
        ],
    )
    def test_sentence_breaks_and_abbreviations_are_not_addresses(self, text: str) -> None:
        assert strip_web_addresses(text) == text

    def test_point_de_vue_is_french_not_a_domain(self) -> None:
        assert strip_web_addresses("à ce point de vue la mer") == "à ce point de vue la mer"

    def test_a_french_sentence_break_is_not_a_domain(self) -> None:
        """A space after the dot is never part of an address: ". De" is a
        sentence ending, however much "de" looks like Germany's domain."""
        assert strip_web_addresses("la fin. De plus, la mer") == "la fin. De plus, la mer"


class TestTheMatcher:
    def test_the_sentence_it_embeds_has_no_address(self, tmp_path: Path) -> None:
        """The whole sentence is what is matched (D-051), so the address has
        to go from it too, not only from the queries recorded in the plan."""
        seen: list[str] = []

        class Recording:
            model_id = "fake/model"

            def embed_texts(self, texts: list[str]) -> list[list[float]]:
                seen.extend(texts)
                return [[1.0] + [0.0] * 511 for _ in texts]

            def embed_text_chunks(self, text: str) -> list[list[float]]:
                return self.embed_texts([text])

        library = AssetLibrary(tmp_path / "lib")
        library.add(
            Asset(
                id="river", path=Path("river.jpg"), kind=AssetKind.IMAGE,
                sha256="0" * 64, width=1920, height=1080,
                license=LicenseInfo(name="CC0-1.0", author="Test", source="local"),
            ),
            embedding=[1.0] + [0.0] * 511,
            embed_model="fake/model",
        )
        text = "read for Librevox .org by the golden river"
        scene = Scene(
            index=0, start_frame=0, end_frame=90,
            words=tuple(
                Word(text=word, start=n * 0.4, end=n * 0.4 + 0.3)
                for n, word in enumerate(text.split())
            ),
        )

        Matcher(library, Recording()).match_scenes((scene,), "en")

        assert seen, "nothing was embedded"
        assert not any("librevox" in item.lower() or ".org" in item for item in seen)
        assert any("golden river" in item for item in seen)
