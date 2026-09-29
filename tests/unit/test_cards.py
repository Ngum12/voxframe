"""Title and chapter cards (D-092, D-099).

A card adds time to a video whose audio is fixed, so the audio, the captions
and the tail padding must all shift by the same amount. Missing any one gives a
video that looks right in a thumbnail and is wrong to watch.
"""

from __future__ import annotations

import pytest

from voxframe.plan import PlannedScene, PlanWord, ScenePlan
from voxframe.plan.builder import insert_cards
from voxframe.render.compose.cards import (
    CHAPTER_PAUSE_SECONDS,
    MIN_AUDIO_FOR_CHAPTERS,
    Card,
    CardKind,
    plan_chapter_cards,
)


def _scene(
    index: int,
    start: int,
    end: int,
    *,
    first_word: float | None = None,
    last_word: float | None = None,
) -> PlannedScene:
    words: tuple[PlanWord, ...] = ()
    if first_word is not None and last_word is not None:
        words = (
            PlanWord(text="first", start=first_word, end=first_word + 0.2),
            PlanWord(text="middle", start=first_word + 0.3, end=first_word + 0.5),
            PlanWord(text="last", start=last_word - 0.2, end=last_word),
        )

    return PlannedScene(
        index=index,
        start_frame=start,
        end_frame=end,
        text="some spoken words",
        words=words,
    )


def _plan(scenes: tuple[PlannedScene, ...], fps: float = 30.0) -> ScenePlan:
    return ScenePlan(
        audio_path="a.wav",
        audio_sha256="0" * 64,
        audio_duration=scenes[-1].end_frame / fps,
        fps=fps,
        total_frames=scenes[-1].end_frame,
        scenes=scenes,
    )


class TestTitleCard:
    def test_no_title_means_no_card(self) -> None:
        """A title is never derived from a filename (D-092)."""
        plan = _plan((_scene(0, 0, 150), _scene(1, 150, 300)))

        assert insert_cards(plan, title="", chapters=False).scenes == plan.scenes

    def test_whitespace_is_not_a_title(self) -> None:
        plan = _plan((_scene(0, 0, 150), _scene(1, 150, 300)))

        result = insert_cards(plan, title="   ", chapters=False)

        assert not any(scene.is_card for scene in result.scenes)

    def test_a_title_adds_a_card_at_the_front(self) -> None:
        plan = _plan((_scene(0, 0, 150), _scene(1, 150, 300)))

        result = insert_cards(plan, title="My Talk", chapters=False)

        assert result.scenes[0].is_card
        assert result.scenes[0].card_kind == CardKind.TITLE
        assert result.scenes[0].card_text == "My Talk"

    def test_the_card_adds_time_rather_than_taking_it(self) -> None:
        """Stealing frames from scene 0 would desync everything after it."""
        plan = _plan((_scene(0, 0, 150), _scene(1, 150, 300)))

        result = insert_cards(
            plan, title="My Talk", title_seconds=3.0, chapters=False
        )

        assert result.total_frames == plan.total_frames + 90

    def test_original_scene_durations_are_unchanged(self) -> None:
        plan = _plan((_scene(0, 0, 150), _scene(1, 150, 300)))

        result = insert_cards(plan, title="My Talk", chapters=False)
        durations = [s.duration_frames for s in result.scenes if not s.is_card]

        assert durations == [150, 150]

    def test_the_timeline_still_tiles(self) -> None:
        """The grid invariant, restated after insertion (D-013)."""
        plan = _plan((_scene(0, 0, 150), _scene(1, 150, 300)))

        result = insert_cards(plan, title="My Talk", chapters=False)

        assert result.scenes[0].start_frame == 0
        for previous, following in zip(
            result.scenes, result.scenes[1:], strict=False
        ):
            assert previous.end_frame == following.start_frame
        assert result.scenes[-1].end_frame == result.total_frames

    def test_indices_are_renumbered(self) -> None:
        plan = _plan((_scene(0, 0, 150), _scene(1, 150, 300)))

        result = insert_cards(plan, title="My Talk", chapters=False)

        assert [s.index for s in result.scenes] == [0, 1, 2]

    def test_a_card_carries_no_asset(self) -> None:
        plan = _plan((_scene(0, 0, 150), _scene(1, 150, 300)))

        card = insert_cards(plan, title="My Talk", chapters=False).scenes[0]

        assert card.asset is None


