"""Tests for scene segmentation.

Where cuts land is most of what makes pacing feel deliberate, so these check
the priority order (pause, then sentence, then length) rather than only that
some number of scenes came out.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from voxframe.config.style import PacingStyle
from voxframe.models.transcript import Transcript, Word
from voxframe.segment import SegmentationError, segment_transcript
from voxframe.timeline import FrameGrid, Span


def _transcript(
    specs: list[tuple[str, float, float]], duration: float | None = None
) -> Transcript:
    words = tuple(Word(text=t, start=s, end=e) for t, s, e in specs)
    return Transcript(
        words=words,
        language="en",
        duration=duration or (words[-1].end + 0.2),
        model_id="test",
        audio_sha256="a" * 64,
    )


def _even_speech(count: int, word_length: float = 0.4, gap: float = 0.05) -> Transcript:
    """Evenly spaced words with no pauses or punctuation."""
    specs = []
    time = 0.0
    for index in range(count):
        specs.append((f"word{index}", time, time + word_length))
        time += word_length + gap
    return _transcript(specs)


class TestBasics:
    def test_empty_transcript_rejected(self) -> None:
        transcript = Transcript(
            words=(), language="en", duration=5.0, model_id="t", audio_sha256="a" * 64
        )
        grid = FrameGrid(fps=30.0, audio_duration=5.0)
        with pytest.raises(SegmentationError, match="empty transcript"):
            segment_transcript(transcript, grid)

    def test_scenes_tile_the_timeline(self) -> None:
        transcript = _even_speech(40)
        grid = FrameGrid(fps=30.0, audio_duration=transcript.duration)

        scenes = segment_transcript(transcript, grid)

        assert scenes[0].start_frame == 0
        assert scenes[-1].end_frame == grid.total_frames
        for previous, following in pairwise(scenes):
            assert previous.end_frame == following.start_frame

    def test_durations_sum_exactly(self) -> None:
        """The frame-grid guarantee, at the segmentation level (D-013)."""
        transcript = _even_speech(60)
        grid = FrameGrid(fps=29.97, audio_duration=transcript.duration)

        scenes = segment_transcript(transcript, grid)

        assert sum(s.duration_frames for s in scenes) == grid.total_frames

    def test_scene_indices_sequential(self) -> None:
        scenes = segment_transcript(
            _even_speech(40), FrameGrid(fps=30.0, audio_duration=18.0)
        )
        assert [s.index for s in scenes] == list(range(len(scenes)))


class TestBoundaryPreference:
    def test_cuts_on_pause(self) -> None:
        """A clear pause should be chosen over an arbitrary length cut."""
        transcript = _transcript(
            [
                ("one", 0.0, 0.4), ("two", 0.45, 0.85), ("three", 0.9, 1.3),
                ("four", 1.35, 1.75), ("five", 1.8, 2.2),
                # 1.3 s pause here
                ("six", 3.5, 3.9), ("seven", 3.95, 4.35), ("eight", 4.4, 4.8),
            ],
            duration=5.0,
        )
        grid = FrameGrid(fps=30.0, audio_duration=5.0)
        pacing = PacingStyle(min_scene_seconds=1.5, max_scene_seconds=3.5)

        scenes = segment_transcript(transcript, grid, pacing)

        assert len(scenes) == 2
        # The cut should land inside the pause, not at either end of it.
        boundary = scenes[0].end_seconds(30.0)
        assert 2.2 < boundary < 3.5

    def test_cuts_on_sentence_when_no_pause(self) -> None:
        transcript = _transcript(
            [
                ("Some", 0.0, 0.4), ("words", 0.45, 0.85), ("here.", 0.9, 1.4),
                ("More", 1.5, 1.9), ("words", 1.95, 2.35), ("follow.", 2.4, 2.9),
            ],
            duration=3.2,
        )
        grid = FrameGrid(fps=30.0, audio_duration=3.2)
        pacing = PacingStyle(
            min_scene_seconds=1.0, max_scene_seconds=2.0, pause_threshold_seconds=0.5
        )

        scenes = segment_transcript(transcript, grid, pacing)

        assert len(scenes) >= 2
        assert scenes[0].text.rstrip().endswith("here.")

    def test_abbreviation_does_not_end_sentence(self) -> None:
        """'Dr. Smith' must not become two scenes."""
        transcript = _transcript(
            [
                ("We", 0.0, 0.3), ("met", 0.35, 0.65), ("Dr.", 0.7, 1.0),
                ("Smith", 1.05, 1.5), ("today", 1.55, 2.0),
            ],
            duration=2.3,
        )
        grid = FrameGrid(fps=30.0, audio_duration=2.3)
        pacing = PacingStyle(min_scene_seconds=0.8, max_scene_seconds=1.5)

        scenes = segment_transcript(transcript, grid, pacing)

        for scene in scenes:
            assert not scene.text.rstrip().endswith("Dr.")

    def test_falls_back_to_length(self) -> None:
        """Continuous speech must still be cut, not left as one long scene."""
        transcript = _even_speech(60)
        grid = FrameGrid(fps=30.0, audio_duration=transcript.duration)
        pacing = PacingStyle(min_scene_seconds=2.0, max_scene_seconds=4.0)

        scenes = segment_transcript(transcript, grid, pacing)

        assert len(scenes) > 1
        for scene in scenes[:-1]:
            assert scene.duration_seconds(30.0) <= 4.5  # allow grid rounding


class TestWordAssignment:
    def test_every_word_lands_in_exactly_one_scene(self) -> None:
        transcript = _even_speech(50)
        grid = FrameGrid(fps=30.0, audio_duration=transcript.duration)

        scenes = segment_transcript(transcript, grid)

        assigned = [w.text for s in scenes for w in s.words]
        assert len(assigned) == len(set(assigned)), "a word appears in two scenes"
        assert len(assigned) == transcript.word_count, "a word was dropped"

    def test_boundaries_avoid_word_interiors(self) -> None:
        """A cut inside a word is visible in the output as a clipped caption."""
        transcript = _even_speech(40, word_length=0.5, gap=0.1)
        grid = FrameGrid(fps=30.0, audio_duration=transcript.duration)

        scenes = segment_transcript(transcript, grid)

        for scene in scenes[:-1]:
            boundary = scene.end_seconds(30.0)
            for word in transcript.words:
                if word.start < boundary < word.end:
                    inside = min(boundary - word.start, word.end - boundary)
                    assert inside < 0.05, f"cut {inside:.3f}s into {word.text!r}"


class TestPacingWindow:
    @pytest.mark.parametrize("fps", [24.0, 25.0, 29.97, 30.0, 60.0])
    def test_holds_across_frame_rates(self, fps: float) -> None:
        transcript = _even_speech(80)
        grid = FrameGrid(fps=fps, audio_duration=transcript.duration)

        scenes = segment_transcript(transcript, grid)

        grid.verify([Span(s.start_frame, s.end_frame) for s in scenes])

    def test_single_short_scene_when_audio_is_brief(self) -> None:
        transcript = _transcript([("Hi", 0.0, 0.5)], duration=1.0)
        grid = FrameGrid(fps=30.0, audio_duration=1.0)

        scenes = segment_transcript(transcript, grid)

        assert len(scenes) == 1
        assert scenes[0].duration_frames == grid.total_frames
