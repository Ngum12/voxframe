"""What the score follows in the speech: stretches, pauses, and energy (D-176).

Measured on the owner's 17-minute talk, loudness hardly varies (a range of
4.1 LU), so energy is read from how fast the speaker talks as well as how
loud: words a minute over 10 seconds, and the voice's level over 4, each as
a z-score against the talk itself, smoothed over about 10 seconds.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

__all__ = ["HOP", "Speech", "read_speech"]

#: The energy curve's resolution, seconds.
HOP = 0.5


@dataclass(frozen=True)
class Speech:
    words: tuple[tuple[float, float], ...]
    duration: float
    landing: float  # the last word's end: the score resolves here
    spans: tuple[tuple[float, float], ...]
    pauses: tuple[tuple[float, float], ...]  # SWELL_MIN_GAP (1.5 s) or longer
    intensity: tuple[float, ...]  # per HOP seconds, 0 to 1

    def energy_at(self, seconds: float) -> float:
        return self.intensity[min(len(self.intensity) - 1, max(0, int(seconds / HOP)))]

    def powerful_lines(self) -> list[tuple[float, float]]:
        """A short sentence after a long pause: the music drops away for it."""
        return [
            (b0, b1)
            for (_, a1), (b0, b1) in itertools.pairwise(self.spans)
            if b0 - a1 >= 1.2 and b1 - b0 <= 3.5
        ]


def _voice_levels(voice: Path, steps: int) -> np.ndarray:
    """The voice's level around each step, dB: RMS over 4 seconds, read in blocks."""
    import soundfile as sf

    hop = None
    powers: list[float] = []
    with sf.SoundFile(str(voice)) as source:
        hop = int(source.samplerate * HOP)
        for block in source.blocks(blocksize=hop, dtype="float32", always_2d=True):
            powers.append(float(np.mean(block.astype(np.float64) ** 2)) if len(block) else 0.0)
    power = np.array(powers + [0.0] * max(0, steps - len(powers)))[:steps]
    window = int(4.0 / HOP) + 1
    smoothed = np.convolve(power, np.ones(window) / window, mode="same")
    levels: np.ndarray = 10 * np.log10(np.maximum(smoothed, 1e-12))
    return levels


def read_speech(words: Sequence[tuple[float, float]], voice: Path, duration: float) -> Speech:
    """The speech's shape, from its words (video seconds) and its voice stem."""
    from voxframe.music.director import SWELL_MIN_GAP, speech_spans

    words = tuple(sorted((float(s), float(e)) for s, e in words))
    if not words:
        raise ValueError("there is no speech to score")
    spans = tuple((s.start, s.end) for s in speech_spans(words))
    pauses = tuple(
        (a[1], b[0]) for a, b in itertools.pairwise(spans) if b[0] - a[1] >= SWELL_MIN_GAP
    )
    steps = int(duration / HOP) + 1
    centres = np.arange(steps) * HOP
    starts = np.array([s for s, _ in words])
    rate = np.array([np.sum(np.abs(starts - c) < 5.0) * 6.0 for c in centres])  # words a minute
    loud = _voice_levels(voice, steps)

    def z(values: np.ndarray) -> np.ndarray:
        result: np.ndarray = (values - np.median(values)) / (np.std(values) + 1e-6)
        return result

    raw = 0.5 + 0.18 * (z(rate) + z(loud)) / 2
    kernel = np.hanning(21) / np.hanning(21).sum()  # about 10 seconds
    intensity = np.clip(np.convolve(raw, kernel, mode="same"), 0.0, 1.0)
    return Speech(
        words=words,
        duration=duration,
        landing=max(e for _, e in words),
        spans=spans,
        pauses=pauses,
        intensity=tuple(round(float(v), 6) for v in intensity),
    )
