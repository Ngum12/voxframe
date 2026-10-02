"""Rendering a score's events to its group stems, in blocks (D-176, D-179).

A 17-minute talk at 48 kHz is too long to mix in memory with seven buses and
their reverbs, so the timeline is made a block at a time: each event is laid
into the block where it starts and runs on into the next, and each bus's
reverb is a convolution whose tail is carried across blocks.

The score is kept as five group stems -- piano, strings, percussion, bass,
pads -- so the editor's group levels re-mix it without composing again
(Stage 2). Finishing is decided once, on their sum, and applied to each group
alike, so the groups add up to exactly the finished score:

1. brought to a mastered level (-18 dBFS RMS while it plays) under gentle bus
   compression;
2. fitted to the voice: in a long pause it comes up to PAUSE_UNDER_VOICE_DB
   under the voice's typical level, and never closer than PAUSE_CEILING_DB;
3. its speech band (300 Hz to 3.5 kHz) dipped SPEECH_BAND_DIP_DB further
   while words are spoken.

The mix (D-171) then ducks it per stretch of speech to the 15 dB margin and
checks every rule, as for any music.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from voxframe.music.score.compose import Event
from voxframe.music.score.instruments import RATE, SampleLibrary
from voxframe.music.score.synth import impact, pad, riser, room, sub_bass

__all__ = ["GROUPS", "Finish", "finish", "group_of", "render_events"]

BLOCK_SECONDS = 20.0
#: No event sounds longer than this; anything longer is cut.
LONGEST_EVENT = 40.0

#: The editor's instrument groups (D-176 point 5), and the style's volume
#: groups (styles/README.md) that make each up.
GROUPS = ("piano", "strings", "percussion", "bass", "pads")
_STYLE_GROUPS = {
    "keys": "piano",
    "strings": "strings",
    "ostinato": "strings",
    "percussion": "percussion",
    "effects": "percussion",
    "bass": "bass",
    "pads": "pads",
}

#: Each bus's room: seconds, damping, and how much of it is heard.
ROOMS = {
    "keys": (3.0, 0.45, 0.30),
    "strings": (3.8, 0.50, 0.32),
    "ostinato": (2.2, 0.45, 0.18),
    "perc": (4.5, 0.50, 0.34),
    "pad": (4.0, 0.60, 0.25),
    "fx": (4.0, 0.50, 0.40),
}
BUSES = (*ROOMS, "sub")

TARGET_RMS_DB = -18.0
PAUSE_UNDER_VOICE_DB = 6.0
PAUSE_CEILING_DB = 3.0
SPEECH_BAND_DIP_DB = 7.5
PEAK = 0.89
FADE_IN = 1.5
FADE_OUT = 1.0


def group_of(event: Event) -> str:
    """The editor group an event's sound belongs to."""
    return _STYLE_GROUPS.get(event.group, "pads")


def _event_audio(event: Event, library: SampleLibrary) -> np.ndarray:
    if event.kind == "note":
        return library.note(
            event.instrument,
            event.midi,
            event.length,
            event.gain,
            layer=event.layer,
            attack=event.attack,
            release=event.release,
            loop=event.loop,
            take=event.take,
        )
    if event.kind == "hit":
        return library.hit(
            event.instrument,
            event.strength,
            event.gain,
            pick=event.pick,
            target=event.target,
            take=event.take,
        )
    if event.kind == "roll":
        audio = library.hit(event.instrument, 0.5, event.gain, target=event.target, take=event.take)
        n = min(len(audio), max(1, int(event.length * RATE)))
        roll = audio[:n].copy()
        roll *= (np.linspace(0.15, 1.0, n, dtype=np.float32) ** 1.6)[:, None]
        tail = min(n, int(0.04 * RATE))
        roll[-tail:] *= np.linspace(1, 0, tail, dtype=np.float32)[:, None]
        return roll
    if event.kind == "pad":
        return pad(event.notes, event.length, event.gain, event.brightness, event.seed)
    if event.kind == "sub":
        return sub_bass(event.midi, event.length, event.gain)
    if event.kind == "riser":
        return riser(event.length, event.gain, event.seed)
    if event.kind == "impact":
        return impact(event.gain, event.seed)
    raise ValueError(f"unknown event kind {event.kind!r}")


