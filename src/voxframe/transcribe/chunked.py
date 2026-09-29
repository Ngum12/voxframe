"""Transcribe long audio in chunks, split at silence.

Transcription does not scale linearly. Measured on a 14:41 recording (D-104):

===============  ==========  ============  =============
model            45s clip    14:41 file    degradation
===============  ==========  ============  =============
base             0.12x       0.90x         7.5x
large-v3-turbo   0.92x       ~8x           ~8.7x
===============  ==========  ============  =============

Both models degrade by about the same factor, so the cause is not model size —
it is how a long file is decoded in a single call. Splitting the audio bounds
that cost.

**Where to split matters more than how big the chunks are.** Cutting
mid-sentence gives the decoder half a phrase for context, and the words either
side of the cut come back wrong. Every boundary is placed in silence, found by
FFmpeg's ``silencedetect``, so each chunk starts and ends where the speaker
already stopped.

**Timestamps are offsets, not guesses.** Each chunk is transcribed
independently and knows only its own time base, so every word's timing has the
chunk's start added back. Getting this wrong would desynchronise captions from
the audio, which is the failure the frame grid exists to prevent (D-013).

A file below the threshold is transcribed in one call as before: chunking costs
an extra FFmpeg pass to find the silences, which is not worth it for a clip
that already transcribes in seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from voxframe.models.transcript import Transcript, Word

__all__ = ["SilenceSplit", "find_split_points", "plan_chunks"]

log = structlog.get_logger(__name__)

#: Below this many seconds, transcribe in one call. Chunking costs a
#: silence-detection pass, which is not worth it for a file that already
#: transcribes quickly.
MIN_SECONDS_FOR_CHUNKING = 240.0

#: Target chunk length. Long enough that the decoder has real context and the
#: per-chunk overhead is amortised, short enough to stay in the range where
#: transcription is near real time.
TARGET_CHUNK_SECONDS = 120.0

#: A chunk is never allowed past this, even if no silence is found. Beyond it
#: the degradation this module exists to avoid starts to reappear.
MAX_CHUNK_SECONDS = 180.0

#: Silence quieter than this counts as a gap.
SILENCE_THRESHOLD_DB = -32.0

#: A gap must last this long to be a candidate split point. Shorter than the
#: pause used for scene boundaries: this is looking for somewhere safe to cut,
#: not for a meaningful break.
MIN_SILENCE_SECONDS = 0.30


@dataclass(frozen=True, slots=True)
class SilenceSplit:
    """A window of silence where the audio can be cut.

    Attributes:
        start: When the silence begins, in seconds.
        end: When it ends.
    """

    start: float
    end: float

    @property
    def midpoint(self) -> float:
        """The safest point to cut: furthest from speech on either side."""
        return (self.start + self.end) / 2

    @property
    def duration(self) -> float:
        return self.end - self.start


def find_split_points(
    audio_path: Path,
    ffmpeg_path: str = "",
    *,
    threshold_db: float = SILENCE_THRESHOLD_DB,
    min_silence: float = MIN_SILENCE_SECONDS,
) -> list[SilenceSplit]:
    """Find silences in an audio file.

    Delegates to :func:`~voxframe.render.encode.probe.detect_silences`, because
    subprocess use is confined to the probe and runner modules (D-020) and an
    architecture test enforces it.

    Returns:
        Silences in order. Empty when none are found, which makes the caller
        fall back to fixed-length chunks.
    """
    from voxframe.render.encode.probe import detect_silences

    found = detect_silences(
        audio_path, threshold_db=threshold_db, min_seconds=min_silence
    )

    log.debug(
        "transcribe.silences", audio=audio_path.name, found=len(found)
    )

    return [SilenceSplit(start, end) for start, end in found]


def plan_chunks(
    duration: float,
    silences: list[SilenceSplit],
    *,
    target: float = TARGET_CHUNK_SECONDS,
    maximum: float = MAX_CHUNK_SECONDS,
) -> list[tuple[float, float]]:
    """Choose chunk boundaries, preferring silence.

    Walks forward from the current position looking for the silence closest to
    ``target``. If none falls before ``maximum``, the chunk is cut at
    ``maximum`` regardless — a hard cut mid-word is worse than a chunk long
    enough to be slow, but only just, and a file with no silence at all has to
    be handled somehow.

    Args:
        duration: Total audio length in seconds.
        silences: Candidate split points, in order.
        target: Preferred chunk length.
        maximum: Hard limit on any chunk.

    Returns:
        ``(start, end)`` pairs covering the whole file with no gaps or
        overlaps.
    """
    if duration <= 0:
        return []

    chunks: list[tuple[float, float]] = []
    position = 0.0

    while position < duration:
        remaining = duration - position

        if remaining <= maximum:
            # The last chunk takes whatever is left rather than splitting off
            # a few seconds that would transcribe badly on their own.
            chunks.append((position, duration))
            break

        ideal = position + target
        limit = position + maximum

        # The silence nearest the ideal point, among those we can still reach.
        usable = [
            s for s in silences if position + 10.0 < s.midpoint <= limit
        ]

        if usable:
            best = min(usable, key=lambda s: abs(s.midpoint - ideal))
            cut = best.midpoint
        else:
            # No silence in range. Cutting at the limit is the lesser evil.
            cut = limit
            log.debug(
                "transcribe.chunk.no_silence",
                position=round(position, 1),
                cut_at=round(cut, 1),
            )

        chunks.append((position, cut))
        position = cut

    log.info(
        "transcribe.chunks.planned",
        chunks=len(chunks),
        duration=round(duration, 1),
        mean_seconds=round(duration / max(1, len(chunks)), 1),
    )

    return chunks


def merge_transcripts(
    pieces: list[tuple[float, Transcript]],
    *,
    language: str,
    language_probability: float,
    duration: float,
    model_id: str,
    audio_sha256: str,
) -> Transcript:
    """Stitch chunk transcripts into one, with timings offset.

    Each chunk was transcribed independently and knows only its own time base,
    so every word needs its chunk's start added back. Without this the captions
    of every chunk after the first would fire at the wrong moment.

    Args:
        pieces: ``(offset_seconds, transcript)`` in order.
        language: Language of the whole file.
        language_probability: Detection confidence.
        duration: Total audio length.
        model_id: Which model produced these.
        audio_sha256: Hash of the whole file, not of any chunk.

    Returns:
        One transcript covering the whole recording.
    """
    words: list[Word] = []

    for offset, piece in pieces:
        for word in piece.words:
            words.append(
                Word(
                    text=word.text,
                    start=word.start + offset,
                    end=word.end + offset,
                    probability=word.probability,
                )
            )

    log.info(
        "transcribe.chunks.merged",
        chunks=len(pieces),
        words=len(words),
    )

    return Transcript(
        words=tuple(words),
        language=language,
        language_probability=language_probability,
        duration=duration,
        model_id=model_id,
        audio_sha256=audio_sha256,
    )
