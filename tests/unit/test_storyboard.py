"""Auditions use the same source edit and direction as the final saved short."""
import pytest

from tests.unit.test_shorts import story
from voxframe.plan.scene_plan import Footage, PlanAsset
from voxframe.plan.shorts import build_short, hook_details, tokens
from voxframe.plan.storyboard import audition, storyboard


@pytest.mark.parametrize("look", ["authority", "energy", "cinema"])
def test_directed_auditions_preserve_source_words_and_return_to_speaker(look: str):
    original = story()
    original = original.model_copy(update={"footage": Footage(
        path="speaker.mp4", width=640, height=360, fps=30, duration=72),
        "scenes": tuple(s.model_copy(update={"asset": PlanAsset(id="visual",
            path="picture.jpg", width=640, height=360, license_name="CC0",
            license_author="Test", license_source="local"), "match_score": .9})
                        for s in original.scenes)})
    plain = build_short(original, 0, 16)
    draft = audition(original, 0, 16, look=look)
    assert draft.total_frames == plain.total_frames
    assert [t.value.text for t in tokens(draft)] == [t.value.text for t in tokens(plain)]
    board = storyboard(draft)
    assert board["has_speaker"]
    assert board["beats"][0]["shot"] == board["beats"][-1]["shot"] == "speaker"
    assert board["beats"][0]["role"] == "opening"
    assert board["beats"][-1]["role"] == "closing"
    assert len(board["beats"]) == len(draft.scenes)
    for beat, scene in zip(board["beats"], draft.scenes, strict=True):
        assert beat["audio_start"] == scene.audio_start
        assert beat["footage_start"] == scene.footage_start
        assert beat["quote"] == " ".join(w.text for w in scene.caption_words())
        assert beat["start"] == scene.start_frame / draft.fps
    assert original.total_frames / original.fps == 72


def test_unselected_direction_retains_existing_edit_and_does_not_claim_speaker():
    plan = story()
    assert audition(plan, 0, 16) == build_short(plan, 0, 16)
    board = storyboard(audition(plan, 0, 16, look="energy"))
    assert not board["has_speaker"]
    assert all(beat["shot"] == "background" for beat in board["beats"])


def test_wordless_lead_in_does_not_hide_the_actual_spoken_hook():
    plan = story()
    plan = plan.model_copy(update={"footage": Footage(
        path="speaker.mp4", width=640, height=360, fps=30, duration=72),
        "scenes": tuple(s.model_copy(update={"asset": PlanAsset(id="visual",
            path="picture.jpg", width=640, height=360, license_name="CC0",
            license_author="Test", license_source="local"), "match_score": .9})
                        for s in plan.scenes)})
    first = next(i for i, token in enumerate(tokens(plan)) if token.scene == 1)
    board = storyboard(audition(plan, first, first + 16, look="energy"))
    assert board["beats"][0]["quote"] == ""  # The natural lead-in is retained.
    spoken = [beat for beat in board["beats"] if beat["quote"]]
    assert spoken[0]["role"] == "opening" and spoken[0]["shot"] == "speaker"
    assert spoken[-1]["role"] == "closing" and spoken[-1]["shot"] == "speaker"


def test_hook_review_quotes_context_and_flags_incomplete_ending():
    plan = story()
    timed = tokens(plan)
    first = next(i for i, t in enumerate(timed) if t.scene == 1)
    detail = hook_details(timed, first, first + 5)
    assert detail["context_before"] == " ".join(t.value.text for t in timed[max(0, first - 24):first])
    assert detail["context_after"] == " ".join(t.value.text for t in timed[first + 6:first + 30])
    assert any("punctuation" in warning for warning in detail["warnings"])
    assert detail["payoff"] in " ".join(t.value.text for t in timed[first:first + 6])