def render_events(
    events: Sequence[Event],
    duration: float,
    library: SampleLibrary,
    outputs: dict[str, Path],
    seed: int,
) -> float:
    """Write each group's raw stem (48 kHz float WAV); return the sum's active RMS."""
    import soundfile as sf
    from scipy import signal

    total = round(duration * RATE)
    block = int(BLOCK_SECONDS * RATE)
    span = block + int(LONGEST_EVENT * RATE)
    keys = [(bus, group) for bus in BUSES for group in GROUPS]
    dry = {key: np.zeros((span, 2), dtype=np.float32) for key in keys}
    rooms = {
        bus: room(seconds, seed + i, damping)
        for i, (bus, (seconds, damping, _)) in enumerate(ROOMS.items())
    }
    tails = {
        key: np.zeros((len(rooms[key[0]]) - 1, 2), dtype=np.float32)
        for key in keys
        if key[0] in ROOMS
    }
    used: set[tuple[str, str]] = set()
    ordered = sorted(events, key=lambda e: e.at)
    next_event = 0
    power, active = 0.0, 0
    fade_in = int(FADE_IN * RATE)

    with ExitStack() as stack:
        sinks = {
            group: stack.enter_context(
                sf.SoundFile(str(outputs[group]), "w", samplerate=RATE, channels=2, subtype="FLOAT")
            )
            for group in GROUPS
        }
        for first in range(0, total, block):
            last = min(total, first + block)
            # Lay in every event that starts in this block.
            while next_event < len(ordered) and int(ordered[next_event].at * RATE) < last:
                event = ordered[next_event]
                next_event += 1
                audio = _event_audio(event, library)[: int(LONGEST_EVENT * RATE)]
                start = round(event.at * RATE) - first
                if start < 0:  # starts before the timeline (or the block): drop what is before it
                    audio = audio[-start:]
                    start = 0
                end = min(span, start + len(audio))
                if end > start:
                    key = (event.bus, group_of(event))
                    dry[key][start:end] += audio[: end - start]
                    used.add(key)

            length = last - first
            summed = np.zeros((length, 2), dtype=np.float32)
            for group in GROUPS:
                mixed = dry[("sub", group)][:length].copy()
                for bus, (_, _, wet) in ROOMS.items():
                    key = (bus, group)
                    part = dry[key][:length]
                    if key not in used:
                        continue
                    reverb = np.stack(
                        [signal.fftconvolve(part[:, c], rooms[bus][:, c]) for c in range(2)], axis=1
                    ).astype(np.float32)
                    reverb[: len(tails[key])] += tails[key][: len(reverb)]
                    carry = np.zeros_like(tails[key])
                    leftover = reverb[length:]
                    carry[: len(leftover)] = leftover[: len(carry)]
                    tails[key] = carry
                    mixed += part * (1 - wet) + reverb[:length] * wet
                # Moderate width: a little less side than the rooms give.
                mid, side = (mixed[:, 0] + mixed[:, 1]) / 2, (mixed[:, 0] - mixed[:, 1]) / 2
                mixed = np.stack([mid + 0.85 * side, mid - 0.85 * side], axis=1)
                if first < fade_in:
                    ramp = np.minimum(1.0, (np.arange(first, last) / fade_in)).astype(np.float32)
                    mixed *= ramp[:, None]
                sinks[group].write(mixed)
                summed += mixed
            loud = np.abs(summed).max(axis=1) > 1e-4
            power += float(np.sum(summed[loud].astype(np.float64) ** 2))
            active += int(np.sum(loud)) * 2
            for key in keys:  # move the timeline on by a block
                dry[key][:-block] = dry[key][block:]
                dry[key][-block:] = 0
    return math.sqrt(power / active) if active else 0.0


