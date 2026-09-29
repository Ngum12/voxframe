"""Style templates must differ in the edit, not only in appearance (D-098).

A template that changed fonts alone would not justify the flag. These assert
that each one changes pacing, motion or transitions in a way that produces a
different video.
"""

from __future__ import annotations

import pytest

from voxframe.config.style import (
    BUILTIN_TEMPLATES,
    DEFAULT_TEMPLATE,
    CaptionBacking,
    TransitionKind,
    get_template,
)


class TestTemplateRegistry:
    def test_the_default_exists(self) -> None:
        assert DEFAULT_TEMPLATE in BUILTIN_TEMPLATES

    def test_get_template_returns_the_default_when_unnamed(self) -> None:
        assert get_template().name == DEFAULT_TEMPLATE

    def test_an_unknown_name_lists_the_alternatives(self) -> None:
        """A typo here is otherwise a confusing failure."""
        with pytest.raises(KeyError) as caught:
            get_template("documentry")

        message = str(caught.value)
        assert "documentary" in message

    @pytest.mark.parametrize("name", sorted(BUILTIN_TEMPLATES))
    def test_every_template_is_retrievable_by_name(self, name: str) -> None:
        assert get_template(name).name == name

    @pytest.mark.parametrize("name", sorted(BUILTIN_TEMPLATES))
    def test_every_template_describes_itself(self, name: str) -> None:
        """`voxframe styles` shows these; a blank one helps nobody."""
        assert len(BUILTIN_TEMPLATES[name].description) > 30


class TestTemplatesDiffer:
    """The point of a template is a different edit."""

    def test_pacing_differs_across_templates(self) -> None:
        maxima = {
            name: template.pacing.max_scene_seconds
            for name, template in BUILTIN_TEMPLATES.items()
        }

        assert len(set(maxima.values())) > 1

    def test_energetic_is_faster_than_documentary(self) -> None:
        energetic = BUILTIN_TEMPLATES["energetic"].pacing
        documentary = BUILTIN_TEMPLATES["documentary"].pacing

        assert energetic.max_scene_seconds < documentary.min_scene_seconds

    def test_documentary_moves_more_slowly_than_energetic(self) -> None:
        assert (
            BUILTIN_TEMPLATES["documentary"].motion.intensity
            < BUILTIN_TEMPLATES["energetic"].motion.intensity
        )

    def test_minimal_has_no_motion(self) -> None:
        """Also the cheapest render: no zoompan, no oversampling."""
        motion = BUILTIN_TEMPLATES["minimal"].motion

        assert not motion.ken_burns_enabled
        assert motion.transition is TransitionKind.CUT

    def test_energetic_cuts_rather_than_blends(self) -> None:
        """A crossfade is the opposite of punchy, and 2s scenes have no room."""
        assert BUILTIN_TEMPLATES["energetic"].motion.transition is TransitionKind.CUT

    def test_documentary_blends_longest(self) -> None:
        blends = {
            name: template.motion.transition_seconds
            for name, template in BUILTIN_TEMPLATES.items()
            if template.motion.transition is not TransitionKind.CUT
        }

        assert blends["documentary"] == max(blends.values())


class TestCaptionStylePerTemplate:
    """D-095: box for documentary styles, outline for energetic."""

    def test_energetic_uses_outline(self) -> None:
        assert (
            BUILTIN_TEMPLATES["energetic"].captions.backing
            is CaptionBacking.OUTLINE
        )

    def test_documentary_uses_a_box(self) -> None:
        assert (
            BUILTIN_TEMPLATES["documentary"].captions.backing is CaptionBacking.BOX
        )

    def test_the_default_uses_a_box(self) -> None:
        """The safe option for a template that says nothing."""
        assert get_template().captions.backing is CaptionBacking.BOX

    def test_energetic_captions_are_larger(self) -> None:
        """Watched on a phone, often muted."""
        assert (
            BUILTIN_TEMPLATES["energetic"].captions.font_size_ratio
            > BUILTIN_TEMPLATES["documentary"].captions.font_size_ratio
        )

    def test_energetic_is_uppercase(self) -> None:
        assert BUILTIN_TEMPLATES["energetic"].captions.uppercase

    def test_documentary_is_not_uppercase(self) -> None:
        assert not BUILTIN_TEMPLATES["documentary"].captions.uppercase


class TestCaptionBackingOverride:
    """The override exists so the three styles can be compared (D-093)."""

    def test_an_override_applies_over_the_template(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXFRAME_CAPTION_BACKING", "band")

        assert get_template("documentary").captions.backing is CaptionBacking.BAND

    def test_an_unknown_value_is_ignored_rather_than_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo in an environment variable should not stop a render."""
        monkeypatch.setenv("VOXFRAME_CAPTION_BACKING", "sparkly")

        assert get_template("documentary").captions.backing is CaptionBacking.BOX

    def test_no_override_leaves_the_template_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VOXFRAME_CAPTION_BACKING", raising=False)

        assert (
            get_template("energetic").captions.backing is CaptionBacking.OUTLINE
        )
