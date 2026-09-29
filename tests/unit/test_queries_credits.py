"""Names in reader credits never become search terms (D-141).

With the web address gone (D-139), "read for Librevox .org by Fox in the Stars"
matched a photograph of a fox. The owner's decision: leave names in credit
lines out of search, only in credit patterns -- a person named elsewhere in a
sentence may well be its subject.
"""

from __future__ import annotations

import pytest

from voxframe.match.queries import extract_queries, searchable_text


def _words(text: str, language: str = "en") -> set[str]:
    return {
        word.lower()
        for query in extract_queries(text, language)
        for word in query.text.split()
    }


class TestTheLibriVoxCredits:
    def test_the_sonnet_credit_line(self) -> None:
        """The line from the owner's measurement set, as transcribed."""
        words = _words("read for Librevox .org by Fox in the Stars of Shininghalf .com.")

        assert not words & {"fox", "stars", "librevox", "shininghalf"}

    def test_the_fable_credit_line(self) -> None:
        words = _words(
            "rendez -vous sur Librivox .org. Enregistré par Esoua, fable de la Fontaine.",
            "fr",
        )

        assert "esoua" not in words
        assert "fable" in words, "the rest of the sentence still searches"


@pytest.mark.parametrize(
    ("text", "language", "name"),
    [
        ("This chapter was read by Mary Ann Smith.", "en", "smith"),
        ("Recorded by John Doe for the family.", "en", "doe"),
        ("Narrated by Kofi Mensah.", "en", "mensah"),
        ("Read for LibriVox by Mary Smith.", "en", "librivox"),
        ("Lu par Jean Dupont.", "fr", "dupont"),
        ("Enregistré par Esoua.", "fr", "esoua"),
        ("Enregistrée par Marie Curie pour vous.", "fr", "curie"),
        ("Raconté par Amadou Hampâté Bâ.", "fr", "amadou"),
    ],
)
def test_every_credit_pattern_drops_its_name(text: str, language: str, name: str) -> None:
    assert name not in _words(text, language)


class TestOnlyInCredits:
    def test_a_person_named_elsewhere_stays(self) -> None:
        """Helen Hunt Jackson wrote the sonnets; she is not a credit."""
        assert "jackson" in _words("Helen Hunt Jackson wrote about January.")

    def test_a_name_that_is_the_subject_stays(self) -> None:
        assert "fox" in _words("Fox ran across the field by the river.")

    @pytest.mark.parametrize(
        "text", ["The tale was read by the fire every winter.", "The letter was read by candlelight."]
    )
    def test_read_by_something_that_is_not_a_name(self, text: str) -> None:
        """A credit needs a capitalised name after it."""
        assert searchable_text(text, marker="") == text

    def test_a_place_after_the_name_stays(self) -> None:
        """"in" joins a name only as "in the", as in "Fox in the Stars"."""
        assert "london" in _words("This recording was narrated by Jane Smith in London.")


class TestNothingLeftToSearch:
    def test_a_credit_line_with_nothing_else_is_not_searched(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """What the credit leaves ("of .") still has a nearest image; one
        cleared the threshold at 0.176. Nothing searchable means no search."""
        from pathlib import Path

        from voxframe.library import AssetLibrary
        from voxframe.match import Matcher
        from voxframe.models import Asset, AssetKind, LicenseInfo, Scene, Word

        embedded: list[str] = []

        class Everything:
            model_id = "fake/model"

            def embed_texts(self, texts: list[str]) -> list[list[float]]:
                embedded.extend(texts)
                return [[1.0] + [0.0] * 511 for _ in texts]

            def embed_text_chunks(self, text: str) -> list[list[float]]:
                return self.embed_texts([text])

        library = AssetLibrary(tmp_path / "lib")
        library.add(
            Asset(
                id="slide", path=Path("slide.jpg"), kind=AssetKind.IMAGE,
                sha256="0" * 64, width=1920, height=1080,
                license=LicenseInfo(name="CC0-1.0", author="Test", source="local"),
            ),
            embedding=[1.0] + [0.0] * 511,
            embed_model="fake/model",
        )
        text = "read for Librevox .org by Fox in the Stars of Shininghalf .com."
        scene = Scene(
            index=0, start_frame=0, end_frame=90,
            words=tuple(
                Word(text=word, start=n * 0.4, end=n * 0.4 + 0.3)
                for n, word in enumerate(text.split())
            ),
        )

        match = Matcher(library, Everything()).match_scenes((scene,), "en")[0]

        assert match.asset is None
        assert "nothing in this scene can be searched" in match.reason
        assert embedded == []
