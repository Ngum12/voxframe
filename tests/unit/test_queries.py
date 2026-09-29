"""Tests for visual query extraction (project owner's item 4).

Narration is not a search query. These tests pin the behaviours that matter for
matching quality: verbs are excluded, noun phrases stay intact, and function
words are dropped in both supported languages.

Extraction is heuristic and offline by design, so it will never be perfect.
Tests therefore assert *properties* — "no verb in the output", "the subject
appears" — rather than exact strings, which would break on every tuning change
without indicating a real regression.
"""

from __future__ import annotations

import pytest

from voxframe.match.queries import SUPPORTED_LANGUAGES, VisualQuery, extract_queries


def _texts(queries: tuple[VisualQuery, ...]) -> list[str]:
    return [q.text.lower() for q in queries]


def _joined(queries: tuple[VisualQuery, ...]) -> str:
    return " ".join(_texts(queries))


class TestEnglishExtraction:
    def test_subject_survives_filler(self) -> None:
        """The point of the module: strip conversational padding."""
        queries = extract_queries(
            "And so what I want to talk about today is really the way that "
            "coral reefs are changing",
            "en",
        )
        assert "coral" in _joined(queries)
        assert "reef" in _joined(queries)

    def test_noun_phrase_stays_intact(self) -> None:
        """'coral reefs' must not split: the words match different images."""
        queries = extract_queries("coral reefs are dying", "en")
        assert any("coral" in text and "reef" in text for text in _texts(queries))

    def test_verb_excluded_from_phrase(self) -> None:
        """A verb pulls the embedding toward action, not the subject."""
        queries = extract_queries(
            "The Amazon rainforest produces twenty percent of the oxygen", "en"
        )
        assert "produces" not in _joined(queries)
        assert "amazon" in _joined(queries)

    def test_visual_adjectives_kept(self) -> None:
        """Colour and scale words change what an image looks like."""
        queries = extract_queries(
            "She walked through the ancient stone corridors", "en"
        )
        assert "ancient" in _joined(queries)

    def test_function_words_dropped(self) -> None:
        queries = extract_queries("the of and with from into", "en")
        assert queries == ()

    def test_empty_text(self) -> None:
        assert extract_queries("", "en") == ()

    def test_punctuation_only(self) -> None:
        assert extract_queries("... !!! ---", "en") == ()


class TestFrenchExtraction:
    def test_subject_extracted(self) -> None:
        """La Fontaine's cicada is the subject and must rank."""
        queries = extract_queries(
            "La cigale ayant chanté tout l'été se trouva fort dépourvue", "fr"
        )
        assert "cigale" in _joined(queries)

    def test_french_verbs_excluded(self) -> None:
        """Past participles and gerunds are verbs, not subjects."""
        queries = extract_queries(
            "La cigale ayant chanté tout l'été se trouva fort dépourvue", "fr"
        )
        joined = _joined(queries)
        for verb in ("ayant", "chanté", "trouva"):
            assert verb not in joined, f"verb {verb!r} leaked into queries"

    def test_french_noun_phrase(self) -> None:
        queries = extract_queries(
            "Les montagnes enneigées dominaient le petit village", "fr"
        )
        joined = _joined(queries)
        assert "montagnes" in joined
        assert "dominaient" not in joined, "verb leaked"

    def test_french_articles_dropped(self) -> None:
        queries = extract_queries("Le corbeau tenait dans son bec un fromage", "fr")
        joined = _joined(queries)
        assert "fromage" in joined or "corbeau" in joined
        for article in (" le ", " la ", " un ", " des "):
            assert article not in f" {joined} "

    def test_accents_preserved_in_output(self) -> None:
        """Accents are stripped for comparison but must survive into the query.

        The multilingual encoder is trained on accented French; stripping them
        would degrade matching.
        """
        queries = extract_queries("Les montagnes enneigées", "fr")
        assert any("é" in q.text for q in queries)


