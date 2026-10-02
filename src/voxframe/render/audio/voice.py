"""Voice polish: the biggest quality win for a phone recording (D-173).

On by default, with a switch; with it off the voice is the recording exactly.
Every step is an FFmpeg filter the app already ships, so nothing is added to
download or install, and no model file is needed (the RNNoise models' licence
is unclear, D-172):

1. **Noise reduction** (``afftdn``), set from the recording's own noise floor,
   measured in its pauses, and capped at 12 dB: past that, speech starts to
   sound underwater.
2. **Rumble and plosives** away below 75 Hz (``highpass``).
3. **Presence**, a small lift near 3 kHz; **boxiness** at 250-400 Hz is cut
   only when measured, never by default (``equalizer``).
4. **Sibilance** softened, mildly (``deesser``). The planned strength took
   6.5 dB from a talk's high frequencies -- music under the voice set it off --
   and the milder one 0.06 dB.
5. **Evenness**: gentle compression, 2.5:1 (``acompressor``).
6. **Room tone**: the recording's own quietest stretch, looped under the voice
   at about -60 dBFS, so pauses are never dead digital silence.

Then it is checked, because "never robotic" is measured, not hoped: the pauses
must still hold sound, and the voice must keep its high frequencies.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

if TYPE_CHECKING:
    from voxframe.render.encode.probe import FFmpegCapabilities

__all__ = ["POLISH_VERSION", "PolishReport", "music_underneath", "noise_floor", "polish_voice"]

log = structlog.get_logger(__name__)

#: Bumped whenever the polish changes, so cached polished stems are not reused.
POLISH_VERSION = 3

#: The most noise reduction ever applied, dB.
MAX_REDUCTION_DB = 12.0
#: The least, when there is any noise to speak of.
MIN_REDUCTION_DB = 3.0

#: Room tone's level under the voice, dBFS (RMS).
ROOM_TONE_DB = -60.0
#: How much room tone is taken to loop.
ROOM_TONE_SECONDS = 2.0

#: The pauses must stay above this after polishing: below it they are dead.
DEAD_PAUSE_DB = -75.0
#: The voice may lose at most this much above 4 kHz, relative to 1-4 kHz.
MAX_HIGH_LOSS_DB = 6.0

#: Boxiness is cut when the 250-400 Hz band stands this far above 1-2 kHz.
#: Two ordinary voices measured 13.1 and 14.5 dB (a talk and a phone
#: recording), so a cut at 8 dB would have applied to everyone; 18 dB is a
#: clearly boxy recording. Calibrated on those two only: to revisit with more.
BOXY_DB = 18.0

BLOCK = 1 << 18


#: Below this spectral flatness the sound between words is tonal -- music,
#: a hum, a television -- not noise. Measured: 0.00 under the owner's
#: 17-minute talk (music under the voice), 0.56 for white noise.
TONAL_FLATNESS = 0.2
#: And when it sits closer than this under the speech, reducing it would
#: take the voice with it.
LOUD_BACKGROUND_DB = 20.0
#: Quieter than this, there is too little noise to be worth reducing.
QUIET_ROOM_DB = -65.0
#: Below this flatness the background is plainly tonal, music whatever its
#: level (the talk: 0.00; the sonnet's room: 0.23). The level test alone was
#: fragile: loose word gaps put the talk's music 20.1 dB under its speech.
VERY_TONAL_FLATNESS = 0.05


@dataclass
class PolishReport:
    """What the polish did, and whether it kept the voice natural."""

    noise_floor_db: float
    reduction_db: float
    #: Why noise reduction was left off or reduced, in words; empty when applied.
    reduction_note: str
    boxiness_cut: bool
    room_tone_db: float
    pause_floor_db: float
    high_change_db: float
    #: Whether the recording already has music (or another steady, tonal
    #: sound) under the voice: adding a music track would put two layers
    #: of music together.
    music_in_recording: bool = False

    @property
    def problems(self) -> list[str]:
        found: list[str] = []
        if self.reduction_db > 0 and self.pause_floor_db < DEAD_PAUSE_DB:
            found.append(
                f"The pauses fell to {self.pause_floor_db:.0f} dB after polishing, "
                "close to dead silence."
            )
        if self.high_change_db < -MAX_HIGH_LOSS_DB:
            found.append(
                f"Polishing took {-self.high_change_db:.1f} dB from the voice's "
                "high frequencies; it may sound dull or processed."
            )
        return found

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["problems"] = self.problems
        return data


def _levels(signal: np.ndarray, rate: int, window: float = 0.1) -> np.ndarray:
    size = max(1, int(rate * window))
    usable = len(signal) - len(signal) % size
    if usable <= 0:
        return np.zeros(0)
    frames = signal[:usable].reshape(-1, size).astype(np.float64)
    levels: np.ndarray = 20 * np.log10(np.maximum(np.sqrt(np.mean(frames**2, axis=1)), 1e-9))
    return levels


def _read(path: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf

    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono: np.ndarray = data.mean(axis=1)
    return mono, int(rate)


def noise_floor(signal: np.ndarray, rate: int) -> float:
    """The recording's noise, dBFS: its quiet 100 ms windows, not its digital zeros.

    Stretches of exact silence (the gaps cards add) are left out: they are the
    renderer's, not the room's.
    """
    levels = _levels(signal, rate)
    real = levels[levels > -120]
    if real.size == 0:
        return -90.0
    return float(np.percentile(real, 10))


def _band_db(spectrum: np.ndarray, freqs: np.ndarray, low: float, high: float) -> float:
    band = spectrum[(freqs >= low) & (freqs < high)]
    return float(10 * np.log10(max(float(band.sum()), 1e-20)))


def _spectrum(
    signal: np.ndarray, rate: int, *, seconds: float = 60.0
) -> tuple[np.ndarray, np.ndarray]:
    """Average power spectrum of the voiced parts, from up to ``seconds`` of them."""
    size = 2048
    levels = _levels(signal, rate, window=size / rate)
    loud = np.flatnonzero(levels > levels.max() - 25) if levels.size else np.zeros(0, dtype=int)
    loud = loud[: int(seconds * rate / size)]
    if loud.size == 0:
        return np.zeros(size // 2 + 1), np.fft.rfftfreq(size, 1 / rate)
    frames = np.stack([signal[i * size : (i + 1) * size] for i in loud])
    window = np.hanning(size)
    power = np.mean(np.abs(np.fft.rfft(frames * window, axis=1)) ** 2, axis=0)
    return power, np.fft.rfftfreq(size, 1 / rate)


def _boxy(signal: np.ndarray, rate: int) -> bool:
    power, freqs = _spectrum(signal, rate)
    return _band_db(power, freqs, 250, 400) - _band_db(power, freqs, 1000, 2000) > BOXY_DB


def _high_change(before: np.ndarray, after: np.ndarray, rate: int) -> float:
    """How the voice's energy above 4 kHz moved, relative to 1-4 kHz, dB."""
    b_power, freqs = _spectrum(before, rate)
    a_power, _ = _spectrum(after, rate)

    def tilt(power: np.ndarray) -> float:
        return _band_db(power, freqs, 4000, 10000) - _band_db(power, freqs, 1000, 4000)

    return round(tilt(a_power) - tilt(b_power), 2)