@dataclass(frozen=True)
class Finish:
    """What finishing did to the score, for the report and the tests."""

    level_change_db: float
    pause_under_voice_db: float | None


def _blocks(paths: Sequence[Path]):  # type: ignore[no-untyped-def]
    """The files' blocks in step: a list of arrays, one per file."""
    import soundfile as sf

    with ExitStack() as stack:
        sources = [stack.enter_context(sf.SoundFile(str(p))) for p in paths]
        size = int(BLOCK_SECONDS * RATE)
        while True:
            chunks = [s.read(size, dtype="float32", always_2d=True) for s in sources]
            if not len(chunks[0]):
                return
            yield chunks


def _glue(raws: dict[str, Path], glued: dict[str, Path], gain: float, duration: float) -> float:
    """Level, gentle bus compression (an RMS follower over about 80 ms), the end's fade.

    The compression follows the sum and is applied to every group alike.
    ``glued`` has one more entry than ``raws``: "sum". Returns the sum's peak.
    """
    import soundfile as sf
    from scipy import signal

    b, a = signal.butter(1, 1 / 0.08, fs=RATE)
    state = signal.lfilter_zi(b, a) * 0.0
    threshold, ratio = -20.0, 1.8
    total = round(duration * RATE)
    fade_from = total - int(FADE_OUT * RATE)
    peak, position = 0.0, 0
    names = list(raws)
    with ExitStack() as stack:
        sinks = {
            name: stack.enter_context(
                sf.SoundFile(str(glued[name]), "w", samplerate=RATE, channels=2, subtype="FLOAT")
            )
            for name in [*names, "sum"]
        }
        for chunks in _blocks([raws[n] for n in names]):
            parts = {n: c * np.float32(gain) for n, c in zip(names, chunks, strict=True)}
            summed = sum(parts.values())
            powers = np.mean(summed.astype(np.float64) ** 2, axis=1)
            followed, state = signal.lfilter(b, a, powers, zi=state)
            level = 10 * np.log10(np.maximum(followed, 1e-12))
            reduction = 10 ** (-np.maximum(0.0, level - threshold) * (1 - 1 / ratio) / 20)
            index = np.arange(position, position + len(summed))
            if index[-1] >= fade_from:
                reduction = reduction * np.clip(
                    (total - index) / max(1, total - fade_from), 0.0, 1.0
                )
            shape = reduction[:, None].astype(np.float32)
            for n in names:
                sinks[n].write(parts[n] * shape)
            out = summed * shape
            sinks["sum"].write(out)
            peak = max(peak, float(np.max(np.abs(out))) if len(out) else 0.0)
            position += len(summed)
    return peak


