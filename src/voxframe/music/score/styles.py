"""Score styles, read from the TOML files in ``styles/`` (D-176).

A style is data, like the metaphor dictionary (D-136): adding a file adds a
style, and the format is described in ``styles/README.md``. Every value is
checked as it is read, so a mistake says which file and which value, rather
than producing odd music.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

__all__ = [
    "GROUPS",
    "PARTS",
    "STYLES_DIR",
    "Style",
    "StyleError",
    "chord_tones",
    "load_style",
    "styles",
]

STYLES_DIR = Path(__file__).with_name("styles")

MAJOR = (0, 2, 4, 5, 7, 9, 11)
DEGREES = {"I": 0, "ii": 1, "iii": 2, "IV": 3, "V": 4, "vi": 5}
CHORD = re.compile(r"(iii|ii|vi|IV|V|I)(maj7|7)?$")
NOTE_NAMES = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
}

#: The arrangement's parts, entered by energy level (styles/README.md).
PARTS = (
    "pad",
    "melody",
    "string_pads",
    "contrabass",
    "high_violins",
    "sub_bass",
    "arpeggio",
    "lead_line",
    "cello_ostinato",
    "viola_ostinato",
    "violin_ostinato",
    "chord_hits",
    "driving_percussion",
    "section_swell",
    "soft_section_roll",
    "gong",
    "pause_roll",
    "pause_riser",
)
#: The instrument groups a style (and later the editor) sets the volume of.
GROUPS = ("keys", "strings", "ostinato", "percussion", "pads", "bass", "effects")
LEADS = ("piano", "violins", "cellos", "harp")


class StyleError(ValueError):
    """A style file says something the score cannot use."""


@dataclass(frozen=True)
class Style:
    name: str
    label: str
    description: str
    order: int
    tonics: tuple[int, ...]  # MIDI numbers, A3 to G#4
    home: str
    lift_last_section: bool
    modulate: float
    modulations: tuple[int, ...]
    tempo_min: float
    tempo_max: float
    energy_offset: float
    energy_min: int
    energy_max: int
    progressions: tuple[tuple[str, ...], ...]
    chord_bars: tuple[int, ...]
    patterns: tuple[tuple[int, ...], ...]
    accents: tuple[float, ...]
    leads: tuple[str, ...]
    arpeggio: str
    arpeggio_eighths_from: int | None
    layers: dict[str, int] = field(hash=False)
    melody_until: int = 4
    final_hit: bool = False
    levels: dict[str, float] = field(default_factory=dict, hash=False)
    #: The file's text, so a score is re-made when its style changes.
    source: str = field(default="", repr=False)

    def enters(self, part: str, level: int) -> bool:
        """Whether ``part`` plays at energy ``level``."""
        start = self.layers.get(part)
        return start is not None and level >= start

    def level(self, group: str) -> float:
        return self.levels.get(group, 1.0)


def chord_tones(symbol: str, tonic: int) -> list[int]:
    """Root, third, fifth (and seventh) of a Roman-numeral chord, as MIDI above the tonic."""
    match = CHORD.match(symbol)
    if match is None:
        raise StyleError(f"unknown chord {symbol!r}")
    degree = DEGREES[match.group(1)]
    steps = (0, 2, 4, 6) if match.group(2) else (0, 2, 4)
    return [tonic + MAJOR[(degree + k) % 7] + 12 * ((degree + k) // 7) for k in steps]


def _tonic(name: object, where: str) -> int:
    if not isinstance(name, str) or name not in NOTE_NAMES:
        raise StyleError(f"{where}: {name!r} is not a note name such as C, F# or Bb")
    return 57 + (NOTE_NAMES[name] - 9) % 12  # A3 .. G#4


def load_style(path: Path) -> Style:
    """Read and check one style file."""
    text = path.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise StyleError(f"{path.name}: not valid TOML ({exc})") from exc
    where = path.name

    def get(table: str, key: str, kind: type | tuple[type, ...], default: Any = ...) -> Any:
        section = data if not table else data.get(table, {})
        if not isinstance(section, dict):
            raise StyleError(f"{where}: [{table}] must be a table")
        if key not in section:
            if default is ...:
                raise StyleError(f"{where}: {table + '.' if table else ''}{key} is missing")
            return default
        value = section[key]
        if isinstance(value, bool) and bool not in (kind if isinstance(kind, tuple) else (kind,)):
            raise StyleError(f"{where}: {table}.{key} must not be true or false")
        if not isinstance(value, kind):
            raise StyleError(f"{where}: {table + '.' if table else ''}{key} has the wrong type")
        return value

    name = get("", "name", str)
    if name != path.stem or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise StyleError(f"{where}: name must be the file's name, in lower case ({path.stem!r})")

    tonics = tuple(_tonic(t, f"{where}: key.tonics") for t in get("key", "tonics", list))
    home = get("key", "home", str)
    if home not in ("I", "vi"):
        raise StyleError(f"{where}: key.home must be 'I' (major) or 'vi' (minor)")
    modulate = float(get("key", "modulate", (int, float), 0.0))
    if not 0 <= modulate <= 1:
        raise StyleError(f"{where}: key.modulate must be between 0 and 1")

    tempo_min = float(get("tempo", "min", (int, float)))
    tempo_max = float(get("tempo", "max", (int, float)))
    if not 40 <= tempo_min <= tempo_max <= 160:
        raise StyleError(f"{where}: the tempo must run from min to max, within 40 to 160")

    energy_min, energy_max = get("energy", "min", int), get("energy", "max", int)
    if not 0 <= energy_min <= energy_max <= 4:
        raise StyleError(f"{where}: energy min and max must be levels 0 to 4, min first")

    progressions = tuple(tuple(p) for p in get("harmony", "progressions", list))
    if not progressions or any(not p for p in progressions):
        raise StyleError(f"{where}: harmony.progressions needs at least one progression")
    for progression in progressions:
        for symbol in progression:
            try:
                chord_tones(symbol, 60)
            except StyleError as exc:
                raise StyleError(f"{where}: harmony.progressions: {exc}") from exc
    chord_bars = tuple(get("harmony", "chord_bars", list))
    if not chord_bars or any(not isinstance(b, int) or not 1 <= b <= 8 for b in chord_bars):
        raise StyleError(f"{where}: harmony.chord_bars must be whole numbers of bars, 1 to 8")

    patterns = tuple(tuple(p) for p in get("rhythm", "patterns", list))
    if not patterns or any(
        len(p) != 8 or any(not isinstance(i, int) or not 0 <= i <= 3 for i in p) for p in patterns
    ):
        raise StyleError(f"{where}: each rhythm pattern must be eight numbers from 0 to 3")
    accents = tuple(float(a) for a in get("rhythm", "accents", list))
    if len(accents) != 8 or any(not 0 < a <= 1.5 for a in accents):
        raise StyleError(f"{where}: rhythm.accents must be eight strengths above 0")

    leads = tuple(get("instruments", "leads", list))
    if not leads or any(lead not in LEADS for lead in leads):
        raise StyleError(f"{where}: instruments.leads may name {', '.join(LEADS)}")
    arpeggio = get("instruments", "arpeggio", str, "none")
    if arpeggio not in ("piano", "harp", "none"):
        raise StyleError(f"{where}: instruments.arpeggio must be piano, harp or none")
    eighths = get("instruments", "arpeggio_eighths_from", int, None)

    layer_table = data.get("layers", {})
    if not isinstance(layer_table, dict):
        raise StyleError(f"{where}: [layers] must be a table")
    layers: dict[str, int] = {}
    for part, value in layer_table.items():
        if part in ("melody_until", "final_hit"):
            continue
        if part not in PARTS:
            raise StyleError(f"{where}: layers.{part} is not a part (see styles/README.md)")
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4:
            raise StyleError(f"{where}: layers.{part} must be an energy level, 0 to 4")
        layers[part] = value
    melody_until = get("layers", "melody_until", int, 4)
    final_hit = get("layers", "final_hit", bool, False)

    level_table = data.get("levels", {})
    levels: dict[str, float] = {}
    for group, value in level_table.items():
        if group not in GROUPS:
            raise StyleError(f"{where}: levels.{group} is not a group ({', '.join(GROUPS)})")
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 4:
            raise StyleError(f"{where}: levels.{group} must be a volume from 0 to 4")
        levels[group] = float(value)

    return Style(
        name=name,
        label=get("", "label", str),
        description=get("", "description", str, ""),
        order=get("", "order", int, 100),
        tonics=tonics,
        home=home,
        lift_last_section=get("key", "lift_last_section", bool, False),
        modulate=modulate,
        modulations=tuple(get("key", "modulations", list, [])),
        tempo_min=tempo_min,
        tempo_max=tempo_max,
        energy_offset=float(get("energy", "offset", (int, float), 0.0)),
        energy_min=energy_min,
        energy_max=energy_max,
        progressions=progressions,
        chord_bars=chord_bars,
        patterns=patterns,
        accents=accents,
        leads=leads,
        arpeggio=arpeggio,
        arpeggio_eighths_from=eighths,
        layers=layers,
        melody_until=melody_until,
        final_hit=final_hit,
        levels=levels,
        source=text,
    )


@cache
def styles(folder: Path = STYLES_DIR) -> dict[str, Style]:
    """Every style, by name, in their order."""
    loaded = [load_style(path) for path in sorted(folder.glob("*.toml"))]
    return {s.name: s for s in sorted(loaded, key=lambda s: (s.order, s.name))}
