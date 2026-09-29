"""Tests for the frame grid (D-013).

The headline test is :func:`test_frame_grid_no_drift`, which is the gate on the
"no audio drift" claim in the architecture document. Until it passes, that
claim is not made.
"""

from __future__ import annotations

import pytest

from voxframe.timeline import FrameGrid, GridError, Span


class TestSpan:
    def test_duration_is_half_open(self) -> None:
        assert Span(0, 30).duration_frames == 30

    def test_rejects_negative_start(self) -> None:
        with pytest.raises(GridError, match="start_frame must be >= 0"):
            Span(-1, 10)

    def test_rejects_empty_span(self) -> None:
        with pytest.raises(GridError, match="end_frame must exceed start_frame"):
            Span(10, 10)

    def test_seconds_conversion(self) -> None:
        start, end = Span(30, 90).seconds(30.0)
        assert start == pytest.approx(1.0)
        assert end == pytest.approx(3.0)


class TestFrameGrid:
    def test_rejects_non_positive_fps(self) -> None:
        with pytest.raises(GridError, match="fps must be positive"):
            FrameGrid(fps=0, audio_duration=10)

    def test_rejects_non_positive_duration(self) -> None:
        with pytest.raises(GridError, match="audio_duration must be positive"):
            FrameGrid(fps=30, audio_duration=0)

    def test_total_frames_pinned_to_audio(self) -> None:
        assert FrameGrid(fps=30.0, audio_duration=10.0).total_frames == 300

    def test_total_frames_rounds_up(self) -> None:
        """The video must never be shorter than the audio (D-032).

        Rounding to nearest would give 300 frames (10.0 s) for 10.01 s of
        audio, forcing the renderer to truncate the speaker. Rounding up means
        padding with silence is the only adjustment ever needed.
        """
        assert FrameGrid(fps=30.0, audio_duration=10.01).total_frames == 301
        assert FrameGrid(fps=30.0, audio_duration=10.001).total_frames == 301

    def test_video_always_covers_audio(self) -> None:
        for duration in (5.001, 5.999, 6.5, 22.741):
            grid = FrameGrid(fps=30.0, audio_duration=duration)
            assert grid.total_frames / 30.0 >= duration

    def test_frame_at_rounds_to_nearest(self) -> None:
        grid = FrameGrid(fps=30.0, audio_duration=10.0)
        assert grid.frame_at(0.0) == 0
        assert grid.frame_at(1.0) == 30
        assert grid.frame_at(1.02) == 31  # 30.6 rounds up

    def test_frame_at_clamps(self) -> None:
        grid = FrameGrid(fps=30.0, audio_duration=10.0)
        assert grid.frame_at(-5.0) == 0
        assert grid.frame_at(999.0) == 300

    def test_spans_tile_timeline(self) -> None:
        grid = FrameGrid(fps=30.0, audio_duration=10.0)
        spans = grid.spans_from_boundaries([0.0, 2.5, 5.0, 7.5])
        grid.verify(spans)
        assert spans[0].start_frame == 0
        assert spans[-1].end_frame == 300

    def test_leading_zero_added_when_absent(self) -> None:
        grid = FrameGrid(fps=30.0, audio_duration=10.0)
        spans = grid.spans_from_boundaries([2.5, 5.0])
        assert spans[0].start_frame == 0
        grid.verify(spans)

    def test_no_boundaries_yields_single_span(self) -> None:
        grid = FrameGrid(fps=30.0, audio_duration=10.0)
        spans = grid.spans_from_boundaries([])
        assert spans == [Span(0, 300)]

    def test_rejects_unsorted_boundaries(self) -> None:
        grid = FrameGrid(fps=30.0, audio_duration=10.0)
        with pytest.raises(GridError, match="ascending order"):
            grid.spans_from_boundaries([5.0, 2.0])

    def test_collapsing_boundaries_merge(self) -> None:
        """Boundaries landing on one frame merge rather than creating empty spans."""
        grid = FrameGrid(fps=30.0, audio_duration=10.0)
        spans = grid.spans_from_boundaries([1.000, 1.001, 1.002, 5.0])
        grid.verify(spans)
        assert all(s.duration_frames >= 1 for s in spans)

    def test_boundary_past_audio_end_dropped(self) -> None:
        grid = FrameGrid(fps=30.0, audio_duration=10.0)
        spans = grid.spans_from_boundaries([2.0, 15.0])
        grid.verify(spans)
        assert spans[-1].end_frame == 300


class TestVerify:
    def test_detects_gap(self) -> None:
        grid = FrameGrid(fps=30.0, audio_duration=10.0)
        with pytest.raises(GridError, match="gap or overlap"):
            grid.verify([Span(0, 100), Span(150, 300)])

    def test_detects_overlap(self) -> None:
        grid = FrameGrid(fps=30.0, audio_duration=10.0)
        with pytest.raises(GridError, match="gap or overlap"):
            grid.verify([Span(0, 150), Span(100, 300)])

    def test_detects_wrong_start(self) -> None:
        grid = FrameGrid(fps=30.0, audio_duration=10.0)
        with pytest.raises(GridError, match="must start at frame 0"):
            grid.verify([Span(10, 300)])

    def test_detects_short_timeline(self) -> None:
        grid = FrameGrid(fps=30.0, audio_duration=10.0)
        with pytest.raises(GridError, match="must end at frame 300"):
            grid.verify([Span(0, 299)])


# --- The gate on the no-drift claim (D-013) ---


@pytest.mark.parametrize("fps", [23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0])
def test_frame_grid_no_drift(fps: float) -> None:
    """Total frames match audio duration within one frame over 30 minutes.

    Fractional rates (23.976, 29.97, 59.94) are included deliberately: they are
    where naive per-scene duration rounding fails fastest.
    """
    audio_duration = 30 * 60.0  # 30 minutes
    grid = FrameGrid(fps=fps, audio_duration=audio_duration)

    # ~4.7 s scenes: roughly 380 scenes, enough for drift to show.
    boundaries = [i * 4.7 for i in range(int(audio_duration / 4.7))]
    spans = grid.spans_from_boundaries(boundaries)

    grid.verify(spans)  # raises on any gap, overlap, or length mismatch

    expected = round(audio_duration * fps)
    assert sum(s.duration_frames for s in spans) == expected
    assert abs(spans[-1].end_frame - expected) <= 1


def test_naive_duration_rounding_would_drift() -> None:
    """Demonstrates the bug the frame grid exists to prevent.

    Rounding each scene's *duration* independently accumulates error. This test
    asserts that the naive approach does drift, so that if someone later
    "simplifies" the grid into per-scene rounding, this test fails and explains
    why the complexity was there.
    """
    fps = 29.97
    audio_duration = 30 * 60.0
    scene_length = 4.7
    count = int(audio_duration / scene_length)

    naive_total = sum(round(scene_length * fps) for _ in range(count))
    naive_end = naive_total / fps
    correct_end = round(count * scene_length * fps) / fps

    assert abs(naive_end - correct_end) > 0.1, (
        "Naive rounding did not drift as expected; "
        "if this fails the test's premise needs revisiting."
    )