def _window_levels(path: Path, windows: Sequence[tuple[float, float]]) -> list[float]:
    """The 90th-percentile 100 ms level (dBFS) inside each window."""
    import soundfile as sf

    found = []
    with sf.SoundFile(str(path)) as source:
        for a, b in windows:
            source.seek(max(0, int(a * RATE)))
            part = source.read(max(0, int((b - a) * RATE)), dtype="float32", always_2d=True)
            w = RATE // 10
            if len(part) < 2 * w:
                continue
            usable = part[: len(part) // w * w].reshape(-1, w, 2).astype(np.float64)
            levels = 20 * np.log10(np.maximum(1e-7, np.sqrt(np.mean(usable**2, axis=(1, 2)))))
            found.append(float(np.percentile(levels, 90)))
    return found


def _dip(
    sources: dict[str, Path],
    outputs: dict[str, Path],
    gain: float,
    spans: Sequence[tuple[float, float]],
    duration: float,
) -> None:
    """Scale by ``gain`` and lower the speech band while words are spoken, file by file.

    A peaking filter, x + (G - 1) * bandpass(x), with G following the words:
    the bandpass has 0 dB at its centre (about 1 kHz) and spans 300 Hz to
    3.5 kHz, so G = 1 leaves the sound untouched. It is linear, so the
    groups dipped add up to the sum dipped.
    """
    import soundfile as sf
    from scipy import signal

    centre = math.sqrt(300 * 3500)
    octaves = math.log2(3500 / 300)
    w0 = 2 * math.pi * centre / RATE
    alpha = math.sin(w0) * math.sinh(math.log(2) / 2 * octaves * w0 / math.sin(w0))
    b = np.array([alpha, 0.0, -alpha]) / (1 + alpha)
    a = np.array([1.0, -2 * math.cos(w0) / (1 + alpha), (1 - alpha) / (1 + alpha)])

    # The dip's depth, at 100 per second, eased in and out over about 150 ms.
    control_rate = 100
    talking = np.zeros(int(duration * control_rate) + 2)
    for start, end in spans:
        talking[max(0, int((start - 0.1) * control_rate)) : int((end + 0.1) * control_rate) + 1] = (
            1.0
        )
    kernel = np.hanning(31) / np.hanning(31).sum()
    talking = np.clip(np.convolve(talking, kernel, mode="same"), 0.0, 1.0)
    depth = 10 ** (-SPEECH_BAND_DIP_DB * talking / 20)

    names = list(sources)
    states = {n: np.zeros((2, 2)) for n in names}
    position = 0
    with ExitStack() as stack:
        sinks = {
            n: stack.enter_context(
                sf.SoundFile(
                    str(outputs[n]),
                    "w",
                    samplerate=RATE,
                    channels=2,
                    format="FLAC",
                    subtype="PCM_24",
                )
            )
            for n in names
        }
        for chunks in _blocks([sources[n] for n in names]):
            seconds = np.arange(position, position + len(chunks[0])) / RATE
            g = np.interp(seconds * control_rate, np.arange(len(depth)), depth)
            for n, chunk in zip(names, chunks, strict=True):
                x = chunk.astype(np.float64) * gain
                band = np.empty_like(x)
                for c in range(2):
                    band[:, c], states[n][c] = signal.lfilter(b, a, x[:, c], zi=states[n][c])
                sinks[n].write(np.clip(x + (g - 1)[:, None] * band, -1.0, 1.0).astype(np.float32))
            position += len(chunks[0])


def finish(
    raws: dict[str, Path],
    outputs: dict[str, Path],
    *,
    rms: float,
    voice: Path,
    spans: Sequence[tuple[float, float]],
    pauses: Sequence[tuple[float, float]],
    duration: float,
    work_dir: Path,
) -> Finish:
    """Level the raw score, fit it under the voice, and dip it under the words.

    ``outputs`` names a file for each group and for "sum" (24-bit FLAC).
    """
    import soundfile as sf

    from voxframe.render.audio.mixdown import BED_DB, speech_levels

    glued = {name: work_dir / f"score.glued.{name}.wav" for name in [*raws, "sum"]}
    level_gain = 10 ** (TARGET_RMS_DB / 20) / max(rms, 1e-6)
    peak = _glue(raws, glued, level_gain, duration)

    measured = speech_levels(spans, voice, glued["sum"], sf)
    voice_db = float(np.median([s.voice_db for s in measured]))
    music_db = float(np.median([s.music_db for s in measured]))
    change = (voice_db - PAUSE_UNDER_VOICE_DB) - (music_db + BED_DB)
    # Never closer than PAUSE_CEILING_DB to the voice in a pause, even at full energy.
    windows = [*pauses, (spans[-1][1], duration)] if spans else list(pauses)
    pause_levels = _window_levels(glued["sum"], windows)
    under: float | None = None
    if pause_levels:
        loudest = max(pause_levels) + BED_DB + change
        excess = loudest - (voice_db - PAUSE_CEILING_DB)
        if excess > 0:
            change -= excess
        under = round(voice_db - (max(pause_levels) + BED_DB + change), 1)
    gain = 10 ** (change / 20)
    if peak * gain > PEAK:
        gain = PEAK / peak
    _dip(glued, outputs, gain, spans, duration)
    for path in glued.values():
        path.unlink(missing_ok=True)
    return Finish(
        level_change_db=round(20 * math.log10(level_gain * gain), 1), pause_under_voice_db=under
    )