def _room_tone(signal: np.ndarray, rate: int) -> np.ndarray:
    """The quietest real stretch of the recording: its own room."""
    length = int(ROOM_TONE_SECONDS * rate)
    if len(signal) < 2 * length:
        return np.zeros(0, dtype=np.float32)
    step = rate // 10
    best, best_level = 0, math.inf
    for start in range(0, len(signal) - length, step):
        chunk = signal[start : start + length]
        level = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
        if 1e-6 < level < best_level:  # quiet, but not the renderer's digital zeros
            best, best_level = start, level
    if best_level is math.inf:
        return np.zeros(0, dtype=np.float32)
    tone = signal[best : best + length].astype(np.float32).copy()
    # Fade its ends so the loop joins without a click.
    fade = int(0.05 * rate)
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    tone[:fade] *= ramp
    tone[-fade:] *= ramp[::-1]
    return tone


def _bandwidth(signal: np.ndarray, rate: int, size: int) -> float:
    """The highest frequency the recording holds, Hz: its spectrum within 60 dB of its peak."""
    starts = np.linspace(0, max(0, len(signal) - size), num=min(200, max(1, len(signal) // size)))
    frames = [signal[int(s) : int(s) + size] for s in starts]
    frames = [f for f in frames if len(f) == size]
    if not frames:
        return rate / 2
    window = np.hanning(size)
    power = np.mean([np.abs(np.fft.rfft(f * window)) ** 2 for f in frames], axis=0)
    audible = np.nonzero(power > power.max() * 1e-6)[0]
    return float(audible[-1] * rate / size) if audible.size else rate / 2


def background(
    signal: np.ndarray, rate: int, pauses: list[tuple[float, float]] | None
) -> tuple[float, float, float]:
    """What sounds between the words: its level, its flatness, and the speech's level.

    Measured in the pauses the transcript knows about when there are enough of
    them; a talk with few real pauses otherwise has only speech in its
    "quietest" moments (measured: -25.6 dB on a 17-minute talk).
    """
    size = 2048
    frames: list[np.ndarray] = []
    for start, end in pauses or []:
        first, last = int((start + 0.08) * rate), int((end - 0.08) * rate)
        frames.extend(signal[i : i + size] for i in range(first, last - size, size))
    frames = [f for f in frames if float(np.max(np.abs(f))) > 0]  # not the renderer's zeros
    levels_all = _levels(signal, rate)
    speech = float(np.percentile(levels_all[levels_all > -120], 70)) if levels_all.size else -30.0
    if len(frames) < 20:
        quiet = levels_all[levels_all > -120]
        floor = float(np.percentile(quiet, 10)) if quiet.size else -90.0
        cut = [
            signal[i : i + size]
            for i in range(0, len(signal) - size, size)
            if -120
            < 20 * math.log10(max(float(np.sqrt(np.mean(signal[i : i + size] ** 2))), 1e-9))
            <= floor
        ]
        frames = cut
    else:
        # The transcript's gaps are loose: word timings run short, so a gap
        # often holds the ends of words (measured: -26.5 dB in the sonnet's
        # gaps, over a -50 dB room). The quiet end of them is the background;
        # music under a talk plays on through every gap, so it still shows.
        levels = np.array(
            [20 * math.log10(max(float(np.sqrt(np.mean(f**2))), 1e-9)) for f in frames]
        )
        quiet = levels_all[levels_all > -120]
        # And never louder than the recording's own quietest tenth: the gaps
        # can miss the room entirely when there are few of them.
        floor = float(np.percentile(levels, 20))
        if quiet.size:
            floor = min(floor, float(np.percentile(quiet, 10)))
        frames = [f for f, level in zip(frames, levels, strict=True) if level <= floor + 6.0]
        if len(frames) < 10:
            frames = [
                signal[i : i + size]
                for i in range(0, len(signal) - size, size)
                if -120
                < 20 * math.log10(max(float(np.sqrt(np.mean(signal[i : i + size] ** 2))), 1e-9))
                <= floor + 6.0
            ]
    if not frames:
        return floor, 1.0, speech
    spectra = np.array([np.abs(np.fft.rfft(f * np.hanning(size))) ** 2 + 1e-12 for f in frames])
    # From about 470 Hz to 16 kHz, or to where the recording's sound stops: a
    # 16 kHz recording resampled to 48 kHz is empty above 8 kHz, and that empty
    # half would read as tonal (measured: 0.39 at its own rate, near 0 after).
    hz = rate / size
    low, high = round(470 / hz), round(min(16000.0, 0.95 * _bandwidth(signal, rate, size)) / hz)
    band = spectra[:, low : max(high, low + 16)]
    flatness = float(np.median(np.exp(np.mean(np.log(band), axis=1)) / np.mean(band, axis=1)))
    return floor, flatness, speech


def music_underneath(floor: float, flatness: float, speech: float) -> bool:
    """Whether what sounds between the words is music: tonal, and close under the speech."""
    if flatness < VERY_TONAL_FLATNESS and floor > QUIET_ROOM_DB:
        return True
    return flatness < TONAL_FLATNESS and speech - floor < LOUD_BACKGROUND_DB


def _reduction(floor: float, flatness: float, speech: float) -> tuple[float, str]:
    """How much noise reduction, and why not more."""
    if music_underneath(floor, flatness, speech):
        return 0.0, (
            "Music or another steady sound plays under the voice, so noise reduction "
            "was left off: it would damage both."
        )
    if floor < QUIET_ROOM_DB:
        return 0.0, "The recording is already quiet between words; there was no noise to remove."
    # A quiet room gets a light touch; a noisy one the most that stays natural.
    return float(np.clip(floor + 72.0, MIN_REDUCTION_DB, MAX_REDUCTION_DB)), ""


def polish_voice(
    raw: Path,
    output: Path,
    caps: FFmpegCapabilities,
    pauses: list[tuple[float, float]] | None = None,
) -> PolishReport:
    """Polish a voice stem into ``output`` (48 kHz float WAV) and check the result.

    Args:
        pauses: Gaps between words (seconds in the stem), where the noise is
            measured.
    """
    import soundfile as sf

    from voxframe.render.ffpath import run_ffmpeg

    voice, rate = _read(raw)
    floor, flatness, speech = background(voice, rate, pauses)
    reduction, note = _reduction(floor, flatness, speech)
    boxy = _boxy(voice, rate)
    chain = ",".join(
        [
            *(
                [f"afftdn=nr={reduction:.1f}:nf={float(np.clip(floor, -80, -20)):.1f}:tn=1"]
                if reduction > 0
                else []
            ),
            "highpass=f=75",
            "equalizer=f=3000:t=q:w=1:g=2",
            *(["equalizer=f=320:t=q:w=1:g=-2"] if boxy else []),
            "deesser=i=0.2:m=0.3:f=0.5:s=o",
            "acompressor=threshold=0.1:ratio=2.5:attack=20:release=250:makeup=1",
        ]
    )
    filtered = output.with_suffix(".filtered.wav")
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel",
            "error",
            "-i",
            str(raw.resolve()),
            "-af",
            chain,
            "-ar",
            str(rate),
            "-ac",
            "1",
            "-c:a",
            "pcm_f32le",
            "-y",
            str(filtered.resolve()),
        ],
    )

    tone = _room_tone(voice, rate)
    tone_db = ROOM_TONE_DB
    if tone.size:
        level = 20 * math.log10(max(float(np.sqrt(np.mean(tone.astype(np.float64) ** 2))), 1e-9))
        # Never louder than the room really was.
        tone_db = min(ROOM_TONE_DB, level)
        tone *= 10 ** ((tone_db - level) / 20)
    partial = output.with_suffix(".partial.wav")
    with (
        sf.SoundFile(str(filtered)) as source,
        sf.SoundFile(str(partial), "w", samplerate=rate, channels=1, subtype="FLOAT") as sink,
    ):
        position = 0
        for block in source.blocks(blocksize=BLOCK, dtype="float32"):
            block = block.reshape(-1)
            if tone.size:
                index = np.arange(position, position + len(block)) % len(tone)
                block = block + tone[index]
            sink.write(block)
            position += len(block)
    filtered.unlink(missing_ok=True)
    partial.replace(output)

    polished, _ = _read(output)
    report = PolishReport(
        noise_floor_db=round(floor, 1),
        reduction_db=round(reduction, 1),
        reduction_note=note,
        boxiness_cut=boxy,
        room_tone_db=round(tone_db, 1),
        pause_floor_db=round(noise_floor(polished, rate), 1),
        high_change_db=_high_change(voice, polished, rate),
        music_in_recording=music_underneath(floor, flatness, speech),
    )
    log.info("voice.polished", **{k: v for k, v in report.as_dict().items() if k != "problems"})
    return report
