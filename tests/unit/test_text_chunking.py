"""Tests for handling text longer than the encoder accepts (D-053).

The text encoder truncates at 77 tokens **silently**: a long scene would search
on its opening words alone with nothing to indicate it. These check that long
text is chunked instead, and that the boundary behaviour is right.
"""

from __future__ import annotations

import pytest

from voxframe.library.embeddings import (
    CHUNK_OVERLAP,
    CHUNK_OVERLAP_TOKENS,
    CHUNK_TOKENS,
    CHUNK_WORDS,
    MAX_TEXT_TOKENS,
    _chunk_by_tokens,
    _chunk_words,
)

#: Rough token counts per word, measured against the real tokenizer (D-056).
#: English splits gently; French long words and German compounds do not.
TOKENS_PER_WORD = {"en": 1.2, "fr": 4.2, "de": 5.3}


def _fake_counter(ratio: float):
    """A tokenizer stand-in with a fixed tokens-per-word ratio.

    Lets the chunker be tested for every language's behaviour without loading
    a 1.5 GB model into a unit test.
    """

    def count(text: str) -> int:
        return int(len(text.split()) * ratio) + 2

    return count


class TestChunking:
    def test_short_text_is_one_chunk(self) -> None:
        """No splitting where none is needed."""
        text = "The mountain rose above the valley."
        assert _chunk_words(text) == [text]

    def test_text_at_the_limit_is_one_chunk(self) -> None:
        text = " ".join(f"word{i}" for i in range(CHUNK_WORDS))
        assert len(_chunk_words(text)) == 1

    def test_long_text_splits(self) -> None:
        text = " ".join(f"word{i}" for i in range(95))
        chunks = _chunk_words(text)

        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.split()) <= CHUNK_WORDS

    def test_chunks_overlap(self) -> None:
        """A subject spanning a boundary must appear whole in one chunk."""
        text = " ".join(f"word{i}" for i in range(95))
        chunks = _chunk_words(text)

        first = set(chunks[0].split())
        second = set(chunks[1].split())
        shared = first & second

        assert len(shared) >= CHUNK_OVERLAP - 1, (
            f"chunks share only {len(shared)} words; a subject at the boundary "
            "would be split across both"
        )

    def test_every_word_appears_somewhere(self) -> None:
        """Chunking must not lose content, which truncation does."""
        words = [f"word{i}" for i in range(120)]
        chunks = _chunk_words(" ".join(words))

        covered = set()
        for chunk in chunks:
            covered.update(chunk.split())

        assert covered == set(words), "chunking dropped words"

    def test_no_trailing_fragment(self) -> None:
        """A final chunk already covered by its predecessor is wasted work."""
        for count in range(CHUNK_WORDS + 1, CHUNK_WORDS + 20):
            text = " ".join(f"w{i}" for i in range(count))
            chunks = _chunk_words(text)
            if len(chunks) > 1:
                assert len(chunks[-1].split()) > CHUNK_OVERLAP // 2

    def test_empty_text(self) -> None:
        assert _chunk_words("") == [""]


class TestLimitIsRealistic:
    def test_a_normal_scene_fits(self) -> None:
        """The default 8-second scene must not trigger chunking.

        At roughly 2.5 to 3.5 words per second, an 8-second scene holds about
        20 to 28 words. In English that is well inside the token limit. If this
        fails, chunking has become the common path rather than the exception
        and the constants need revisiting.
        """
        scene_text = " ".join(["word"] * 28)
        count = _fake_counter(TOKENS_PER_WORD["en"])
        assert len(_chunk_by_tokens(scene_text, count)) == 1

    def test_a_normal_french_scene_also_fits(self) -> None:
        """French tokenizes harder, so the same scene length is checked again.

        At 4.2 tokens per word a 28-word scene reaches about 119 tokens, which
        *does* exceed the limit — so French scenes at the top of the pacing
        window are chunked routinely. That is correct behaviour, and worth
        pinning so it is a known property rather than a surprise.
        """
        scene_text = " ".join(["mot"] * 28)
        count = _fake_counter(TOKENS_PER_WORD["fr"])
        chunks = _chunk_by_tokens(scene_text, count)

        assert len(chunks) > 1
        for chunk in chunks:
            assert count(chunk) <= CHUNK_TOKENS

    def test_the_limit_is_recorded(self) -> None:
        """Measured from the tokenizer, not assumed."""
        assert MAX_TEXT_TOKENS == 77
        assert CHUNK_WORDS < MAX_TEXT_TOKENS, (
            "chunks must be shorter than the token limit, with headroom for "
            "subword splits and punctuation"
        )


