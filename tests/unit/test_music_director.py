"""The music director's decisions, without audio (D-170).

Bars, landing, repeats and the gain curve are pure arithmetic on the track's
analysis and the words' timings; these pin them. The rendered result is
measured in ``tests/integration/test_music_directed_render.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.music.analysis import TrackAnalysis
from voxframe.music.director import (
    DUCK_READY,
    SWELL_MIN_GAP,
    MusicPlan,
    NotDirectable,
    Span,
    gain_curve,
    plan_edits,
    speech_spans,
)

FPS = 30.0


def _track(bars: int, bar: float = 2.0, beats_per_bar: int = 4) -> TrackAnalysis:
    """A steady track: ``bars`` bars of ``bar`` seconds, plus a partial one."""
    downbeats = tuple(i * bar for i in range(bars + 1))
    beat = bar / beats_per_bar
    beats = tuple(i * beat for i in range((bars + 1) * beats_per_bar))
    chroma = tuple(
        tuple(1.0 if note == i % 12 else 0.0 for note in range(12)) for i in range(bars + 1)
    )
    return TrackAnalysis(
        duration=(bars + 0.5) * bar,
        tempo=60 / beat,
        beats=beats,
        downbeats=downbeats,
        beats_per_bar=beats_per_bar,
        bar_energy=tuple(0.0 for _ in downbeats),
        bar_chroma=chroma,
        beat_variation=0.01,
        pulse_clarity=3.0,
    )


def _source_at(plan: MusicPlan, seconds: float) -> float:
    """Which moment of the track plays at ``seconds`` into the video."""
    for piece in plan.pieces:
        if piece.at <= seconds < piece.at + piece.length:
            return piece.source_start + (seconds - piece.at)
    raise AssertionError(f"nothing plays at {seconds}")


def _assert_whole_bars(plan: MusicPlan, analysis: TrackAnalysis) -> None:
    downbeats = set(analysis.downbeats)
    for piece in plan.pieces:
        assert piece.source_start in downbeats, "a cut not on a bar"
        assert piece.source_end in downbeats, "a cut not on a bar"


def _assert_continuous(plan: MusicPlan, video_end: float) -> None:
    for before, after in zip(plan.pieces, plan.pieces[1:], strict=False):
        assert after.at == pytest.approx(before.at + before.length), "a gap or overlap"
    assert plan.pieces[0].at <= 0
    last = plan.pieces[-1]
    assert last.at + last.length >= video_end, "the music stops before the video"


class TestTheEdit:
    def test_a_long_track_lands_a_phrase_on_the_last_word(self) -> None:
        analysis = _track(120)
        landing, end = 61.3, 64.0

        plan = plan_edits(analysis, landing, end)

        at_landing = _source_at(plan, landing)
        bar = analysis.downbeats.index(pytest.approx(at_landing, abs=1e-6))  # type: ignore[arg-type]
        assert bar % 4 == 0, "the landing is not the start of a phrase"
        assert plan.lead_in < 2.0, "the start moved by a bar or more"
        _assert_whole_bars(plan, analysis)
        _assert_continuous(plan, end)

    def test_a_short_track_repeats_whole_phrases(self) -> None:
        analysis = _track(24)  # 48 s of music under a 3-minute talk
        landing, end = 178.4, 181.0

        plan = plan_edits(analysis, landing, end)

        assert plan.repeats >= 3
        _assert_whole_bars(plan, analysis)
        _assert_continuous(plan, end)
        assert _source_at(plan, landing) in analysis.downbeats

    def test_the_intro_and_ending_are_not_looped(self) -> None:
        analysis = _track(24)

        plan = plan_edits(analysis, 178.4, 181.0)

        # What repeats lies between the first phrase (the intro) and the last
        # (the ending). Trimming bars out of the intro to land exactly is a
        # different edit, and allowed.
        starts = [piece.source_start for piece in plan.pieces]
        # The last piece runs on from the final repeat into the ending.
        repeated = [piece for piece in plan.pieces[:-1] if starts.count(piece.source_start) > 1]
        assert repeated
        assert all(piece.source_start >= 4 * 2.0 for piece in repeated)
        assert all(piece.source_end <= (24 - 4) * 2.0 for piece in repeated)

    def test_a_track_too_short_for_phrases_is_refused(self) -> None:
        with pytest.raises(NotDirectable):
            plan_edits(_track(6), 30.0, 32.0)


class TestSpeech:
    def test_words_close_together_are_one_stretch(self) -> None:
        spans = speech_spans([(0.0, 0.4), (0.5, 0.9), (2.0, 2.5)])

        assert [(s.start, s.end) for s in spans] == [(0.0, 0.9), (2.0, 2.5)]


class TestTheTwoTimelinesAgree:
    def test_stretches_are_grouped_once(self) -> None:
        """A gap of 0.6 s less a rounding error merged in one timeline and not
        the other, and every later stretch was measured against the wrong voice."""
        from voxframe.music.director import paired_spans

        source = [(0.0, 1.0), (1.6, 2.0), (3.0, 3.5)]
        shift = 3.1  # a title card
        video = [(a + shift, b + shift) for a, b in source]

        pairs = paired_spans(video, source)

        assert [(round(v.start - shift, 6), round(v.end - shift, 6)) for v, _ in pairs] == [
            (s.start, s.end) for _, s in pairs
        ]


class TestTheGainCurve:
    def _curve(self) -> list[tuple[float, float]]:
        spans = [
            Span(2.0, 6.0, -12.0),
            Span(6.4, 9.0, -14.0),   # a short gap: stays ducked
            Span(12.0, 15.0, -10.0),  # a 3 s pause before it: a swell
        ]
        return gain_curve(spans, video_end=18.0, landing=15.0, fps=FPS)

    @staticmethod
    def _at(curve: list[tuple[float, float]], seconds: float) -> float:
        import numpy as np

        times, levels = zip(*curve, strict=True)
        return float(np.interp(seconds, times, levels))

    def test_ducked_under_speech(self) -> None:
        curve = self._curve()

        assert self._at(curve, 4.0) == pytest.approx(-12.0)
        assert self._at(curve, 13.0) == pytest.approx(-10.0)

    def test_ready_before_the_first_syllable(self) -> None:
        curve = self._curve()

        assert self._at(curve, 12.0 - DUCK_READY) == pytest.approx(-10.0)

    def test_a_short_gap_does_not_swell(self) -> None:
        assert self._at(self._curve(), 6.2) <= -12.0

    def test_a_long_pause_swells(self) -> None:
        curve = self._curve()

        assert 12.0 - 9.0 >= SWELL_MIN_GAP
        assert self._at(curve, 10.5) == pytest.approx(0.0)

    def test_full_before_the_first_word(self) -> None:
        assert self._at(self._curve(), 0.5) == pytest.approx(0.0)

    def test_every_change_is_on_a_frame(self) -> None:
        for seconds, _ in self._curve():
            assert seconds * FPS == pytest.approx(round(seconds * FPS), abs=1e-6)


class TestFallsBackPlainly:
    """Music is never the reason a video fails (D-170)."""

    def _call(self, monkeypatch: pytest.MonkeyPatch, raise_: BaseException | None) -> tuple:  # type: ignore[type-arg]
        from voxframe.music import director
        from voxframe.render.audio.music import MusicSettings
        from voxframe.render.compose import from_plan

        def direct(*args: object, **kwargs: object) -> object:
            assert raise_ is not None
            raise raise_

        monkeypatch.setattr(director, "direct_music", direct)
        music = MusicSettings(path=Path("track.mp3"))
        return from_plan._directed_bed(None, music, Path("a"), None, Path("w"), Path("c"))  # type: ignore[arg-type]

    def test_an_unsteady_track_is_explained(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bed, note = self._call(monkeypatch, NotDirectable("its tempo is not steady"))

        assert bed is None
        assert "simple loop" in note and "tempo is not steady" in note

    def test_an_unexpected_failure_still_renders(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bed, note = self._call(monkeypatch, RuntimeError("boom"))

        assert bed is None and "simple loop" in note

    def test_simple_mode_skips_the_director(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from voxframe.render.audio.music import MusicSettings
        from voxframe.render.compose import from_plan

        music = MusicSettings(path=Path("track.mp3"), directed=False)

        assert from_plan._directed_bed(None, music, Path("a"), None, Path("w"), Path("c")) == (None, "")  # type: ignore[arg-type]
