"""Edits to a scene's image (D-127, D-128).

Pure functions over a plan: nothing edited in place, nothing on disk. The rules
they share are pinned here -- a person's choice is theirs, every edit can be
undone the way it was made, and an edit can only ever pick an image the plan
already recorded.
"""

from __future__ import annotations

import pytest

from voxframe.plan.editing import (
    USER,
    EditError,
    choose_image,
    edited_scene_count,
    remove_image,
    use_image,
)
from voxframe.plan.scene_plan import MotionKind, PlanAsset, PlannedScene, ScenePlan


def asset(asset_id: str, similarity: float | None = None) -> PlanAsset:
    return PlanAsset(
        id=asset_id,
        path=f"/library/{asset_id}.jpg",
        width=1600,
        height=900,
        license_name="CC0",
        license_author="Someone",
        license_source="Test",
        similarity=similarity,
    )


def plan(*scenes: PlannedScene) -> ScenePlan:
    return ScenePlan(
        audio_path="a.wav",
        audio_sha256="0" * 64,
        audio_duration=scenes[-1].end_frame / 30,
        fps=30.0,
        total_frames=scenes[-1].end_frame,
        scenes=scenes,
    )


def matched_scene(index: int = 0) -> PlannedScene:
    return PlannedScene(
        index=index,
        start_frame=index * 100,
        end_frame=(index + 1) * 100,
        text="a river through a forest",
        asset=asset("chosen"),
        alternatives=(asset("runner-up", 0.21), asset("third", 0.19)),
        semantic_score=0.24,
        match_score=0.24,
    )


def plain_scene(index: int = 0) -> PlannedScene:
    """A scene that fell back to a plain background, with near misses."""
    return PlannedScene(
        index=index,
        start_frame=index * 100,
        end_frame=(index + 1) * 100,
        text="they had better aim at something high",
        asset=None,
        near_misses=(asset("close", 0.16), asset("closer", 0.165)),
        motion=MotionKind.NONE,
        match_reason="best similarity 0.16 below threshold 0.17",
    )


def title_card() -> PlannedScene:
    return PlannedScene(
        index=0, start_frame=0, end_frame=90, card_kind="title", card_text="A Talk",
        motion=MotionKind.NONE,
    )


class TestUseAnyway:
    """The owner's first request: a near miss, used with one click."""

    def test_a_near_miss_can_be_used(self) -> None:
        edited = choose_image(plan(plain_scene()), 0, "close")

        assert edited.scenes[0].asset is not None
        assert edited.scenes[0].asset.id == "close"

    def test_the_scene_is_marked_as_the_users_choice(self) -> None:
        edited = choose_image(plan(plain_scene()), 0, "close")

        assert edited.scenes[0].asset_source == USER

    def test_the_scene_gets_motion(self) -> None:
        """It was a plain background, which has no camera move."""
        edited = choose_image(plan(plain_scene()), 0, "close")

        assert edited.scenes[0].motion is MotionKind.KEN_BURNS

    def test_the_used_near_miss_is_no_longer_offered(self) -> None:
        edited = choose_image(plan(plain_scene()), 0, "close")

        assert [a.id for a in edited.scenes[0].near_misses] == ["closer"]

    def test_the_similarity_is_kept_on_the_scene(self) -> None:
        """So the Details section can still say how near it was."""
        edited = choose_image(plan(plain_scene()), 0, "close")

        assert edited.scenes[0].semantic_score == pytest.approx(0.16)


