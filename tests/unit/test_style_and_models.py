"""Tests for style templates (D-006) and the transcript/scene domain models."""

from __future__ import annotations

import pytest

from voxframe.config.style import (
    BUILTIN_TEMPLATES,
    CaptionStyle,
    MotionStyle,
    PacingStyle,
    get_template,
)
from voxframe.models.scene import Scene
from voxframe.models.transcript import Transcript, Word


class TestStyleTemplate:
    def test_default_template_exists(self) -> None:
        template = get_template()
        assert template.name in BUILTIN_TEMPLATES

    def test_unknown_name_lists_alternatives(self) -> None:
        """A typo must say what is available, not just fail."""
        with pytest.raises(KeyError, match="Available:"):
            get_template("no-such-template")

    def test_templates_are_frozen(self) -> None:
        """A shared builtin must not be mutable by one caller."""
        template = get_template()
        with pytest.raises(ValueError, match="frozen"):
            template.name = "changed"  # type: ignore[misc]

    def test_derive_produces_a_copy(self) -> None:
        original = get_template()
        derived = original.derive(name="custom")

        assert derived.name == "custom"
        assert original.name != "custom"

    def test_derive_merges_nested_sections(self) -> None:
        """Changing one caption field must not reset the others."""
        original = get_template()
        derived = original.derive(captions={"uppercase": True})

        assert derived.captions.uppercase is True
        assert derived.captions.font_family == original.captions.font_family
        assert derived.captions.max_lines == original.captions.max_lines


class TestCaptionStyle:
    def test_font_size_scales_with_frame(self) -> None:
        """One template must work at 1080p and 4K without re-tuning."""
        style = CaptionStyle(font_size_ratio=0.05)
        assert style.font_size_px(1080) == 54
        assert style.font_size_px(2160) == 108

    def test_font_size_has_a_floor(self) -> None:
        assert CaptionStyle(font_size_ratio=0.011).font_size_px(100) >= 12

    def test_vertical_margin_larger_than_wide(self) -> None:
        """Vertical video must clear platform UI; landscape must not float."""
        style = CaptionStyle()
        vertical = style.margin_vertical_px(1080, 1920)
        landscape = style.margin_vertical_px(1920, 1080)
        assert vertical > landscape

    def test_square_treated_as_wide(self) -> None:
        style = CaptionStyle()
        square = style.margin_vertical_px(1080, 1080)
        landscape = style.margin_vertical_px(1920, 1080)
        assert square == landscape

    def test_absurd_margins_rejected(self) -> None:
        """Margins that would leave no room for text are refused.

        The field bound (<= 0.45) catches most cases; the model validator
        catches the remainder, where two margins together squeeze the text box.
        """
        with pytest.raises(ValueError):
            CaptionStyle(margin_horizontal_ratio=0.46)

        with pytest.raises(ValueError, match="under 10%"):
            CaptionStyle(margin_horizontal_ratio=0.45)


class TestPacingStyle:
    def test_inverted_window_rejected(self) -> None:
        with pytest.raises(ValueError, match="must exceed"):
            PacingStyle(min_scene_seconds=8.0, max_scene_seconds=4.0)

    def test_defaults_match_the_brief(self) -> None:
        """The brief specifies a new visual every 4-8 seconds by default."""
        pacing = PacingStyle()
        assert pacing.min_scene_seconds == 4.0
        assert pacing.max_scene_seconds == 8.0


class TestMotionStyle:
    def test_parallax_displacement_capped(self) -> None:
        """A large cap creates disocclusions too big to inpaint (D-007)."""
        with pytest.raises(ValueError):
            MotionStyle(parallax_max_displacement=0.5)

    def test_conservative_default(self) -> None:
        assert MotionStyle().parallax_max_displacement <= 0.05


class TestWord:
    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(ValueError, match="ends"):
            Word(text="bad", start=2.0, end=1.0)

    def test_stripped_removes_punctuation(self) -> None:
        assert Word(text="world!", start=0, end=1).stripped == "world"
        assert Word(text='"quoted"', start=0, end=1).stripped == "quoted"
        assert Word(text="fine", start=0, end=1).stripped == "fine"

    def test_duration(self) -> None:
        assert Word(text="x", start=1.0, end=1.5).duration == pytest.approx(0.5)


class TestTranscript:
    def _transcript(self, specs: list[tuple[str, float, float]]) -> Transcript:
        return Transcript(
            words=tuple(Word(text=t, start=s, end=e) for t, s, e in specs),
            language="en",
            duration=specs[-1][2] + 0.5,
            model_id="test",
            audio_sha256="a" * 64,
        )

    def test_out_of_order_words_rejected(self) -> None:
        """Unordered words would make captions jump backwards."""
        with pytest.raises(ValueError, match="out of order"):
            self._transcript([("second", 5.0, 5.5), ("first", 1.0, 1.5)])

    def test_text_joins_words(self) -> None:
        transcript = self._transcript([("Hello", 0, 0.5), ("world", 0.6, 1.1)])
        assert transcript.text == "Hello world"

    def test_words_between_uses_midpoint(self) -> None:
        """A word straddling a boundary belongs to exactly one scene.

        "b" spans 0.9-1.3, so its midpoint is exactly 1.1. With half-open
        ranges the boundary belongs to the *later* scene, which is what stops
        a straddling word being captioned twice.
        """
        transcript = self._transcript(
            [("a", 0.0, 0.4), ("b", 0.9, 1.3), ("c", 1.8, 2.2)]
        )
        first = transcript.words_between(0.0, 1.1)
        second = transcript.words_between(1.1, 3.0)

        assert [w.text for w in first] == ["a"]
        assert [w.text for w in second] == ["b", "c"]
        assert len(first) + len(second) == transcript.word_count

    def test_no_word_assigned_twice_across_a_boundary(self) -> None:
        transcript = self._transcript(
            [("a", 0.0, 0.4), ("b", 0.9, 1.3), ("c", 1.8, 2.2)]
        )
        for boundary in (0.5, 1.0, 1.1, 1.5, 2.0):
            before = transcript.words_between(0.0, boundary)
            after = transcript.words_between(boundary, 3.0)
            assert len(before) + len(after) == transcript.word_count

    def test_pauses_detected(self) -> None:
        transcript = self._transcript(
            [("a", 0.0, 0.4), ("b", 0.5, 0.9), ("c", 2.5, 2.9)]
        )
        pauses = transcript.pauses(threshold=0.5)

        assert len(pauses) == 1
        assert pauses[0] == pytest.approx((0.9, 2.5))


class TestScene:
    def test_zero_length_rejected(self) -> None:
        with pytest.raises(ValueError, match="not after"):
            Scene(index=0, start_frame=100, end_frame=100)

    def test_emphasis_index_must_be_valid(self) -> None:
        word = Word(text="only", start=0, end=1)
        with pytest.raises(ValueError, match="outside"):
            Scene(index=0, start_frame=0, end_frame=30, words=(word,), emphasis=(5,))

    def test_silent_scene(self) -> None:
        assert Scene(index=0, start_frame=0, end_frame=30).is_silent

    def test_seconds_derived_from_frames(self) -> None:
        scene = Scene(index=0, start_frame=30, end_frame=90)
        assert scene.start_seconds(30.0) == pytest.approx(1.0)
        assert scene.duration_seconds(30.0) == pytest.approx(2.0)
