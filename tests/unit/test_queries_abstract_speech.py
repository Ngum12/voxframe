"""Query extraction on abstract, conversational speech (D-135).

Before this fix, speech full of modals, contractions and bare verbs produced
search terms like "need", "able", "don" (from "don't"), "identify", "leave",
"Ceci", "rendez" and "dit" -- words no image service can do anything useful
with, which is part of why a first video filled only a third of its scenes
(D-132).

The English sentences are public-domain speech and prose, chosen for the same
constructions: modals ("will", "can", "shall", "must", "would", "could",
"should") and a negated contraction ("don't"). The French ones are the LibriVox
fable, as transcribed.
"""

from __future__ import annotations

import pytest

from voxframe.match.queries import extract_queries

#: Public-domain sentences, verbatim.
SPEECH = [
    # Abraham Lincoln, the Gettysburg Address (1863).
    "that this nation, under God, shall have a new birth of freedom",
    "The world will little note, nor long remember what we say here, but it can "
    "never forget what they did here.",
    # Mark Twain, Adventures of Huckleberry Finn (1884), the opening line.
    "You don't know about me without you have read a book by the name of The "
    "Adventures of Tom Sawyer; but that ain't no matter.",
    # Ralph Waldo Emerson, Self-Reliance (1841).
    "Whoso would be a man must be a nonconformist.",
    # Henry David Thoreau, Walden (1854).
    "I went to the woods because I wished to live deliberately, to front only the "
    "essential facts of life, and see if I could not learn what it had to teach.",
    "Therefore, though they should fail immediately, they had better aim at "
    "something high.",
    # Helen Keller, Optimism (1903).
    "Optimism is the faith that leads to achievement. Nothing can be done without "
    "hope and confidence.",
]

#: Function words that must never become a search term.
NEVER_EN = {
    "need", "able", "don", "will", "must", "can", "would", "should", "going",
    "identify", "leave", "called", "calls", "execute", "accomplish", "whereby",
}


def words_in(text: str, language: str = "en") -> set[str]:
    return {
        word.lower()
        for query in extract_queries(text, language)
        for word in query.text.split()
    }


@pytest.mark.parametrize("sentence", SPEECH)
def test_no_modal_auxiliary_or_bare_verb_becomes_a_query(sentence: str) -> None:
    assert not (words_in(sentence) & NEVER_EN), extract_queries(sentence)


def test_a_negated_contraction_is_dropped_whole() -> None:
    """ "don't" was split into "don" and "t", and "don" survived."""
    assert "don" not in words_in(SPEECH[2])


@pytest.mark.parametrize(
    "contraction", ["can't", "won't", "isn't", "didn't", "don\u2019t"]
)
def test_every_negated_auxiliary_is_dropped(contraction: str) -> None:
    """Curly apostrophes too: transcripts use both."""
    assert not words_in(f"You {contraction} see the ocean") - {"ocean"}


def test_a_possessive_keeps_its_noun() -> None:
    assert "people" in words_in("the people's harvest festival")


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        (SPEECH[0], {"god", "nation", "birth"}),
        (SPEECH[2], {"book"}),
        (SPEECH[3], {"man", "nonconformist"}),
        (SPEECH[6], {"optimism", "faith"}),
    ],
)
def test_the_subject_survives(sentence: str, expected: set[str]) -> None:
    """Filtering must remove the noise, not the content."""
    assert expected <= words_in(sentence)


def test_semi_modals_are_filtered_in_any_form() -> None:
    sentence = "we are going to need to be able to see it, and you ought to try"

    assert not words_in(sentence)


def test_a_url_is_not_a_query() -> None:
    assert not (words_in("read for Librevox .org by Fox in the Stars .com") & {"org", "com", "read"})


class TestFrench:
    def test_a_demonstrative_is_not_a_query(self) -> None:
        text = "Ceci est un enregistrement Librivox, tous nos enregistrements"

        assert "ceci" not in words_in(text, "fr")

    def test_an_imperative_is_not_a_query(self) -> None:
        assert "rendez" not in words_in("rendez -vous sur Librivox .org", "fr")

    def test_speech_verbs_and_determiners_are_not_queries(self) -> None:
        text = "Je vous priais lui dit telle, avant l'eau, foie d'animal"

        assert not (words_in(text, "fr") & {"dit", "telle"})

    def test_an_elision_keeps_its_noun(self) -> None:
        """ "l'eau" is "eau", not "l" plus a lost word."""
        assert "eau" in words_in("avant l'eau, foie d'animal", "fr")

    @pytest.mark.parametrize(
        "modal", ["peut", "doit", "faut", "veut", "pouvons", "devez", "besoin"]
    )
    def test_french_modals_are_filtered(self, modal: str) -> None:
        assert modal not in words_in(f"Il {modal} regarder la montagne", "fr")
