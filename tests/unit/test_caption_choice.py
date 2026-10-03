"""Caption choices kept in the plan, and the document built from them (D-196)."""

from __future__ import annotations

import pytest

from voxframe.config.style import (
    CaptionAnimation,
    CaptionStyle,
    CaptionTransition,
    get_template,
)
from voxframe.models.scene import Scene
from voxframe.models.transcript import Word
from voxframe.plan.caption_choice import CaptionChoice
from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan
from voxframe.render.captions.ass import BOX_STYLE, WORD_STYLE, build_ass
from voxframe.render.compose.from_plan import plan_captions


def _scene() -> Scene:
    words = (("Floods", 0.3, 0.7), ("hit", 0.75, 0.95), ("Douala", 1.0, 1.5))
    return Scene(
        index=0, start_frame=0, end_frame=60,
        words=tuple(Word(text=t, start=s, end=e) for t, s, e in words),
    )


def _dialogues(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


class TestChoice:
    def test_nothing_chosen_changes_nothing(self) -> None:
        style = CaptionStyle()
        assert CaptionChoice().is_default
        assert CaptionChoice().apply(style) is style

    def test_each_choice_is_made(self) -> None:
        style = CaptionStyle(highlight_enabled=False)
        chosen = CaptionChoice(
            animation=CaptionAnimation.POP, transition=CaptionTransition.SLIDE,
            anchor_y=0.5, size=1.5, uppercase=True,
        ).apply(style)
        assert chosen.effective_animation is CaptionAnimation.POP
        assert chosen.transition is CaptionTransition.SLIDE
        assert chosen.anchor_y == 0.5
        assert chosen.font_size_ratio == pytest.approx(style.font_size_ratio * 1.5)
        assert chosen.uppercase

    def test_choosing_plain_turns_highlighting_off(self) -> None:
        chosen = CaptionChoice(animation=CaptionAnimation.PLAIN).apply(CaptionStyle())
        assert chosen.effective_animation is CaptionAnimation.PLAIN

    def test_a_placement_off_the_frame_is_refused(self) -> None:
        with pytest.raises(ValueError):
            CaptionChoice(anchor_y=1.2)


class TestDocument:
    def test_the_default_is_as_it_was(self) -> None:
        """No choice made: only colour tags, so existing videos are unchanged."""
        ass = build_ass((_scene(),), CaptionStyle(), 1920, 1080, 30.0)
        assert WORD_STYLE not in ass and BOX_STYLE not in ass
        for line in _dialogues(ass):
            text = line.split(",", 9)[9]
            assert "\\pos" not in text and "\\fade" not in text and "\\fsc" not in text

    @pytest.mark.parametrize("animation", list(CaptionAnimation))
    @pytest.mark.parametrize("transition", list(CaptionTransition))
    def test_every_line_has_its_fields(self, animation, transition) -> None:  # type: ignore[no-untyped-def]
        style = CaptionStyle(animation=animation, transition=transition, anchor_y=0.4)
        ass = build_ass((_scene(),), style, 1080, 1920, 30.0)
        for line in _dialogues(ass):
            # Text is last and may hold commas; the nine before it may not.
            fields = line.removeprefix("Dialogue: ").split(",", 9)
            assert len(fields) == 10
            assert fields[0].isdigit()

    @pytest.mark.parametrize("animation", list(CaptionAnimation))
    def test_nothing_shows_after_the_scene(self, animation) -> None:  # type: ignore[no-untyped-def]
        ass = build_ass((_scene(),), CaptionStyle(animation=animation), 1080, 1920, 30.0)
        for line in _dialogues(ass):
            end = line.split(",")[2]
            assert end <= "0:00:02.00"

    def test_word_animations_declare_their_styles(self) -> None:
        ass = build_ass(
            (_scene(),), CaptionStyle(), 1080, 1920, 30.0,
            animations={0: CaptionAnimation.SPOTLIGHT},
        )
        assert f"Style: {WORD_STYLE}," in ass and f"Style: {BOX_STYLE}," in ass
        assert any(f",{WORD_STYLE}," in line for line in _dialogues(ass))


def _plan(**changes: object) -> ScenePlan:
    words = (
        PlanWord(text="Floods", start=0.3, end=0.7),
        PlanWord(text="hit", start=0.75, end=0.95),
        PlanWord(text="Douala", start=1.0, end=1.5),
    )
    return ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64, audio_duration=2.0, fps=30.0,
        total_frames=60,
        scenes=(
            PlannedScene(index=0, start_frame=0, end_frame=60, text="Floods hit Douala", words=words),
        ),
        **changes,  # type: ignore[arg-type]
    )


class TestFromThePlan:
    def test_choices_round_trip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        plan = _plan(captions=CaptionChoice(animation=CaptionAnimation.BOUNCE, anchor_y=0.3))
        scene = plan.scenes[0].model_copy(
            update={"caption_animation": CaptionAnimation.POP, "emphasis": (2,)}
        )
        plan = plan.model_copy(update={"scenes": (scene,)})
        loaded = ScenePlan.load(plan.save(tmp_path / "p.json"))
        assert loaded.captions == plan.captions
        assert loaded.scenes[0].caption_animation is CaptionAnimation.POP
        assert loaded.scenes[0].emphasis == (2,)

    def test_the_document_follows_the_plan(self) -> None:
        template = get_template()
        before = plan_captions(_plan(), template, 1080, 1920)
        placed = plan_captions(
            _plan(captions=CaptionChoice(anchor_y=0.3)), template, 1080, 1920
        )
        assert "\\pos" not in before
        assert "\\an5\\pos(540,576)" in placed

    def test_emphasis_out_of_range_is_dropped(self) -> None:
        """A correction can leave fewer words than were emphasised."""
        scene = _plan().scenes[0].model_copy(update={"emphasis": (1, 7)})
        plan = _plan().model_copy(update={"scenes": (scene,)})
        ass = plan_captions(plan, get_template(), 1080, 1920)
        assert "\\fscx125" in ass
