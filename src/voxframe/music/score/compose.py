"""Composing and arranging a score from the speech and a style (D-176).

Everything random comes from one generator seeded per video, in a fixed
order, so the same speech, style and seed always give the same events, and
a new seed gives a new piece:

- **The variation** chosen per video: the key (from the style's list), the
  tempo (within its range), the progressions and their order, the ostinato
  patterns, which instrument leads each section, and the voicing register.
- **Sections** every 45 to 90 seconds at a speech boundary, sooner after a
  long pause; each with its own progression, ostinato pattern and lead.
- **Chords** on a beat grid that breathes a little, each with an energy level
  from 0 to 4 read from the speech and shaped by the style. Layers enter one
  level at a time, and the speaker's peaks bring everything in.
- **Events**: every sound with its values fixed when it is planned, including
  which of several equal recordings plays. Rendering them is then pure.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np

from voxframe.music.score.speech import Speech
from voxframe.music.score.styles import MAJOR, Style, chord_tones

__all__ = ["Chord", "Composition", "Event", "Section", "arrange", "compose"]


@dataclass(frozen=True)
class Section:
    start: float
    end: float
    progression: tuple[str, ...]
    tonic: int
    pattern: tuple[int, ...]
    register: int
    lead: str


@dataclass(frozen=True)
class Chord:
    start: float
    end: float
    symbol: str
    tonic: int
    level: int
    section: int
    beat: float
    first_of_section: bool = False


@dataclass(frozen=True)
class Composition:
    tonic: int
    tempo: float
    sections: tuple[Section, ...]
    chords: tuple[Chord, ...]
    powerful: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class Event:
    """One sound, fully decided. Rendering an event depends on nothing else."""

    at: float
    bus: str  # the reverb room: keys, strings, ostinato, perc, pad, sub, fx
    kind: str  # note, hit, roll, pad, sub, riser, impact
    gain: float
    instrument: str = ""
    midi: int = 0
    length: float = 0.0
    layer: int = 0
    attack: float = 0.005
    release: float = 0.4
    loop: bool = False
    take: int = 0  # which of several equal recordings
    pick: str = ""  # a word the hit's file name must contain
    strength: float = 0.5
    target: tuple[int, ...] = ()  # pitch classes a tuned drum is retuned to
    notes: tuple[int, ...] = ()  # a pad's chord
    brightness: float = 0.0
    seed: int = 0  # for synthesis
    group: str = ""  # the style's volume group: keys, strings, ostinato, percussion, ...


# --- composing ---------------------------------------------------------------------


def _sections(speech: Speech, style: Style, rng: random.Random, tonic: int) -> list[Section]:
    long_ends = {round(b, 2) for _, b in speech.pauses}
    boundaries = [0.0]
    for start, _ in speech.spans[1:]:
        since = start - boundaries[-1]
        after_long = round(start, 2) in long_ends
        if ((after_long and since >= 25) or since >= 45) and speech.landing - start >= 20:
            boundaries.append(start)
    boundaries.append(speech.duration)

    patterns = list(style.patterns)
    rng.shuffle(patterns)
    leads = list(style.leads)
    rng.shuffle(leads)
    bag: list[tuple[str, ...]] = []
    previous: tuple[str, ...] | None = None
    sections: list[Section] = []
    count = len(boundaries) - 1
    for i in range(count):
        if not bag:  # every progression once, in a new order, before any repeats
            bag = list(style.progressions)
            rng.shuffle(bag)
            if bag[0] == previous and len(bag) > 1:
                bag.append(bag.pop(0))
        progression = bag.pop(0)
        previous = progression
        key = tonic
        if style.lift_last_section and i == count - 1 and i > 0:
            key += 2  # the lift: the last section a whole step up
        elif style.modulations and i > 0 and rng.random() < style.modulate:
            key += rng.choice(style.modulations)
        sections.append(
            Section(
                start=boundaries[i],
                end=boundaries[i + 1],
                progression=progression,
                tonic=key,
                pattern=patterns[i % len(patterns)],
                register=rng.choice((0, 0, 12)),
                lead=leads[i % len(leads)],
            )
        )
    return sections


#: How far the editor's intensity setting (-1 to 1) moves the energy, in levels.
INTENSITY_LEVELS = 1.2


def _level_target(
    speech: Speech, style: Style, at: float, section: Section, intensity: float = 0.0
) -> float:
    z = (speech.energy_at(at) - 0.5) / 0.18
    arc = 0.7 * (at - section.start) / max(1.0, section.end - section.start)  # sections build
    return 1.6 + style.energy_offset + INTENSITY_LEVELS * intensity + 1.1 * z + arc


def compose(
    speech: Speech, style: Style, seed: int, intensity: float = 0.0
) -> tuple[Composition, random.Random]:
    """The piece's plan: its variation, sections and chords.

    Returns the generator too, so arranging continues the same sequence.
    """
    rng = random.Random(seed)
    tonic = rng.choice(style.tonics)
    tempo = round(rng.uniform(style.tempo_min, style.tempo_max) * 2) / 2
    sections = _sections(speech, style, rng, tonic)

    peak = float(np.quantile(np.array(speech.intensity), 0.9))
    chords: list[Chord] = []
    previous = style.energy_min
    for n, section in enumerate(sections):
        beat = 60.0 / tempo
        t, i = section.start, 0
        while t < min(section.end, speech.landing) - 0.5:
            bars = rng.choice(style.chord_bars)
            local_beat = beat * rng.uniform(0.985, 1.015)  # the tempo breathes
            end = min(t + bars * 4 * local_beat, speech.landing)
            if speech.landing - end < 2.0:
                end = speech.landing
            middle = (t + end) / 2
            target = _level_target(speech, style, middle, section, intensity)
            if speech.energy_at(middle) >= peak and target >= 2.5:
                target = 4  # the speaker's peaks: everything in
            level = round(float(np.clip(target, style.energy_min, style.energy_max)))
            level = min(level, previous + 1)  # layers enter one at a time
            symbol = section.progression[i % len(section.progression)]
            chords.append(Chord(t, end, symbol, section.tonic, level, n, local_beat, i == 0))
            previous, t, i = level, end, i + 1
    if chords:
        # A cadence into the last word, then home on it.
        last = chords[-1]
        chords[-1] = Chord(
            last.start,
            last.end,
            "V" if style.home == "I" else "IV",
            last.tonic,
            last.level,
            last.section,
            last.beat,
            last.first_of_section,
        )
        chords.append(
            Chord(
                speech.landing,
                speech.duration,
                style.home,
                last.tonic,
                max(1, last.level),
                last.section,
                last.beat,
            )
        )
    composition = Composition(
        tonic=tonic,
        tempo=tempo,
        sections=tuple(sections),
        chords=tuple(chords),
        powerful=tuple(speech.powerful_lines()),
    )
    return composition, rng


# --- arranging ---------------------------------------------------------------------


@dataclass
class _Arranger:
    style: Style
    rng: random.Random
    events: list[Event] = field(default_factory=list)
    drop: tuple[tuple[float, float], ...] = ()

    GROUP: ClassVar[dict[str, str]] = {
        "keys": "keys",
        "strings": "strings",
        "ostinato": "ostinato",
        "perc": "percussion",
        "pad": "pads",
        "sub": "bass",
        "fx": "effects",
    }

    def human(self, sd: float = 0.008) -> float:
        return max(-0.03, min(0.03, self.rng.gauss(0, sd)))

    def dropped(self, t: float) -> bool:
        return any(a <= t <= b for a, b in self.drop)

    def add(
        self, at: float, bus: str, kind: str, gain: float, *, group: str = "", **values: Any
    ) -> None:
        group = group or self.GROUP[bus]
        level = self.style.level(group)
        if kind in ("note", "hit", "roll"):
            values.setdefault("take", self.rng.randrange(1 << 16))
        if kind in ("pad", "riser", "impact", "roll"):
            values.setdefault("seed", self.rng.randrange(1 << 30))
        self.events.append(
            Event(at=round(at, 5), bus=bus, kind=kind, gain=gain * level, group=group, **values)
        )


def _targets(symbol: str, tonic: int) -> tuple[int, int]:
    root = chord_tones(symbol, tonic)[0] % 12
    return root, (root + 7) % 12


def arrange(
    composition: Composition, speech: Speech, style: Style, rng: random.Random
) -> tuple[Event, ...]:
    """Every sound of the score, by the style's layers and the chords' energy."""
    a = _Arranger(style, rng, drop=tuple((s - 0.6, e) for s, e in composition.powerful))
    top = 74
    gong_done = False
    chords = composition.chords
    for c_i, ch in enumerate(chords):
        section = composition.sections[ch.section]
        tones = chord_tones(ch.symbol, ch.tonic)
        root = tones[0] % 12
        bass = 36 + (root - 36) % 12
        if bass > 43:
            bass -= 12
        third = tones[1] % 12
        upper = sorted({60 + (t % 12 - 60) % 12 for t in tones[1:]} | {60 + (root + 2 - 60) % 12})
        upper = [u + section.register if u + section.register <= 84 else u for u in upper]
        previous_top = top
        top = min(
            (u + o for u in upper for o in (0, 12) if 67 <= u + o <= 84),
            key=lambda n: abs(n - previous_top) + rng.uniform(0, 2),
        )
        length = ch.end - ch.start
        last = c_i == len(chords) - 1
        lv = ch.level
        target = _targets(ch.symbol, ch.tonic)

        if style.enters("pad", lv) or last:
            a.add(
                ch.start,
                "pad",
                "pad",
                0.10 + 0.03 * lv,
                notes=(bass + 12, *upper),
                length=length + 0.5,
                brightness=0.25 + 0.15 * lv,
            )

        if style.enters("string_pads", lv) or last:
            for midi, inst, gain in (
                (bass + 12, "cellos", 0.30 + 0.05 * lv),
                (bass + 19, "violas", 0.22 + 0.04 * lv),
            ):
                a.add(
                    ch.start + a.human(0.02),
                    "strings",
                    "note",
                    gain,
                    instrument=inst,
                    midi=midi,
                    length=length,
                    attack=1.0,
                    release=1.8,
                    loop=True,
                )
        if style.enters("contrabass", lv) or (last and "contrabass" in style.layers):
            a.add(
                ch.start,
                "strings",
                "note",
                0.20 + 0.04 * lv,
                group="bass",
                instrument="contrabass",
                midi=bass,
                length=length,
                attack=0.8,
                release=1.5,
                loop=True,
            )
        if style.enters("high_violins", lv) or (last and style.final_hit):
            a.add(
                ch.start + a.human(0.02),
                "strings",
                "note",
                0.16 + 0.05 * (lv - 3),
                instrument="violins",
                midi=top,
                length=length,
                attack=1.2,
                release=2.0,
                loop=True,
            )
        if style.enters("sub_bass", lv) and not last:
            a.add(ch.start, "sub", "sub", 0.22 + 0.05 * (lv - 2), midi=bass - 12, length=length)

        if last:
            # Home: everyone on the last word, with a low hit in the fuller styles.
            if style.final_hit:
                a.add(
                    ch.start - 0.02,
                    "perc",
                    "hit",
                    0.9,
                    instrument="bass_drum",
                    strength=0.8,
                    pick="hit",
                )
                a.add(
                    ch.start - 0.01,
                    "perc",
                    "hit",
                    0.8,
                    instrument="timpani",
                    strength=0.8,
                    target=target,
                )
                a.add(ch.start, "fx", "impact", 0.25)
            a.add(
                ch.start + 0.05,
                "keys",
                "note",
                0.22,
                instrument="piano",
                midi=top,
                length=6.0,
                layer=3,
                release=1.5,
            )
            a.add(
                ch.start + 0.08,
                "keys",
                "note",
                0.16,
                instrument="piano",
                midi=bass + 12,
                length=6.0,
                layer=2,
                release=1.5,
            )
            continue

        # Keys: a sparse melody at low energy; arpeggios as it rises.
        if style.enters("melody", lv) and lv <= style.melody_until:
            t = ch.start + rng.uniform(0.0, ch.beat)
            melody = top
            while t < ch.end - 0.8:
                if not a.dropped(t):
                    a.add(
                        t + a.human(0.015),
                        "keys",
                        "note",
                        0.15 + 0.04 * rng.random(),
                        instrument="piano",
                        midi=melody,
                        length=2.8,
                        layer=2,
                        release=1.2,
                    )
                scale = [
                    n
                    for n in range(melody - 5, melody + 6)
                    if (n - ch.tonic) % 12 in MAJOR and 64 <= n <= 86
                ]
                aim = melody + rng.choice((-3, -2, -1, 1, 2, 4))
                melody = min(scale, key=lambda n: abs(n - aim))
                t += ch.beat * rng.choice((2, 3, 4) if lv == 0 else (1, 2, 2, 3))
            a.add(
                ch.start + a.human(0.015),
                "keys",
                "note",
                0.12,
                instrument="piano",
                midi=bass + 12,
                length=min(length, 5.0),
                layer=2,
                release=1.2,
            )
        if style.enters("arpeggio", lv) and style.arpeggio != "none":
            harp = style.arpeggio == "harp" or section.lead == "harp"
            pool = sorted({bass + 12, bass + 19, *[u - 12 if u > 79 else u for u in upper]})
            eighths = style.arpeggio_eighths_from is not None and lv >= style.arpeggio_eighths_from
            step_time = ch.beat / 2 if eighths else ch.beat
            t, j, direction = ch.start, 0, 1
            while t < ch.end - 0.1:
                if not a.dropped(t):
                    gain = (
                        (0.20 if harp else 0.10) * (1.0 if j == 0 else 0.8) * rng.uniform(0.85, 1.1)
                    )
                    if harp:
                        a.add(
                            t + a.human(),
                            "keys",
                            "note",
                            gain,
                            instrument="harp",
                            midi=pool[j],
                            length=2.5,
                            release=1.0,
                        )
                    else:
                        a.add(
                            t + a.human(),
                            "keys",
                            "note",
                            gain,
                            instrument="piano",
                            midi=pool[j],
                            length=1.2,
                            layer=2 if lv < 4 else 3,
                            release=0.8,
                        )
                if not 0 <= j + direction < len(pool):
                    direction = -direction
                j += direction
                t += step_time

        # The section's lead: a slow line over the chord.
        if style.enters("lead_line", lv) and section.lead in ("violins", "cellos", "piano"):
            low, high = {"violins": (67, 84), "cellos": (52, 64), "piano": (69, 86)}[section.lead]
            chord_pcs = {t % 12 for t in tones} | {(root + 2) % 12}
            candidates = [n for n in range(low, high + 1) if n % 12 in chord_pcs]
            aim = top if section.lead != "cellos" else 58
            line = min(candidates, key=lambda n: abs(n - aim))
            t = ch.start + ch.beat * rng.choice((0, 1, 2))
            while t < ch.end - ch.beat:
                hold = ch.beat * rng.choice((2, 3, 4))
                if not a.dropped(t):
                    if section.lead == "piano":
                        a.add(
                            t + a.human(0.012),
                            "keys",
                            "note",
                            0.16 * rng.uniform(0.9, 1.1),
                            instrument="piano",
                            midi=line,
                            length=hold,
                            layer=3,
                            release=1.0,
                        )
                    else:
                        a.add(
                            t + a.human(0.02),
                            "strings",
                            "note",
                            0.20 if section.lead == "violins" else 0.26,
                            instrument=section.lead,
                            midi=line,
                            length=min(hold, ch.end - t),
                            attack=0.35,
                            release=1.2,
                            loop=True,
                        )
                steps = [n for n in candidates if 0 < abs(n - line) <= 5]
                line = rng.choice(steps) if steps else line
                t += hold

        # Ostinatos.
        parts: list[tuple[str, list[int], float]] = []
        if style.enters("cello_ostinato", lv):
            parts.append(
                ("cellos_spic", [bass + 12, bass + 19, bass + 24, 48 + (third - 48) % 12], 0.32)
            )
        if style.enters("viola_ostinato", lv):
            parts.append(
                ("violas_spic", [bass + 24, bass + 31, bass + 36, 60 + (third - 60) % 12], 0.24)
            )
        if style.enters("violin_ostinato", lv):
            parts.append(("violins_spic", [top - 12, top - 5, top, top - 8], 0.20))
        for inst, pitches, gain in parts:
            t, k = ch.start, 0
            while t < ch.end - 0.05:
                if not a.dropped(t):
                    accent = style.accents[k % 8]
                    midi = pitches[section.pattern[k % 8] % len(pitches)]
                    a.add(
                        t + a.human(),
                        "ostinato",
                        "note",
                        gain * accent * rng.uniform(0.88, 1.08),
                        instrument=inst,
                        midi=midi,
                        length=ch.beat / 2 * 0.9,
                        layer=2 if accent > 0.9 and lv >= 3 else 1,
                        attack=0.002,
                        release=0.25,
                    )
                t += ch.beat / 2
                k += 1

        # Percussion.
        if style.enters("chord_hits", lv) and not a.dropped(ch.start):
            a.add(
                ch.start - 0.01,
                "perc",
                "hit",
                0.55,
                instrument="timpani",
                strength=0.35 + 0.15 * (lv - 3),
                target=target,
            )
            a.add(
                ch.start - 0.015,
                "perc",
                "hit",
                0.45,
                instrument="bass_drum",
                strength=0.3 + 0.2 * (lv - 3),
                pick="hit",
            )
        if style.enters("driving_percussion", lv):
            t, b = ch.start, 0
            while t < ch.end - 0.1:
                if not a.dropped(t):
                    if b % 4 == 0 and t > ch.start:
                        a.add(
                            t + a.human(0.006),
                            "perc",
                            "hit",
                            0.40,
                            instrument="bass_drum",
                            strength=0.45,
                            pick="hit",
                        )
                    if b % 8 in (0, 3, 6):  # 3-3-2 on the timpani
                        a.add(
                            t + a.human(0.006),
                            "perc",
                            "hit",
                            0.42,
                            instrument="timpani",
                            strength=0.6 if b % 8 == 0 else 0.35,
                            target=target,
                        )
                t += ch.beat / 2
                b += 1
        if ch.first_of_section and c_i > 0:
            if style.enters("section_swell", lv):
                a.add(
                    ch.start - 3.9,
                    "perc",
                    "hit",
                    0.45,
                    instrument="cymbal",
                    strength=0.5,
                    pick="cresc_4s",
                )
                a.add(
                    ch.start - 2.8,
                    "perc",
                    "roll",
                    0.5,
                    instrument="timpani_rolls",
                    length=2.8,
                    target=target,
                )
                if not gong_done and style.enters("gong", lv):
                    a.add(ch.start, "perc", "hit", 0.30, instrument="gong", strength=0.3)
                    gong_done = True
            elif style.enters("soft_section_roll", lv):
                a.add(
                    ch.start - 2.0,
                    "perc",
                    "roll",
                    0.25,
                    instrument="timpani_rolls",
                    length=2.0,
                    target=target,
                )

    # Powerful lines: the music thins away for them (above), then lands after.
    for start, end in composition.powerful:
        a.add(
            start - 0.55,
            "strings",
            "note",
            0.14,
            instrument="violins",
            midi=top,
            length=max(1.5, end - start + 0.5),
            attack=0.4,
            release=1.2,
            loop=True,
        )
        a.add(end + 0.05, "perc", "hit", 0.55, instrument="bass_drum", strength=0.7, pick="hit")
        a.add(end + 0.05, "fx", "impact", 0.18)

    # Long pauses: a swell into the silence.
    for p0, p1 in speech.pauses:
        if p1 > speech.landing:
            continue
        here = next((c for c in chords if c.start <= p0 < c.end), chords[0])
        lv = here.level
        target = _targets(here.symbol, here.tonic)
        a.add(
            p0 - 1.2,
            "perc",
            "hit",
            0.30 + 0.06 * lv,
            instrument="cymbal",
            strength=0.4,
            pick="cresc_2s",
        )
        if style.enters("pause_roll", lv):
            a.add(
                p0 - 0.2,
                "perc",
                "roll",
                0.25 + 0.1 * lv,
                instrument="timpani_rolls",
                length=p1 - p0,
                target=target,
            )
        length = max(1.2, p1 - p0)
        a.add(
            p0 - 0.3,
            "strings",
            "note",
            0.16 + 0.04 * lv,
            instrument="violins",
            midi=top,
            length=length,
            attack=length * 0.6,
            release=1.5,
            loop=True,
        )
        if style.enters("pause_riser", lv):
            a.add(p0 + 0.2, "fx", "riser", 0.07, length=max(0.6, p1 - p0 - 0.55))
    return tuple(sorted(a.events, key=lambda e: (e.at, e.bus, e.instrument, e.midi)))
