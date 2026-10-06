"""Only reviewed lexical cues remove words, while all source clocks survive."""

import pytest

from voxframe.plan.pacing import apply_cuts, review_cuts, suggest_speech_cuts
from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan


def speech(text="Um, I think I think we can euh win.", fps=30):
    words = tuple(PlanWord(text=word, start=.4 + i * .5, end=.7 + i * .5)
                  for i, word in enumerate(text.split()))
    scene = PlannedScene(index=0, start_frame=0, end_frame=round(10 * fps),
        text=text, words=words, footage_start=2, audio_start=2, caption_emphasis=(8,))
    return ScenePlan(audio_path="source.wav", audio_sha256="0" * 64, audio_duration=10,
                     fps=fps, total_frames=round(10 * fps), scenes=(scene,))


@pytest.mark.parametrize("fps", [24, 25, 29.97, 30, 60])
def test_review_removes_only_the_quoted_words_and_retains_source_clocks(fps):
    original = speech(fps=fps)
    cuts = suggest_speech_cuts(original)
    assert [c["kind"] for c in cuts] == ["filler", "repeat", "filler"]
    assert [c["removed_text"] for c in cuts] == ["Um,", "I think", "euh"]
    changed = apply_cuts(original, tuple(c["id"] for c in cuts))
    assert [w.text for s in changed.scenes for w in s.words] == "I think we can win.".split()
    assert changed.total_frames == original.total_frames - sum(c["end_frame"] - c["start_frame"] for c in cuts)
    assert all(s.audio_start == s.footage_start for s in changed.scenes)
    removed_before = 0
    for scene in changed.scenes:
        source_start = scene.audio_start - 2
        removed_before = sum(c["seconds"] for c in cuts if c["end"] <= source_start + 1e-6)
        assert scene.start_frame / fps == pytest.approx(source_start - removed_before)
    assert ScenePlan.model_validate_json(changed.model_dump_json()) == changed
    assert original.scenes[0].text.startswith("Um,")
    assert not suggest_speech_cuts(changed)


@pytest.mark.parametrize("text", ["You know this matters.", "It is very very good.",
    "I think. I think this works.", "I mean that sincerely.", "Non non c'est bon."])
def test_ambiguous_discourse_words_and_emphasis_are_not_offered(text):
    assert not suggest_speech_cuts(speech(text))


def test_corrected_captions_cards_missing_times_and_single_word_are_guarded():
    base = speech()
    for update in ({"caption_text": "Corrected caption"}, {"card_kind": "chapter", "card_text": "Title"},
                   {"words": ()}):
        assert not suggest_speech_cuts(base.model_copy(update={"scenes": (
            base.scenes[0].model_copy(update=update),)}))
    assert not suggest_speech_cuts(speech("um"))


def test_rounding_cannot_clip_a_neighboring_word():
    base = speech("hello um world")
    words = (PlanWord(text="hello", start=.4, end=.905),
             PlanWord(text="um", start=.91, end=1.205),
             PlanWord(text="world", start=1.21, end=1.7))
    assert not suggest_speech_cuts(base.model_copy(update={"scenes": (
        base.scenes[0].model_copy(update={"words": words}),)}))


def test_long_pause_and_scene_boundary_are_not_inferred_as_a_restart():
    base = speech("I think I think we win")
    words = tuple(w.model_copy(update={"start": w.start + (2 if i >= 2 else 0),
                                      "end": w.end + (2 if i >= 2 else 0)})
                  for i, w in enumerate(base.scenes[0].words))
    assert not suggest_speech_cuts(base.model_copy(update={"scenes": (
        base.scenes[0].model_copy(update={"words": words}),)}))
    assert all(c["kind"] in {"pause", "filler", "repeat"} for c in review_cuts(base))
