"""Finding cuts, hooks, stressed words and stillness (D-199)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from voxframe.plan.pace import CutKind, PaceEdits
from voxframe.plan.pace_finder import find_cuts, find_hooks, find_stillness, find_stressed_words
from voxframe.plan.projection import project
from voxframe.plan.scene_plan import Footage, PlannedScene, PlanWord, ScenePlan, Shot

FPS = 30.0


def _plan(spec: list[tuple[str, float, float]], seconds: float = 10.0, **changes: object) -> ScenePlan:
    words = tuple(PlanWord(text=t, start=s, end=e) for t, s, e in spec)
    frames = round(seconds * FPS)
    return ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64, audio_duration=seconds, fps=FPS,
        total_frames=frames,
        scenes=(PlannedScene(index=0, start_frame=0, end_frame=frames, text="x", words=words,
                             shot=Shot.SPEAKER, footage_start=0.0),),
        footage=Footage(path="v.mp4", width=1280, height=720, fps=30.0, duration=seconds),
        **changes,  # type: ignore[arg-type]
    )


class TestCuts:
    def test_a_long_pause_is_trimmed_to_a_breath(self) -> None:
        plan = _plan([("Hello", 0.1, 0.5), ("there", 1.7, 2.0)], seconds=2.2)
        (cut,) = find_cuts(plan, edges=False)
        assert cut.kind is CutKind.SILENCE
        assert (cut.start, cut.end) == pytest.approx((0.625, 1.575))
        assert cut.label == "1.2 s pause"

    def test_a_short_pause_is_left(self) -> None:
        plan = _plan([("Hello", 0.1, 0.5), ("there", 0.75, 1.0)], seconds=1.2)
        assert find_cuts(plan, edges=False) == ()

    def test_fillers_and_repeats_go(self) -> None:
        plan = _plan([
            ("So", 0.1, 0.3), ("um", 0.4, 0.6), ("the", 0.7, 0.8), ("the", 0.85, 0.95),
            ("river", 1.0, 1.4), ("rose", 1.45, 1.8),
        ], seconds=2.0)
        cuts = find_cuts(plan, edges=False)
        kinds = {c.kind for c in cuts}
        assert kinds == {CutKind.FILLER, CutKind.REPEAT}
        video = project(plan.model_copy(update={"pace": PaceEdits(cuts=cuts)})).plan
        assert [w.text for w in video.scenes[0].words] == ["So", "the", "river", "rose"]

    def test_a_restarted_phrase_goes(self) -> None:
        plan = _plan([("I", 0.1, 0.2), ("think", 0.25, 0.5), ("I", 0.55, 0.65),
                      ("think", 0.7, 0.95), ("so", 1.0, 1.3)], seconds=1.5)
        (cut,) = find_cuts(plan, edges=False)
        assert cut.kind is CutKind.REPEAT and cut.label == "“I think” said twice"

    def test_the_silent_edges_go(self) -> None:
        plan = _plan([("Hello", 1.0, 1.4)], seconds=3.0)
        cuts = find_cuts(plan)
        assert [(c.kind, c.start) for c in cuts] == [(CutKind.EDGE, 0.0), (CutKind.EDGE, 1.75)]

    def test_numbers_said_twice_are_not_repeats(self) -> None:
        plan = _plan([("10", 0.1, 0.3), ("10", 0.35, 0.6)], seconds=1.0)
        assert find_cuts(plan, edges=False) == ()


class TestHooks:
    def test_a_question_with_a_number_to_you_wins(self) -> None:
        tokens = ("Today we talk about rivers. "
                  "Did you know three cities flooded? "
                  "It was a long week.").split()
        spec = [(t, 0.1 + i * 0.4, 0.4 + i * 0.4) for i, t in enumerate(tokens)]
        hooks = find_hooks(_plan(spec, seconds=8.0))
        assert hooks[0].text == "Did you know three cities flooded?"
        assert set(hooks[0].reasons) >= {"a question", "a number", "talks to you"}
        assert all(not h.text.startswith("Today") for h in hooks)


class TestStressedWords:
    def test_the_loudest_word_is_punched_in_on(self, tmp_path: Path) -> None:
        import soundfile as sf

        rate = 16000
        tokens = ["floods", "covered", "the", "whole", "market", "yesterday", "morning",
                  "everyone", "watched", "closely"]
        spec = [(t, 0.2 + i * 0.5, 0.6 + i * 0.5) for i, t in enumerate(tokens)]
        t = np.arange(int(5.5 * rate)) / rate
        signal = np.zeros_like(t)
        for word, start, end in spec:
            loud = 0.8 if word == "market" else 0.1
            mask = (t >= start) & (t < end)
            signal[mask] = loud * np.sin(2 * np.pi * 220 * t[mask])
        audio = tmp_path / "talk.wav"
        sf.write(audio, signal, rate)
        punches = find_stressed_words(_plan(spec, seconds=5.5), audio)
        assert [p.word for p in punches] == ["market"]


class TestStillness:
    def test_long_stretches_with_nothing_new_are_found(self) -> None:
        plan = _plan([("a", 0.1, 0.3)], seconds=10.0)
        assert [(s.start, s.end) for s in find_stillness(plan)] == [(0.0, 10.0)]
        from voxframe.plan.overlays import Overlay, OverlayKind

        busy = plan.model_copy(update={"overlays": (
            Overlay(id="a", kind=OverlayKind.TEXT, text="x", scene=0, word=0),
        )})
        # A pop-up at 0.1 s splits nothing that matters: still 9.9 s.
        assert [(s.start, s.end) for s in find_stillness(busy)] == [(0.1, 10.0)]