class TestChooseAnother:
    def test_a_runner_up_can_be_chosen(self) -> None:
        edited = choose_image(plan(matched_scene()), 0, "runner-up")

        assert edited.scenes[0].asset is not None
        assert edited.scenes[0].asset.id == "runner-up"

    def test_the_replaced_image_becomes_a_runner_up(self) -> None:
        """So choosing it back is one click, the same way it was undone."""
        edited = choose_image(plan(matched_scene()), 0, "runner-up")

        assert "chosen" in [a.id for a in edited.scenes[0].alternatives]

    def test_a_swap_can_be_undone_by_swapping_back(self) -> None:
        once = choose_image(plan(matched_scene()), 0, "runner-up")
        back = choose_image(once, 0, "chosen")

        assert back.scenes[0].asset is not None
        assert back.scenes[0].asset.id == "chosen"

    def test_no_image_is_duplicated_among_the_candidates(self) -> None:
        edited = choose_image(plan(matched_scene()), 0, "runner-up")
        scene = edited.scenes[0]
        ids = [a.id for a in scene.alternatives] + [a.id for a in scene.near_misses]

        assert len(ids) == len(set(ids))
        assert scene.asset is not None
        assert scene.asset.id not in ids


class TestOnlyRecordedCandidates:
    """An edit can never point a scene at an arbitrary file (D-115)."""

    def test_an_unknown_asset_is_refused(self) -> None:
        with pytest.raises(EditError, match="not one of this scene's candidates"):
            choose_image(plan(matched_scene()), 0, "../../etc/passwd")

    def test_another_scenes_candidate_is_refused(self) -> None:
        first = matched_scene(0)
        second = plain_scene(1)

        with pytest.raises(EditError):
            choose_image(plan(first, second), 1, "runner-up")

    def test_a_card_cannot_take_an_image(self) -> None:
        with pytest.raises(EditError, match="cards do not take an image"):
            remove_image(plan(title_card()), 0)

    def test_a_missing_scene_is_refused(self) -> None:
        with pytest.raises(EditError, match="no scene 5"):
            choose_image(plan(matched_scene()), 5, "runner-up")


class TestRemove:
    def test_an_image_can_be_removed(self) -> None:
        edited = remove_image(plan(matched_scene()), 0)

        assert edited.scenes[0].asset is None
        assert edited.scenes[0].motion is MotionKind.NONE

    def test_removing_is_undoable(self) -> None:
        edited = remove_image(plan(matched_scene()), 0)
        restored = choose_image(edited, 0, "chosen")

        assert restored.scenes[0].asset is not None

    def test_removing_nothing_is_refused(self) -> None:
        with pytest.raises(EditError, match="already shows a plain background"):
            remove_image(plan(plain_scene()), 0)


class TestOwnImage:
    def test_an_own_image_is_used(self) -> None:
        own = asset("own-1")

        edited = use_image(plan(plain_scene()), 0, own)

        assert edited.scenes[0].asset == own
        assert edited.scenes[0].asset_source == USER

    def test_the_image_it_replaces_is_kept(self) -> None:
        edited = use_image(plan(matched_scene()), 0, asset("own-1"))

        assert "chosen" in [a.id for a in edited.scenes[0].alternatives]


class TestPurity:
    def test_the_original_plan_is_unchanged(self) -> None:
        original = plan(matched_scene())

        choose_image(original, 0, "runner-up")

        assert original.scenes[0].asset is not None
        assert original.scenes[0].asset.id == "chosen"

    def test_other_scenes_are_untouched(self) -> None:
        original = plan(matched_scene(0), plain_scene(1))

        edited = choose_image(original, 1, "close")

        assert edited.scenes[0] == original.scenes[0]

    def test_the_timeline_is_untouched(self) -> None:
        """Editing an image must never move a scene boundary (D-013)."""
        original = plan(matched_scene(0), plain_scene(1))

        edited = choose_image(original, 1, "close")

        assert [(s.start_frame, s.end_frame) for s in edited.scenes] == [
            (s.start_frame, s.end_frame) for s in original.scenes
        ]

    def test_an_edited_plan_survives_a_round_trip(self, tmp_path) -> None:
        """The edit is saved to disk and read back by the renderer."""
        edited = choose_image(plan(plain_scene()), 0, "close")
        path = tmp_path / "plan.json"

        edited.save(path)
        loaded = ScenePlan.load(path)

        assert loaded.scenes[0].asset is not None
        assert loaded.scenes[0].asset.id == "close"
        assert loaded.scenes[0].asset_source == USER


