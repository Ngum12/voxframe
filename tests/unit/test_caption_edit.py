"""Caption corrections must preserve word timings.

The cases that matter are the ones where the word count changes: a correction
splitting one spoken word into two, or merging two into one. Those are where a
naive positional approach silently shifts every later word onto the wrong
timing, so they are tested explicitly rather than only in aggregate.
"""

from __future__ import annotations

import pytest

from voxframe.models.transcript import Word
from voxframe.plan import apply_caption_correction
from voxframe.plan.scene_plan import PlannedScene, PlanWord


def words(*specs: tuple[str, float, float]) -> tuple[Word, ...]:
    return tuple(Word(text=text, start=start, end=end) for text, start, end in specs)


def _spans(result: tuple[Word, ...]) -> list[tuple[str, float, float]]:
    return [(w.text, round(w.start, 3), round(w.end, 3)) for w in result]


class TestUnchanged:
    def test_identical_text_keeps_every_word_object(self) -> None:
        original = words(("the", 0.0, 0.3), ("quick", 0.3, 0.8))
        result = apply_caption_correction(original, "the quick")

        assert result.words == original
        assert not result.changed

    def test_punctuation_only_change_is_not_a_correction(self) -> None:
        """Adding a comma must not redistribute timings."""
        original = words(("hello", 0.0, 0.4), ("world", 0.4, 0.9))
        result = apply_caption_correction(original, "hello, world!")

        # The text updates but the timings are untouched.
        assert [w.text for w in result.words] == ["hello,", "world!"]
        assert [w.start for w in result.words] == [0.0, 0.4]
        assert [w.end for w in result.words] == [0.4, 0.9]

    def test_empty_correction_is_ignored(self) -> None:
        """Blanking a caption is almost always an accident."""
        original = words(("kept", 0.0, 0.5))
        result = apply_caption_correction(original, "   ")

        assert result.words == original


class TestOneToOne:
    def test_misheard_word_inherits_exact_timing(self) -> None:
        """The common case: "leave" was actually "live"."""
        original = words(
            ("I", 0.0, 0.2), ("leave", 0.2, 0.7), ("here", 0.7, 1.1)
        )
        result = apply_caption_correction(original, "I live here")

        assert _spans(result.words) == [
            ("I", 0.0, 0.2),
            ("live", 0.2, 0.7),
            ("here", 0.7, 1.1),
        ]
        assert result.summary() == "1 replace"

    def test_later_words_keep_their_timings(self) -> None:
        """A correction early on must not shift what follows."""
        original = words(
            ("goal", 0.0, 0.4),
            ("is", 0.4, 0.6),
            ("to", 0.6, 0.75),
            ("accomplish", 0.75, 1.5),
        )
        result = apply_caption_correction(original, "good is to accomplish")

        assert result.words[-1].start == 0.75
        assert result.words[-1].end == 1.5


class TestSplit:
    def test_one_word_becoming_two_divides_the_span(self) -> None:
        """A split must cover exactly the original span, with no gap."""
        original = words(("cant", 1.0, 1.6), ("stop", 1.6, 2.0))
        result = apply_caption_correction(original, "can not stop")

        assert [w.text for w in result.words] == ["can", "not", "stop"]

        can, not_, stop = result.words
        assert can.start == pytest.approx(1.0)
        # Contiguous: no gap and no overlap between the two halves.
        assert can.end == pytest.approx(not_.start)
        # The split ends exactly where the original word ended.
        assert not_.end == pytest.approx(1.6)
        # The following word is untouched.
        assert (stop.start, stop.end) == (1.6, 2.0)

    def test_split_is_proportional_to_word_length(self) -> None:
        """A longer replacement gets more of the span than a shorter one."""
        original = words(("dont", 0.0, 1.0))
        result = apply_caption_correction(original, "do not")

        do, not_ = result.words
        assert do.duration < not_.duration
        assert do.duration + not_.duration == pytest.approx(1.0)

    def test_split_into_three(self) -> None:
        original = words(("gonnado", 2.0, 2.9))
        result = apply_caption_correction(original, "going to do")

        assert [w.text for w in result.words] == ["going", "to", "do"]
        assert result.words[0].start == pytest.approx(2.0)
        assert result.words[-1].end == pytest.approx(2.9)
        # Strictly increasing, which caption rendering requires.
        for previous, following in zip(
            result.words, result.words[1:], strict=False
        ):
            assert previous.end <= following.start

    def test_split_is_reported_as_a_split(self) -> None:
        result = apply_caption_correction(words(("cant", 0.0, 0.5)), "can not")
        assert [op.kind for op in result.operations] == ["split"]


class TestMerge:
    def test_two_words_becoming_one_spans_both(self) -> None:
        """A merge keeps the full span, so no audio goes uncaptioned."""
        original = words(
            ("it", 0.0, 0.2), ("may", 0.2, 0.5), ("be", 0.5, 0.8), ("so", 0.8, 1.0)
        )
        result = apply_caption_correction(original, "it maybe so")

        assert [w.text for w in result.words] == ["it", "maybe", "so"]

        maybe = result.words[1]
        # Spans from "may"'s start to "be"'s end.
        assert maybe.start == pytest.approx(0.2)
        assert maybe.end == pytest.approx(0.8)

    def test_merge_leaves_no_gap_against_neighbours(self) -> None:
        original = words(
            ("every", 0.0, 0.4), ("one", 0.4, 0.7), ("knows", 0.7, 1.2)
        )
        result = apply_caption_correction(original, "everyone knows")

        everyone, knows = result.words
        assert everyone.start == pytest.approx(0.0)
        assert everyone.end == pytest.approx(0.7)
        assert knows.start == pytest.approx(0.7)

    def test_merge_is_reported_as_a_merge(self) -> None:
        result = apply_caption_correction(
            words(("may", 0.0, 0.3), ("be", 0.3, 0.6)), "maybe"
        )
        assert [op.kind for op in result.operations] == ["merge"]

    def test_three_words_becoming_one(self) -> None:
        original = words(
            ("never", 0.0, 0.3), ("the", 0.3, 0.4), ("less", 0.4, 0.8)
        )
        result = apply_caption_correction(original, "nevertheless")

        assert len(result.words) == 1
        assert result.words[0].start == pytest.approx(0.0)
        assert result.words[0].end == pytest.approx(0.8)