class TestChapterCards:
    def _long_plan(self, pause: float) -> ScenePlan:
        # Long enough that chapter cards are considered at all.
        frames = int(MIN_AUDIO_FOR_CHAPTERS * 30) + 300
        return _plan(
            (
                _scene(0, 0, 150, first_word=0.0, last_word=4.0),
                _scene(1, 150, frames, first_word=4.0 + pause, last_word=200.0),
            )
        )

    def test_short_audio_gets_no_chapters(self) -> None:
        """A 2s card is longer than the section it would introduce."""
        plan = _plan(
            (
                _scene(0, 0, 150, first_word=0.0, last_word=4.0),
                _scene(1, 150, 300, first_word=10.0, last_word=14.0),
            )
        )

        assert plan_chapter_cards(plan.scenes, 30.0, 10.0) == []

    def test_a_long_pause_makes_a_chapter(self) -> None:
        plan = self._long_plan(pause=CHAPTER_PAUSE_SECONDS + 0.5)

        cards = plan_chapter_cards(
            plan.scenes, 30.0, plan.audio_duration
        )

        assert len(cards) == 1
        assert cards[0].kind == CardKind.CHAPTER

    def test_a_short_pause_makes_no_chapter(self) -> None:
        """A breath is not a chapter break."""
        plan = self._long_plan(pause=0.6)

        assert plan_chapter_cards(plan.scenes, 30.0, plan.audio_duration) == []

    def test_the_label_quotes_the_following_section(self) -> None:
        """A generated summary would be the tool inventing content."""
        plan = self._long_plan(pause=CHAPTER_PAUSE_SECONDS + 0.5)

        cards = plan_chapter_cards(plan.scenes, 30.0, plan.audio_duration)

        assert "first" in cards[0].text

    def test_disabling_gives_no_chapters(self) -> None:
        plan = self._long_plan(pause=CHAPTER_PAUSE_SECONDS + 0.5)

        assert (
            plan_chapter_cards(
                plan.scenes, 30.0, plan.audio_duration, enabled=False
            )
            == []
        )

    def test_scenes_without_words_are_skipped(self) -> None:
        """A hand-written plan may have text but no timings."""
        plan = _plan((_scene(0, 0, 150), _scene(1, 150, 6000)))

        assert plan_chapter_cards(plan.scenes, 30.0, 200.0) == []


class TestCardFrames:
    @pytest.mark.parametrize(
        ("seconds", "fps", "expected"),
        [(3.0, 30.0, 90), (2.0, 30.0, 60), (3.0, 25.0, 75), (0.5, 30.0, 15)],
    )
    def test_frames_land_on_the_grid(
        self, seconds: float, fps: float, expected: int
    ) -> None:
        card = Card(CardKind.TITLE, "x", 0, seconds)

        assert card.frames(fps) == expected

    def test_a_card_is_never_zero_frames(self) -> None:
        assert Card(CardKind.TITLE, "x", 0, 0.001).frames(30.0) >= 1


class TestWordTimingsFollowTheCard:
    """Cards shift scenes, and the words must move with them (D-110)."""

    def _plan_with_words(self) -> ScenePlan:
        return _plan(
            (
                _scene(0, 0, 150, first_word=0.5, last_word=4.5),
                _scene(1, 150, 300, first_word=5.5, last_word=9.5),
            )
        )

    def test_words_shift_with_their_scene(self) -> None:
        plan = self._plan_with_words()

        result = insert_cards(
            plan, title="A Talk", title_seconds=3.0, chapters=False
        )

        scene = next(s for s in result.scenes if not s.is_card)
        assert scene.words[0].start == pytest.approx(3.5)

    def test_no_word_starts_before_its_scene(self) -> None:
        """The symptom: captions firing while the card is still on screen."""
        plan = self._plan_with_words()

        result = insert_cards(plan, title="A Talk", chapters=False)

        for scene in result.scenes:
            if scene.words:
                assert scene.words[0].start >= scene.start_frame / result.fps - 0.01

    def test_word_order_is_preserved(self) -> None:
        plan = self._plan_with_words()

        result = insert_cards(plan, title="A Talk", chapters=False)
        starts = [w.start for s in result.scenes for w in s.words]

        assert starts == sorted(starts)

    def test_no_card_means_no_shift(self) -> None:
        plan = self._plan_with_words()

        result = insert_cards(plan, title="", chapters=False)

        assert result.scenes[0].words[0].start == pytest.approx(0.5)

    def test_word_durations_are_unchanged(self) -> None:
        """A shift moves words; it must not stretch them."""
        plan = self._plan_with_words()

        result = insert_cards(plan, title="A Talk", chapters=False)
        scene = next(s for s in result.scenes if not s.is_card)

        word = scene.words[0]
        assert word.end - word.start == pytest.approx(0.2)
