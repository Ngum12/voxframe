"""The score's sampled instruments, read from the sample folder (D-176).

Instruments are described in ``instruments.toml``. Recordings are loaded the
first time a note needs them, resampled to 48 kHz, and kept while memory
allows, so a long talk never holds every sample at once.

Each instrument's recordings are brought to a common level (the median of
their first half second), keeping the differences between its strengths.
"""

from __future__ import annotations

import math
import re
import tomllib
from collections import OrderedDict
from dataclasses import dataclass
from fractions import Fraction
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["INSTRUMENTS_FILE", "RATE", "SampleLibrary", "SamplesMissing", "envelope"]

RATE = 48000
INSTRUMENTS_FILE = Path(__file__).with_name("instruments.toml")
NOTE = {
    "C": 0,
    "C#": 1,
    "D": 2,
    "D#": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "G": 7,
    "G#": 8,
    "A": 9,
    "A#": 10,
    "B": 11,
}

#: How much decoded audio is kept in memory at most, bytes.
CACHE_BYTES = 512 * 1024 * 1024
#: The longest a one-shot recording is read, seconds: no note needs more.
ONE_SHOT_SECONDS = 15.0
#: Every recording is brought to this level (RMS of its first half second).
LEVEL = 0.1


class SamplesMissing(RuntimeError):
    """The sample folder lacks an instrument the score needs."""


@dataclass(frozen=True)
class _File:
    path: Path
    midi: int  # pitched: its note; hits: 0
    layer: int


def envelope(audio: np.ndarray, attack: float, length: float, release: float) -> None:
    """Shape a note in place: a rise over ``attack``, then a fall after ``length``."""
    n = len(audio)
    a = min(n, max(1, int(attack * RATE)))
    audio[:a] *= (np.sin(np.linspace(0, np.pi / 2, a, dtype=np.float32)) ** 2)[:, None]
    start = min(n, int(length * RATE))
    if n > start:
        audio[start:] *= (np.cos(np.linspace(0, np.pi / 2, n - start, dtype=np.float32)) ** 2)[
            :, None
        ]