class TestInsertAndDelete:
    def test_inserted_word_gets_an_ordered_span(self) -> None:
        """A word never spoken has no true timing, but must still be ordered."""
        original = words(("hello", 0.0, 0.5), ("world", 1.0, 1.5))
        result = apply_caption_correction(original, "hello there world")

        assert [w.text for w in result.words] == ["hello", "there", "world"]
        for previous, following in zip(
            result.words, result.words[1:], strict=False
        ):
            assert previous.start <= following.start
            assert previous.end <= following.end

    def test_deleted_word_leaves_the_rest_in_place(self) -> None:
        original = words(
            ("the", 0.0, 0.2), ("um", 0.2, 0.4), ("point", 0.4, 0.9)
        )
        result = apply_caption_correction(original, "the point")

        assert _spans(result.words) == [("the", 0.0, 0.2), ("point", 0.4, 0.9)]

    def test_word_inserted_at_the_start(self) -> None:
        original = words(("world", 1.0, 1.5))
        result = apply_caption_correction(original, "hello world")

        assert [w.text for w in result.words] == ["hello", "world"]
        assert result.words[0].start >= 0.0
        assert result.words[0].end <= result.words[1].start


class TestInvariants:
    """Properties that must hold whatever the edit, since ASS requires them."""

    @pytest.mark.parametrize(
        "corrected",
        [
            "I live here now",
            "I cannot stop",
            "live",
            "I can not stop it here",
            "totally different words entirely",
            "I leave here",
        ],
    )
    def test_words_are_ordered_and_non_overlapping(self, corrected: str) -> None:
        original = words(
            ("I", 0.0, 0.2),
            ("leave", 0.2, 0.7),
            ("here", 0.7, 1.1),
            ("now", 1.1, 1.4),
        )
        result = apply_caption_correction(original, corrected)

        for previous, following in zip(
            result.words, result.words[1:], strict=False
        ):
            assert previous.start <= following.start
            assert previous.end <= following.start + 1e-9

    @pytest.mark.parametrize(
        "corrected",
        ["I live here now", "I cannot stop", "one two three four five"],
    )
    def test_word_count_matches_the_correction(self, corrected: str) -> None:
        """Every corrected word must be displayed, and nothing extra."""
        original = words(
            ("I", 0.0, 0.2), ("leave", 0.2, 0.7), ("here", 0.7, 1.1),
            ("now", 1.1, 1.4),
        )
        result = apply_caption_correction(original, corrected)

        assert " ".join(w.text for w in result.words) == corrected

    def test_correction_stays_within_the_original_span(self) -> None:
        """Captions must not run past the scene they belong to."""
        original = words(("cant", 2.0, 2.5), ("stop", 2.5, 3.0))
        result = apply_caption_correction(original, "can not stop")

        assert min(w.start for w in result.words) >= 2.0
        assert max(w.end for w in result.words) <= 3.0

    def test_no_original_words_returns_nothing(self) -> None:
        assert apply_caption_correction((), "anything").words == ()


class TestPlannedSceneIntegration:
    """The correction must reach the renderer through the plan (D-011)."""

    def scene(self, caption_text: str = "") -> PlannedScene:
        return PlannedScene(
            index=0,
            start_frame=0,
            end_frame=45,
            text="I leave here",
            caption_text=caption_text,
            words=(
                PlanWord(text="I", start=0.0, end=0.2),
                PlanWord(text="leave", start=0.2, end=0.7),
                PlanWord(text="here", start=0.7, end=1.1),
            ),
        )

    def test_uncorrected_scene_displays_the_transcript(self) -> None:
        scene = self.scene()

        assert scene.display_text == "I leave here"
        assert not scene.is_corrected
        assert [w.text for w in scene.caption_words()] == ["I", "leave", "here"]

    def test_corrected_scene_displays_the_correction(self) -> None:
        scene = self.scene("I live here")

        assert scene.display_text == "I live here"
        assert scene.is_corrected
        assert [w.text for w in scene.caption_words()] == ["I", "live", "here"]

    def test_correction_preserves_timings_through_the_plan(self) -> None:
        scene = self.scene("I live here")
        corrected = scene.caption_words()

        assert (corrected[1].start, corrected[1].end) == (0.2, 0.7)

    def test_split_through_the_plan(self) -> None:
        scene = self.scene("I do not leave here")
        texts = [w.text for w in scene.caption_words()]

        assert texts == ["I", "do", "not", "leave", "here"]

    def test_transcript_text_is_left_intact(self) -> None:
        """The original stays visible, so a correction can be reviewed."""
        scene = self.scene("I live here")
        assert scene.text == "I leave here"

    def test_caption_text_equal_to_text_is_not_a_correction(self) -> None:
        """Re-saving the plan unchanged must not count as an edit."""
        scene = self.scene("I leave here")
        assert not scene.is_corrected