def test_edited_scenes_are_counted() -> None:
    original = plan(matched_scene(0), plain_scene(1))
    edited = choose_image(original, 1, "close")

    assert edited_scene_count(original) == 0
    assert edited_scene_count(edited) == 1


class TestCaptionCorrection:
    """Correcting what captions say (D-062, D-128)."""

    @staticmethod
    def spoken() -> PlannedScene:
        from voxframe.plan.scene_plan import PlanWord

        # A plan's timeline must start at frame 0 (D-013), so this scene does.
        words = (
            PlanWord(text="Vox", start=0.2, end=0.5),
            PlanWord(text="Frame", start=0.5, end=0.9),
            PlanWord(text="turns", start=1.0, end=1.3),
            PlanWord(text="audio", start=1.4, end=1.8),
        )
        return PlannedScene(
            index=0, start_frame=0, end_frame=60,
            text="Vox Frame turns audio", words=words,
        )

    def test_a_caption_can_be_corrected(self) -> None:
        from voxframe.plan.editing import correct_caption

        edited = correct_caption(plan(self.spoken()), 0, "Voxframe turns audio")

        assert edited.scenes[0].caption_text == "Voxframe turns audio"

    def test_what_was_heard_is_kept(self) -> None:
        """So the person can always see the original next to the correction."""
        from voxframe.plan.editing import correct_caption

        edited = correct_caption(plan(self.spoken()), 0, "Voxframe turns audio")

        assert edited.scenes[0].text == "Vox Frame turns audio"

    def test_a_merge_keeps_real_timings(self) -> None:
        """Two heard words become one, spanning both (D-062)."""
        from voxframe.plan.editing import correct_caption

        edited = correct_caption(plan(self.spoken()), 0, "Voxframe turns audio")
        words = edited.scenes[0].caption_words()

        assert [w.text for w in words] == ["Voxframe", "turns", "audio"]
        assert words[0].start == pytest.approx(0.2)
        assert words[0].end == pytest.approx(0.9)

    def test_corrected_words_stay_inside_the_scene(self) -> None:
        from voxframe.plan.editing import correct_caption

        edited = correct_caption(plan(self.spoken()), 0, "Voxframe really turns audio")
        scene = edited.scenes[0]

        for word in scene.caption_words():
            assert scene.start_frame / 30 - 0.01 <= word.start <= word.end
            assert word.end <= scene.end_frame / 30 + 0.01

    def test_typing_back_what_was_heard_reverts(self) -> None:
        """Revert is the same edit as any other."""
        from voxframe.plan.editing import correct_caption

        once = correct_caption(plan(self.spoken()), 0, "Voxframe turns audio")
        back = correct_caption(once, 0, "Vox  Frame turns   audio")

        assert back.scenes[0].caption_text == ""
        assert not back.scenes[0].is_corrected

    def test_an_empty_caption_is_refused(self) -> None:
        """Almost always an accident; blanking a scene would be worse."""
        from voxframe.plan.editing import correct_caption

        with pytest.raises(EditError, match="cannot be empty"):
            correct_caption(plan(self.spoken()), 0, "   ")

    def test_an_implausibly_long_caption_is_refused(self) -> None:
        from voxframe.plan.editing import correct_caption

        with pytest.raises(EditError, match="longer than"):
            correct_caption(plan(self.spoken()), 0, "word " * 1000)

    def test_a_card_has_no_caption_to_correct(self) -> None:
        from voxframe.plan.editing import correct_caption

        with pytest.raises(EditError):
            correct_caption(plan(title_card()), 0, "New title")

    def test_the_image_is_untouched(self) -> None:
        from voxframe.plan.editing import correct_caption

        original = plan(matched_scene())
        edited = correct_caption(original, 0, "a river running through a forest")

        assert edited.scenes[0].asset == original.scenes[0].asset