def _extend(audio: np.ndarray, need: int) -> np.ndarray:
    """A sustained recording made longer by crossfading its steady middle into itself."""
    start, stop = int(1.2 * RATE), len(audio) - int(0.6 * RATE)
    body = audio[start:stop]
    if len(body) < RATE // 2:
        return audio
    fade = min(int(0.8 * RATE), len(body) // 3)
    rise = np.sin(np.linspace(0, np.pi / 2, fade, dtype=np.float32))[:, None]
    out = audio[:stop]
    while len(out) < need:
        joined = out[-fade:] * rise[::-1] + body[:fade] * rise
        out = np.concatenate([out[:-fade], joined, body[fade:]])
    return out


def _shift(audio: np.ndarray, semitones: int) -> np.ndarray:
    from scipy import signal

    if not semitones:
        return audio
    ratio = Fraction(2 ** (semitones / 12)).limit_denominator(240)
    shifted: np.ndarray = signal.resample_poly(audio, ratio.denominator, ratio.numerator, axis=0)
    return shifted.astype(np.float32)


class _Instrument:
    def __init__(self, name: str, spec: dict[str, Any], root: Path) -> None:
        self.name = name
        self.kind = spec.get("kind", "pitched")
        self.folder = root / spec["folder"]
        self.tuned = bool(spec.get("tuned", False))
        self.loops = self.kind == "pitched"
        if not self.folder.is_dir():
            raise SamplesMissing(f"the {name} samples are not in {self.folder}")
        layer_re = re.compile(spec["layer"]) if "layer" in spec else None
        words: dict[str, int] = dict(spec.get("layers", {}))
        default_word_layer = sorted(words.values())[len(words) // 2] if words else 1
        files = []
        for path in sorted(self.folder.glob("*.wav")):
            if layer_re is not None:
                found = layer_re.search(path.name)
                layer = int(found.group(1)) if found else 1
            elif words:
                token = re.sub(r"\d+$", "", path.stem.rsplit("_", 1)[-1])
                layer = words.get(token, default_word_layer)
            else:
                layer = 1
            midi = 0
            if self.kind == "pitched":
                note = re.search(r"_([A-G]#?)(-?\d)(?=_|\.|$)", path.name)
                if note is None:
                    raise SamplesMissing(f"{path.name}: no note in the file name")
                midi = (
                    12 * (int(note.group(2)) + 1) + NOTE[note.group(1)] + int(spec.get("octave", 0))
                )
            files.append(_File(path, midi, layer))
        if not files:
            raise SamplesMissing(f"the {name} folder {self.folder} has no recordings")
        self.files = tuple(files)
        self.octave = int(spec.get("octave", 0))

    @cached_property
    def gain(self) -> float:
        """Brings the set to LEVEL: the median RMS of every recording's first half second."""
        import soundfile as sf

        levels = []
        for f in self.files:
            data, _ = sf.read(str(f.path), frames=22050, dtype="float32", always_2d=True)
            levels.append(float(np.sqrt(np.mean(data.astype(np.float64) ** 2))))
        return LEVEL / max(float(np.median(levels)), 1e-6)

    @cached_property
    def pitches(self) -> tuple[float, ...]:
        """A tuned drum's pitch: its strongest partial between 60 and 250 Hz."""
        import soundfile as sf

        found = []
        for f in self.files:
            data, rate = sf.read(str(f.path), frames=66150, dtype="float32", always_2d=True)
            mono = data.mean(axis=1)[int(0.1 * rate) : int(1.5 * rate)]
            spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
            freqs = np.fft.rfftfreq(len(mono), 1 / rate)
            band = (freqs > 60) & (freqs < 250)
            found.append(69 + 12 * math.log2(float(freqs[band][np.argmax(spectrum[band])]) / 440))
        return tuple(found)


class SampleLibrary:
    """Every instrument in ``instruments.toml``, read from ``root`` on demand."""

    def __init__(self, root: Path, spec_file: Path = INSTRUMENTS_FILE) -> None:
        self.root = root
        spec = tomllib.loads(spec_file.read_text(encoding="utf-8"))["instruments"]
        self._specs: dict[str, dict[str, Any]] = spec
        self._instruments: dict[str, _Instrument] = {}
        self._cache: OrderedDict[tuple[str, int, int, bool], np.ndarray] = OrderedDict()
        self._cached_bytes = 0

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def instrument(self, name: str) -> _Instrument:
        if name not in self._instruments:
            if name not in self._specs:
                raise SamplesMissing(f"no instrument called {name!r} in instruments.toml")
            self._instruments[name] = _Instrument(name, self._specs[name], self.root)
        return self._instruments[name]

    def check(self, names: tuple[str, ...] | None = None) -> None:
        """Raise SamplesMissing unless every instrument (or those named) is present."""
        for name in names or self.names():
            self.instrument(name)

    def _audio(self, inst: _Instrument, index: int, semitones: int, full: bool) -> np.ndarray:
        key = (inst.name, index, semitones, full)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if semitones:
            audio = _shift(self._audio(inst, index, 0, full), semitones)
        else:
            import soundfile as sf
            from scipy import signal

            info = sf.info(str(inst.files[index].path))
            frames = -1 if full else int(ONE_SHOT_SECONDS * info.samplerate)
            data, rate = sf.read(
                str(inst.files[index].path), frames=frames, dtype="float32", always_2d=True
            )
            data = data[:, :2] if data.shape[1] >= 2 else np.repeat(data, 2, axis=1)
            if rate != RATE:
                step = Fraction(RATE, rate)
                data = signal.resample_poly(data, step.numerator, step.denominator, axis=0)
            audio = np.ascontiguousarray(data * inst.gain, dtype=np.float32)
        self._cache[key] = audio
        self._cached_bytes += audio.nbytes
        while self._cached_bytes > CACHE_BYTES and len(self._cache) > 1:
            _, dropped = self._cache.popitem(last=False)
            self._cached_bytes -= dropped.nbytes
        return audio

    def note(
        self,
        name: str,
        midi: int,
        length: float,
        gain: float,
        *,
        layer: int = 0,
        attack: float = 0.005,
        release: float = 0.4,
        loop: bool = False,
        take: int = 0,
    ) -> np.ndarray:
        """A pitched note: the nearest recording (preferring ``layer``), retuned and shaped."""
        inst = self.instrument(name)

        def distance(f: _File) -> int:
            return abs(f.midi - midi) + (0 if not layer or f.layer == layer else 20)

        best = min(distance(f) for f in inst.files)
        candidates = [i for i, f in enumerate(inst.files) if distance(f) == best]
        index = candidates[take % len(candidates)]
        audio = self._audio(inst, index, midi - inst.files[index].midi, full=loop)
        need = int((length + release) * RATE)
        if loop and len(audio) < need:
            audio = _extend(audio, need)
        out = audio[:need].copy()
        envelope(out, attack, length, release)
        result: np.ndarray = out * np.float32(gain)
        return result

    def hit(
        self,
        name: str,
        strength: float,
        gain: float,
        *,
        pick: str = "",
        target: tuple[int, ...] = (),
        take: int = 0,
    ) -> np.ndarray:
        """A one-shot: a recording of about ``strength`` (0 to 1), retuned if the set is tuned."""
        inst = self.instrument(name)
        named = [i for i, f in enumerate(inst.files) if not pick or pick in f.path.name] or list(
            range(len(inst.files))
        )
        layers = sorted({inst.files[i].layer for i in named})
        want = layers[min(len(layers) - 1, int(strength * len(layers)))]
        choices = [i for i in named if inst.files[i].layer == want] or named
        semitones = 0
        if target and inst.tuned:
            pitches = inst.pitches

            def shift(i: int) -> int:
                pc = round(pitches[i]) % 12
                return min(((t - pc + 6) % 12 - 6 for t in target), key=abs)

            closest = min(abs(shift(i)) for i in choices)
            choices = [i for i in choices if abs(shift(i)) == closest]
            index = choices[take % len(choices)]
            semitones = shift(index)
        else:
            index = choices[take % len(choices)]
        result: np.ndarray = self._audio(inst, index, semitones, full=False) * np.float32(gain)
        return result