class TestRanking:
    def test_weights_descend(self) -> None:
        queries = extract_queries(
            "The ancient stone cathedral stood above the small harbour town", "en"
        )
        weights = [q.weight for q in queries]
        assert weights == sorted(weights, reverse=True)

    def test_max_queries_respected(self) -> None:
        text = "mountains rivers forests deserts oceans glaciers valleys canyons"
        assert len(extract_queries(text, "en", max_queries=2)) <= 2

    def test_max_words_per_query_respected(self) -> None:
        queries = extract_queries(
            "ancient stone cathedral tower spire roof", "en", max_words_per_query=2
        )
        for query in queries:
            assert len(query.text.split()) <= 2

    def test_near_duplicates_removed(self) -> None:
        """'coral reef' and 'reef' must not both occupy a slot."""
        queries = extract_queries("coral reef reef coral reefs", "en")
        texts = _texts(queries)
        assert len(texts) == len(set(texts))


class TestProvenance:
    def test_source_word_indices_recorded(self) -> None:
        """A user editing the plan needs to see where a query came from."""
        queries = extract_queries("The ancient stone cathedral", "en")
        assert queries
        for query in queries:
            assert query.source_words
            assert all(isinstance(i, int) for i in query.source_words)

    def test_indices_are_within_range(self) -> None:
        text = "The ancient stone cathedral stood tall"
        queries = extract_queries(text, "en")
        word_count = len(text.split())
        for query in queries:
            assert all(0 <= i < word_count for i in query.source_words)


class TestLanguageHandling:
    @pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
    def test_supported_languages_work(self, language: str) -> None:
        assert extract_queries("mountain cathedral village", language) != ()

    def test_unknown_language_falls_back(self) -> None:
        """An unsupported language must still produce something usable."""
        queries = extract_queries("Berg Kathedrale Dorf", "de")
        assert queries != ()


class TestPluralNouns:
    """Regression tests for D-041.

    Plural nouns were classified as verbs and discarded, so "Waves rolled
    across the deep blue ocean" yielded ``['across', 'deep', 'blue']`` — the
    entire subject gone. These use plural subjects deliberately, which the
    original tests did not.
    """

    @pytest.mark.parametrize(
        "subject",
        ["waves", "trees", "mountains", "clouds", "fields", "flowers", "birds"],
    )
    def test_plural_subject_survives(self, subject: str) -> None:
        queries = extract_queries(f"The {subject} moved slowly", "en")
        assert subject in _joined(queries), f"plural noun {subject!r} was dropped"

    def test_subject_after_a_verb_survives(self) -> None:
        """A verb ends a phrase; the words after it must still be considered.

        Previously the remainder of the sentence was abandoned, so "ocean" was
        lost even though it is the clearest visual term present.
        """
        queries = extract_queries("Waves rolled across the deep blue ocean", "en")
        joined = _joined(queries)
        assert "ocean" in joined
        assert "waves" in joined

    def test_unrecognised_nouns_are_kept(self) -> None:
        """The noun suffix rules recognise only a fraction of English nouns."""
        for noun in ("ocean", "forest", "sun", "valley", "river"):
            queries = extract_queries(f"A wide {noun} lay ahead", "en")
            assert noun in _joined(queries), f"{noun!r} was dropped"

    def test_irregular_past_tense_excluded(self) -> None:
        """'rose' is a real noun, but in narration it is nearly always a verb."""
        queries = extract_queries("The sun rose over the hills", "en")
        joined = _joined(queries)
        assert "rose" not in joined.split()
        assert "sun" in joined

    def test_french_plural_subject_survives(self) -> None:
        queries = extract_queries("Les vagues déferlaient sur l'océan", "fr")
        joined = _joined(queries)
        assert "vagues" in joined
        assert "océan" in joined


class TestRealNarration:
    """Sentences taken from actual matching runs, where the bug appeared."""

    @pytest.mark.parametrize(
        ("text", "language", "expected"),
        [
            ("Snow covered mountain peaks rose above the valley", "en", "mountain"),
            ("A thick forest of tall green pine trees", "en", "forest"),
            ("The bright sun rose slowly over the horizon", "en", "sun"),
            ("Les sommets enneigés des montagnes dominaient la vallée", "fr", "sommets"),
            ("Une forêt dense de grands pins verts", "fr", "forêt"),
            ("Le soleil brillant se leva sur l'horizon", "fr", "soleil"),
        ],
    )
    def test_visual_subject_present(
        self, text: str, language: str, expected: str
    ) -> None:
        queries = extract_queries(text, language)
        assert expected in _joined(queries), (
            f"subject {expected!r} missing from {[q.text for q in queries]}"
        )