@pytest.mark.needs_models
class TestAgainstTheRealEncoder:
    """Verified against the actual tokenizer, not a model of it."""

    def test_chunks_fit_the_encoder(self) -> None:
        from voxframe.library.embeddings import Embedder

        embedder = Embedder(use_gpu=False)
        text = " ".join(["the mountain rose sharply above the valley"] * 12)

        for chunk in _chunk_words(text):
            assert embedder.token_count(chunk) <= MAX_TEXT_TOKENS, (
                "a chunk still exceeds the encoder limit; CHUNK_WORDS needs "
                "more headroom"
            )

    def test_long_text_produces_several_vectors(self) -> None:
        from voxframe.library.embeddings import Embedder

        embedder = Embedder(use_gpu=False)
        text = " ".join(["the mountain rose sharply above the valley"] * 12)

        vectors = embedder.embed_text_chunks(text)
        assert len(vectors) > 1
        assert all(len(v) == 512 for v in vectors)


class TestTokenBasedChunking:
    """Chunks are bounded by tokens, not words (D-056).

    Word count is a proxy that fails unevenly across languages: 40 English
    words measured at 48 tokens, the same number of French words at 169 and
    German compounds at 212. Splitting on words guarantees nothing.
    """

    @pytest.mark.parametrize("language", ["en", "fr", "de"])
    def test_no_chunk_exceeds_the_limit(self, language: str) -> None:
        count = _fake_counter(TOKENS_PER_WORD[language])
        text = " ".join(f"word{i}" for i in range(120))

        for chunk in _chunk_by_tokens(text, count):
            assert count(chunk) <= CHUNK_TOKENS, (
                f"a {language} chunk reached {count(chunk)} tokens against a "
                f"{CHUNK_TOKENS} limit"
            )

    def test_french_long_words_split_more(self) -> None:
        """The case that motivated this: identical word counts, different
        token counts, so the chunk counts must differ."""
        text = " ".join(f"word{i}" for i in range(80))

        english = _chunk_by_tokens(text, _fake_counter(TOKENS_PER_WORD["en"]))
        french = _chunk_by_tokens(text, _fake_counter(TOKENS_PER_WORD["fr"]))

        assert len(french) > len(english), (
            "French text should split into more chunks than English of the "
            "same word count, because its tokens per word are higher"
        )

    def test_short_text_is_one_chunk(self) -> None:
        count = _fake_counter(TOKENS_PER_WORD["fr"])
        assert _chunk_by_tokens("les montagnes", count) == ["les montagnes"]

    def test_chunks_overlap(self) -> None:
        count = _fake_counter(TOKENS_PER_WORD["en"])
        text = " ".join(f"word{i}" for i in range(150))
        chunks = _chunk_by_tokens(text, count)

        assert len(chunks) > 1
        shared = set(chunks[0].split()) & set(chunks[1].split())
        assert shared, "chunks do not overlap; a boundary subject would split"

    def test_every_word_is_covered(self) -> None:
        count = _fake_counter(TOKENS_PER_WORD["fr"])
        words = [f"word{i}" for i in range(150)]
        chunks = _chunk_by_tokens(" ".join(words), count)

        covered: set[str] = set()
        for chunk in chunks:
            covered.update(chunk.split())

        assert covered == set(words), "chunking dropped words"

    def test_terminates_on_a_single_over_long_word(self) -> None:
        """A word longer than the limit cannot be split without cutting
        mid-word, and must not loop forever."""
        chunks = _chunk_by_tokens("x" * 400, lambda t: len(t))
        assert len(chunks) == 1

    def test_overlap_budget_is_in_tokens(self) -> None:
        assert CHUNK_OVERLAP_TOKENS < CHUNK_TOKENS
        assert CHUNK_TOKENS < MAX_TEXT_TOKENS, (
            "chunks must leave room for the tokenizer's special tokens"
        )


@pytest.mark.needs_models
class TestFrenchAgainstTheRealTokenizer:
    """The specific regression: long French words at the old 40-word chunk."""

    def test_forty_french_words_would_have_overflowed(self) -> None:
        from voxframe.library.embeddings import Embedder

        embedder = Embedder(use_gpu=False)
        forty = " ".join(
            [
                "anticonstitutionnellement",
                "incompréhensiblement",
                "particulièrement",
                "extraordinairement",
                "vraisemblablement",
            ]
            * 8
        )

        assert embedder.token_count(forty) > MAX_TEXT_TOKENS, (
            "test premise broken: these 40 words no longer exceed the limit"
        )

        for chunk in _chunk_by_tokens(forty, embedder.token_count):
            assert embedder.token_count(chunk) <= MAX_TEXT_TOKENS

    @pytest.mark.parametrize(
        "text",
        [
            "les sommets enneigés des montagnes dominaient la vallée tranquille " * 9,
            "the mountain rose sharply above the quiet valley below " * 9,
        ],
    )
    def test_real_long_text_chunks_within_the_limit(self, text: str) -> None:
        from voxframe.library.embeddings import Embedder

        embedder = Embedder(use_gpu=False)
        for chunk in _chunk_by_tokens(text, embedder.token_count):
            assert embedder.token_count(chunk) <= MAX_TEXT_TOKENS
