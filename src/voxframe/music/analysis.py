"""What a music track is made of: beats, bars, phrases, energy (D-170).

librosa only (ISC), no model: beat tracking from the onset envelope, then bars
from the beats. librosa does not find downbeats, so the bar's first beat and
the metre (3 or 4 beats a bar) are chosen from the accents themselves: the
beat that carries the most low-frequency energy and onset strength, bar after
bar, is where bars start. Whether that is good enough is for the owner's
listening test to say; beat_this is the named alternative (the plan, decision
5).

A track without a steady pulse -- rubato piano, ambient pads -- is reported as
such (``steady`` false) rather than cut on bars that are not there. The caller
then falls back to the plain looped bed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import structlog

__all__ = ["ANALYSIS_VERSION", "TrackAnalysis", "analyse_track"]

log = structlog.get_logger(__name__)

#: Bumped whenever the analysis changes, so cached results are not reused.
ANALYSIS_VERSION = 7

#: librosa's usual rate for rhythm analysis: enough for beats, a quarter of
#: the memory of 44.1 kHz stereo.
ANALYSIS_RATE = 22050
HOP = 512

#: Below this many beats there is nothing to build bars from.
MIN_BEATS = 16

#: A pulse is steady when beat intervals vary by less than this share of their
#: mean.
MAX_BEAT_VARIATION = 0.10

#: And when the local tempo varies by less than this. Measured: 0.00 on a
#: generated steady track, 0.22 on one whose beats wander by up to 40%, and
#: 0.05-0.11 on the owner's three tracks (worship, hymns, a song).
MAX_TEMPO_VARIATION = 0.15

#: And when onsets on the beat stand out from the rest by at least this ratio.
MIN_PULSE_CLARITY = 1.15

#: Bars per phrase. Four is the overwhelming convention in the popular and
#: library music a person is likely to use; a phrase is where a change of
#: section can happen without sounding like a mistake.
BARS_PER_PHRASE = 4


@dataclass(frozen=True)
class TrackAnalysis:
    """The structure the director cuts on.

    Times are seconds into the file.
    """

    duration: float
    tempo: float
    beats: tuple[float, ...]
    #: Start of every bar, the first beat of each.
    downbeats: tuple[float, ...]
    beats_per_bar: int
    #: Loudness of each bar, dB relative to the loudest bar.
    bar_energy: tuple[float, ...]
    #: Mean chroma of each bar, for choosing loop points that sound alike.
    bar_chroma: tuple[tuple[float, ...], ...]
    beat_variation: float
    pulse_clarity: float
    #: How much the local tempo moves through the track (coefficient of
    #: variation, octave errors folded). The beat tracker forces an even grid
    #: onto anything, so its own beats always look regular; this is what shows
    #: a track with no steady pulse for what it is.
    tempo_variation: float = 0.0
    version: int = ANALYSIS_VERSION

    @property
    def steady(self) -> bool:
        """Whether there is a pulse regular enough to cut on."""
        return (
            len(self.beats) >= MIN_BEATS
            and len(self.downbeats) >= 2 * BARS_PER_PHRASE
            and self.beat_variation <= MAX_BEAT_VARIATION
            and self.tempo_variation <= MAX_TEMPO_VARIATION
            and self.pulse_clarity >= MIN_PULSE_CLARITY
        )

    @property
    def why_not_steady(self) -> str:
        if len(self.beats) < MIN_BEATS or len(self.downbeats) < 2 * BARS_PER_PHRASE:
            return "too few beats were found to cut on"
        if self.beat_variation > MAX_BEAT_VARIATION or self.tempo_variation > MAX_TEMPO_VARIATION:
            return "its tempo is not steady"
        if self.pulse_clarity < MIN_PULSE_CLARITY:
            return "it has no clear beat"
        return ""

    @property
    def bar_seconds(self) -> float:
        if len(self.downbeats) < 2:
            return 0.0
        return float(np.median(np.diff(self.downbeats)))


def analyse_track(
    path: Path, *, limit_seconds: float | None = None, cache_dir: Path | None = None
) -> TrackAnalysis:
    """Beats, bars and energy of a track, cached per file content.

    Args:
        path: The track.
        limit_seconds: Analyse only this much from the start. An hour-long
            track under a ten-minute talk needs only its first ten minutes.
        cache_dir: Where results are kept; ``None`` disables the cache.
    """
    key = _cache_key(path, limit_seconds)
    cached = cache_dir / f"{key}.json" if cache_dir is not None else None
    if cached is not None and cached.is_file():
        try:
            data = json.loads(cached.read_text(encoding="utf-8"))
            if data.get("version") == ANALYSIS_VERSION:
                return _from_json(data)
        except (OSError, ValueError, TypeError):
            pass

    librosa = _librosa()

    signal, rate = librosa.load(
        str(path), sr=ANALYSIS_RATE, mono=True, duration=limit_seconds
    )
    result = _analyse(signal, int(rate))
    log.info(
        "music.analysed",
        track=path.name,
        seconds=round(result.duration, 1),
        tempo=round(result.tempo, 1),
        bars=len(result.downbeats),
        beats_per_bar=result.beats_per_bar,
        steady=result.steady,
        variation=round(result.beat_variation, 3),
        clarity=round(result.pulse_clarity, 2),
    )
    if cached is not None:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(asdict(result)), encoding="utf-8")
    return result


def _analyse(signal: np.ndarray, rate: int) -> TrackAnalysis:
    librosa = _librosa()

    duration = len(signal) / rate
    onset = librosa.onset.onset_strength(y=signal, sr=rate, hop_length=HOP)
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset, sr=rate, hop_length=HOP, trim=False
    )
    beat_frames = np.asarray(beat_frames, dtype=int)
    tracked = librosa.frames_to_time(beat_frames, sr=rate, hop_length=HOP)
    # The tracker works in 23 ms frames, so its beats sit up to a frame either
    # side of the note: measured -43 to +39 ms on a track with known bars.
    # Each beat moves to where the sound's energy rises fastest nearby,
    # measured in 2 ms steps, so a cut lands just before the attack.
    beats = _refine(signal, rate, tracked)

    if len(beats) < 3:
        return TrackAnalysis(
            duration=duration, tempo=float(np.atleast_1d(tempo)[0]), beats=tuple(beats),
            downbeats=(), beats_per_bar=4, bar_energy=(), bar_chroma=(),
            beat_variation=1.0, pulse_clarity=0.0, tempo_variation=1.0,
        )

    # Steadiness from the tracker's own grid: the refinement follows the
    # player's small timing variations, which are music, not unsteadiness.
    intervals = np.diff(tracked)
    variation = float(np.std(intervals) / max(np.mean(intervals), 1e-6))
    tempo_variation = _tempo_variation(librosa, onset, rate, float(np.atleast_1d(tempo)[0]))
    on_beat = onset[np.clip(beat_frames, 0, len(onset) - 1)]
    clarity = float(np.mean(on_beat) / max(float(np.mean(onset)), 1e-6))

    # Accent per beat: onset strength plus low-frequency energy (the kick and
    # the bass usually mark the bar).
    mel = librosa.feature.melspectrogram(y=signal, sr=rate, hop_length=HOP, n_mels=64, fmax=4000)
    low = mel[:6].sum(axis=0)
    low = low / max(float(low.max()), 1e-9)
    strength = onset / max(float(onset.max()), 1e-9)
    frames = np.clip(beat_frames, 0, len(low) - 1)
    accent = strength[frames] + low[frames]

    beats_per_bar, phase = _metre_and_phase(accent)
    downbeats = beats[phase::beats_per_bar]

    rms = librosa.feature.rms(y=signal, hop_length=HOP)[0]
    chroma = librosa.feature.chroma_stft(y=signal, sr=rate, hop_length=HOP)
    times = librosa.frames_to_time(np.arange(len(rms)), sr=rate, hop_length=HOP)
    energy: list[float] = []
    colours: list[tuple[float, ...]] = []
    for start, end in zip(downbeats, [*downbeats[1:], duration], strict=True):
        span = (times >= start) & (times < end)
        level = float(np.sqrt(np.mean(rms[span] ** 2))) if span.any() else 0.0
        energy.append(20 * np.log10(max(level, 1e-6)))
        colour = chroma[:, span].mean(axis=1) if span.any() else np.zeros(12)
        norm = float(np.linalg.norm(colour)) or 1.0
        colours.append(tuple(round(float(v) / norm, 4) for v in colour))
    loudest = max(energy) if energy else 0.0

    return TrackAnalysis(
        duration=duration,
        tempo=float(np.atleast_1d(tempo)[0]),
        beats=tuple(round(float(b), 5) for b in beats),
        downbeats=tuple(round(float(d), 5) for d in downbeats),
        beats_per_bar=beats_per_bar,
        bar_energy=tuple(round(e - loudest, 2) for e in energy),
        bar_chroma=tuple(colours),
        beat_variation=round(variation, 4),
        pulse_clarity=round(clarity, 3),
        tempo_variation=round(tempo_variation, 4),
    )


#: How far a beat may move to reach the attack it belongs to, as a share of
#: the beat. The tracker's tempo can be a fraction off (117.5 against a true
#: 118), so its grid drifts through a track: a fixed 60 ms window lost the
#: beat after a minute. A third of a beat reaches it without reaching the
#: off-beat, half a beat away.
REFINE_SHARE = 0.3
#: The energy envelope's step for that search.
REFINE_STEP = 0.002


def _refine(signal: np.ndarray, rate: int, beats: np.ndarray) -> np.ndarray:
    """Move each beat to the steepest rise in energy within a third of a beat."""
    step = max(1, int(rate * REFINE_STEP))
    window = max(step, int(rate * 0.010))
    interval = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.5
    reach = int(rate * interval * REFINE_SHARE)
    refined = beats.copy()
    for index, beat in enumerate(beats):
        centre = int(beat * rate)
        lo, hi = max(0, centre - reach - step), min(len(signal), centre + reach)
        chunk = signal[lo:hi].astype(np.float64)
        if len(chunk) < 2 * window:
            continue
        # Energy over 10 ms, every 2 ms: shorter windows ripple with a bass
        # note's own cycle (a 55 Hz kick, measured), and the steepest "rise"
        # then lands inside the note instead of at its start.
        smoothed = np.convolve(chunk**2, np.ones(window) / window, mode="valid")[::step]
        energy = np.log10(smoothed + 1e-10)
        if len(energy) < 4:
            continue
        rise = energy[3:] - energy[:-3]
        refined[index] = (lo + (int(np.argmax(rise)) + 3) * step) / rate
    return refined


def _tempo_variation(librosa: Any, onset: np.ndarray, rate: int, tempo: float) -> float:
    """Coefficient of variation of the local tempo, octave errors folded."""
    local = np.asarray(
        librosa.feature.tempo(onset_envelope=onset, sr=rate, hop_length=HOP, aggregate=None),
        dtype=float,
    )
    local = local[local > 0]
    if local.size == 0 or tempo <= 0:
        return 1.0
    # A local estimate of half or double the tempo is the same pulse.
    folded = local * 2.0 ** np.round(np.log2(tempo / local))
    return float(np.std(folded) / np.mean(folded))


def _librosa() -> Any:
    """librosa, imported late: it takes seconds, and only music needs it."""
    import importlib

    return importlib.import_module("librosa")


def _metre_and_phase(accent: np.ndarray) -> tuple[int, int]:
    """Beats per bar (3 or 4) and which beat starts the first bar.

    The metre whose strongest phase stands out most from its other phases
    wins: in 4/4 the accents repeat every four beats, and folding them by
    three smears them out.
    """
    best = (4, 0)
    best_contrast = -1.0
    for metre in (4, 3):
        usable = len(accent) - len(accent) % metre
        if usable < 2 * metre:
            continue
        folded = accent[:usable].reshape(-1, metre).mean(axis=0)
        phase = int(np.argmax(folded))
        others = np.delete(folded, phase)
        contrast = float(folded[phase] - others.mean())
        # 4/4 is far more common; 3/4 must be clearly better to win.
        if metre == 3:
            contrast *= 0.85
        if contrast > best_contrast:
            best, best_contrast = (metre, phase), contrast
    return best


def _cache_key(path: Path, limit_seconds: float | None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    digest.update(f"|{limit_seconds}|{ANALYSIS_VERSION}".encode())
    return digest.hexdigest()[:24]


def _from_json(data: dict[str, object]) -> TrackAnalysis:
    fields = dict(data)
    for name in ("beats", "downbeats", "bar_energy"):
        fields[name] = tuple(fields[name])  # type: ignore[arg-type]
    fields["bar_chroma"] = tuple(tuple(c) for c in fields["bar_chroma"])  # type: ignore[attr-defined]
    return TrackAnalysis(**fields)  # type: ignore[arg-type]
