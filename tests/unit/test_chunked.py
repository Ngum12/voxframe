"""Chunked transcription for long audio (D-104, D-105).

The chunk plan must cover the whole file exactly: a gap loses speech, an
overlap transcribes it twice, and either shows up as captions drifting against
the audio.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from voxframe.models.transcript import Transcript, Word
from voxframe.transcribe.chunked import (
    MAX_CHUNK_SECONDS,
    MIN_SECONDS_FOR_CHUNKING,
    SilenceSplit,
    merge_transcripts,
    plan_chunks,
)


def _silences(*points: float, width: float = 0.5) -> list[SilenceSplit]:
    return [SilenceSplit(p - width / 2, p + width / 2) for p in points]


class TestChunkPlanning:
    def test_the_plan_covers_the_whole_file(self) -> None:
        chunks = plan_chunks(880.0, _silences(*range(100, 880, 100)))

        assert chunks[0][0] == 0.0
        assert chunks[-1][1] == pytest.approx(880.0)

    def test_there_are_no_gaps_or_overlaps(self) -> None:
        """A gap loses speech; an overlap transcribes it twice."""
        chunks = plan_chunks(880.0, _silences(*range(100, 880, 100)))

        for (_, end), (start, _) in pairwise(chunks):
            assert end == pytest.approx(start)

    def test_boundaries_land_in_silence(self) -> None:
        """Cutting mid-sentence gives the decoder half a phrase."""
        midpoints = [130.0, 250.0, 370.0]
        chunks = plan_chunks(500.0, _silences(*midpoints))

        internal = [end for _, end in chunks[:-1]]
        for boundary in internal:
            assert any(abs(boundary - m) < 0.01 for m in midpoints)

    def test_no_chunk_exceeds_the_maximum(self) -> None:
        """Beyond the limit the degradation chunking exists to avoid returns."""
        chunks = plan_chunks(900.0, [])

        for start, end in chunks[:-1]:
            assert end - start <= MAX_CHUNK_SECONDS + 0.01

    def test_a_file_with_no_silence_still_chunks(self) -> None:
        """A hard cut is bad; one long chunk is worse."""
        chunks = plan_chunks(900.0, [])

        assert len(chunks) > 1

    def test_short_audio_is_one_chunk(self) -> None:
        chunks = plan_chunks(120.0, _silences(60.0))

        assert len(chunks) == 1
        assert chunks[0] == (0.0, 120.0)

    def test_zero_duration_yields_nothing(self) -> None:
        assert plan_chunks(0.0, []) == []

    def test_the_last_chunk_absorbs_the_remainder(self) -> None:
        """Rather than splitting off a few seconds that transcribe badly."""
        chunks = plan_chunks(400.0, _silences(*range(60, 400, 60)))

        assert chunks[-1][1] == pytest.approx(400.0)
        assert chunks[-1][1] - chunks[-1][0] > 10.0

    def test_a_silence_too_close_to_the_start_is_not_used(self) -> None:
        """A 2-second first chunk would transcribe badly on its own."""
        chunks = plan_chunks(600.0, _silences(3.0, 150.0))

        assert chunks[0][1] > 10.0

    @pytest.mark.parametrize("duration", [300.0, 600.0, 880.0, 3600.0])
    def test_coverage_holds_at_any_length(self, duration: float) -> None:
        chunks = plan_chunks(duration, _silences(*range(90, int(duration), 90)))

        assert chunks[0][0] == 0.0
        assert chunks[-1][1] == pytest.approx(duration)
        assert sum(e - s for s, e in chunks) == pytest.approx(duration)

    def test_the_chunking_threshold_is_above_the_target(self) -> None:
        """Chunking a file into one chunk would be pointless work."""
        assert MIN_SECONDS_FOR_CHUNKING > MAX_CHUNK_SECONDS / 2


class TestMerging:
    def _piece(self, *times: tuple[float, float]) -> Transcript:
        return Transcript(
            words=tuple(
                Word(text=f"w{i}", start=s, end=e)
                for i, (s, e) in enumerate(times)
            ),
            language="en",
            duration=max(e for _, e in times) + 1,
            model_id="test",
            audio_sha256="0" * 64,
        )

    def test_timings_are_offset_by_the_chunk_start(self) -> None:
        """Each chunk knows only its own time base."""
        merged = merge_transcripts(
            [
                (0.0, self._piece((0.0, 1.0))),
                (120.0, self._piece((0.0, 1.0), (2.0, 3.0))),
            ],
            language="en",
            language_probability=1.0,
            duration=240.0,
            model_id="test",
            audio_sha256="0" * 64,
        )

        assert [w.start for w in merged.words] == [0.0, 120.0, 122.0]

    def test_words_stay_in_order(self) -> None:
        """Transcript validation rejects out-of-order words."""
        merged = merge_transcripts(
            [
                (0.0, self._piece((0.0, 1.0), (5.0, 6.0))),
                (100.0, self._piece((0.0, 1.0))),
                (200.0, self._piece((0.0, 1.0))),
            ],
            language="en",
            language_probability=1.0,
            duration=300.0,
            model_id="test",
            audio_sha256="0" * 64,
        )

        starts = [w.start for w in merged.words]
        assert starts == sorted(starts)

    def test_every_word_survives(self) -> None:
        merged = merge_transcripts(
            [
                (0.0, self._piece((0.0, 1.0), (2.0, 3.0))),
                (100.0, self._piece((0.0, 1.0))),
            ],
            language="en",
            language_probability=1.0,
            duration=200.0,
            model_id="test",
            audio_sha256="0" * 64,
        )

        assert merged.word_count == 3

    def test_the_whole_file_hash_is_recorded(self) -> None:
        """Not a chunk's hash: the cache key is for the whole recording."""
        merged = merge_transcripts(
            [(0.0, self._piece((0.0, 1.0)))],
            language="en",
            language_probability=0.9,
            duration=200.0,
            model_id="test",
            audio_sha256="a" * 64,
        )

        assert merged.audio_sha256 == "a" * 64
        assert merged.duration == 200.0

    def test_language_confidence_is_carried(self) -> None:
        """A shaky detection must stay shaky after merging (D-061)."""
        merged = merge_transcripts(
            [(0.0, self._piece((0.0, 1.0)))],
            language="en",
            language_probability=0.41,
            duration=200.0,
            model_id="test",
            audio_sha256="0" * 64,
        )

        assert merged.language_probability == pytest.approx(0.41)


class TestSilenceSplit:
    def test_the_midpoint_is_furthest_from_speech(self) -> None:
        assert SilenceSplit(10.0, 12.0).midpoint == 11.0

    def test_duration_is_reported(self) -> None:
        assert SilenceSplit(10.0, 12.5).duration == pytest.approx(2.5)
