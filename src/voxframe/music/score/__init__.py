"""The generated score: music composed for the speech, from data styles (D-176).

``make_score`` reads the speech, composes and arranges a piece in a style
with a per-video seed, renders it from CC0 samples and synthesis, and fits it
under the voice. The result is the music stem the mix (D-171) ducks and
checks like any other music.

The same speech, style and seed always give the same score; a new seed gives
a new piece in the same style.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from voxframe.music.score.styles import Style, StyleError, styles

if TYPE_CHECKING:
    from voxframe.music.score.compose import Composition, Event
    from voxframe.music.score.speech import Speech

__all__ = [
    "SCORE_VERSION",
    "ScoreResult",
    "ScoreUnavailable",
    "Style",
    "StyleError",
    "events_for",
    "group_paths",
    "make_score",
    "samples_dir",
    "score_key",
    "style_preview",
    "styles",
]

log = structlog.get_logger(__name__)

#: Bumped whenever the same events would render differently.
SCORE_VERSION = 2


class ScoreUnavailable(RuntimeError):
    """The score cannot be made here: say why, and the video goes on without it."""


def group_paths(path: Path) -> dict[str, Path]:
    """Where a score's group stems are kept, beside its sum at ``path``."""
    from voxframe.music.score.render import GROUPS

    return {group: path.with_name(f"{path.stem}.{group}{path.suffix}") for group in GROUPS}


@dataclass(frozen=True)
class ScoreResult:
    path: Path  # the sum of the groups at their designed levels
    groups: dict[str, Path]
    landing: float
    style: str
    key: str  # e.g. "D major"
    tempo: float
    sections: int
    level_change_db: float
    pause_under_voice_db: float | None
    note: str = ""


NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")


def samples_dir() -> Path:
    """Where the score's samples are: the setting, or the downloaded pack."""
    from voxframe.config.paths import data_dir
    from voxframe.config.settings import get_settings

    configured = get_settings().score_samples_path
    return Path(configured) if configured else data_dir() / "components" / "score-samples"


def _check_tools() -> None:
    """SciPy and soundfile come with the music component (D-172, D-176)."""
    try:
        import scipy.signal  # noqa: F401
        import soundfile  # noqa: F401
    except ImportError:
        from voxframe.components import activate_music

        if not activate_music():
            raise ScoreUnavailable("the music tools are not downloaded yet") from None


def score_key(
    words: Sequence[tuple[float, float]],
    duration: float,
    style: Style,
    seed: int,
    voice_key: str,
    intensity: float = 0.0,
) -> str:
    """What a score depends on, as a cache key."""
    material = json.dumps(
        {
            "version": SCORE_VERSION,
            "style": hashlib.sha256(style.source.encode()).hexdigest(),
            "seed": seed,
            "intensity": round(intensity, 3),
            "words": [[round(s, 3), round(e, 3)] for s, e in words],
            "duration": round(duration, 3),
            "voice": voice_key,
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:24]


def events_for(
    words: Sequence[tuple[float, float]],
    voice: Path,
    duration: float,
    style: Style,
    seed: int,
    intensity: float = 0.0,
) -> tuple[Speech, Composition, tuple[Event, ...]]:
    """The speech read, and the score's composition and events, without rendering."""
    from voxframe.music.score.compose import arrange, compose
    from voxframe.music.score.speech import read_speech

    speech = read_speech(words, voice, duration)
    composition, rng = compose(speech, style, seed, intensity)
    return speech, composition, arrange(composition, speech, style, rng)


#: A style's preview: how long, and the stand-in speech it is composed for.
PREVIEW_SECONDS = 12.0
PREVIEW_SEED = 7
_PREVIEW_PHRASES = ((0.8, 5.4), (7.6, 10.4))  # a long pause between: the swell
_preview_lock = threading.Lock()


def _stand_in_speech(path: Path) -> list[tuple[float, float]]:
    """A quiet stand-in voice (never heard) and its words, for composing a preview."""
    import numpy as np
    import soundfile as sf

    rate = 48000
    rng = np.random.default_rng(1)
    voice = np.zeros(int(PREVIEW_SECONDS * rate), dtype=np.float32)
    words: list[tuple[float, float]] = []
    for start, end in _PREVIEW_PHRASES:
        t = start
        while t + 0.3 <= end:
            first = int(t * rate)
            voice[first : first + int(0.3 * rate)] = rng.standard_normal(int(0.3 * rate)) * 0.05
            words.append((round(t, 3), round(t + 0.3, 3)))
            t += 0.42
    sf.write(str(path), voice, rate, subtype="FLOAT")
    return words


def style_preview(name: str, folder: Path, samples: Path | None = None) -> Path:
    """A short sample of a style, made once from the samples and kept (D-179).

    Composed for a stand-in speech pattern, two phrases and a pause, so it
    shows the style building and swelling; the score alone is kept, leveled
    for listening. Made again only when the style or the engine changes.
    """
    import shutil

    import numpy as np
    import soundfile as sf

    style = styles()[name]
    digest = hashlib.sha256(f"{SCORE_VERSION}:{style.source}".encode()).hexdigest()[:12]
    path = folder / f"{name}-{digest}.wav"
    with _preview_lock:
        if path.is_file():
            return path
        work = folder / f".work-{name}"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            voice = work / "voice.wav"
            words = _stand_in_speech(voice)
            result = make_score(
                words=words,
                voice=voice,
                duration=PREVIEW_SECONDS,
                style_name=name,
                seed=PREVIEW_SEED,
                output=work / "score.flac",
                work_dir=work,
                samples=samples,
                intensity=0.6,
            )
            audio, rate = sf.read(str(result.path), dtype="float32", always_2d=True)
            active = audio[np.abs(audio).max(axis=1) > 1e-4]
            rms = float(np.sqrt(np.mean(active.astype(np.float64) ** 2))) if active.size else 1.0
            audio *= 10 ** (-20 / 20) / max(rms, 1e-6)
            audio *= min(1.0, 0.9 / max(float(np.max(np.abs(audio))), 1e-6))
            fade = int(1.0 * rate)
            audio[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
            partial = path.with_name(f"{path.stem}.partial.wav")
            sf.write(str(partial), audio, rate, subtype="PCM_16")
            partial.replace(path)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return path


def make_score(
    *,
    words: Sequence[tuple[float, float]],
    voice: Path,
    duration: float,
    style_name: str,
    seed: int,
    output: Path,
    work_dir: Path,
    samples: Path | None = None,
    intensity: float = 0.0,
) -> ScoreResult:
    """Compose, render and fit a score for this speech.

    Writes its group stems (``group_paths(output)``) and their sum (``output``),
    48 kHz 24-bit FLAC.

    Raises:
        ScoreUnavailable: the style, the samples or the music tools are missing.
    """
    from voxframe.music.score.instruments import SampleLibrary, SamplesMissing
    from voxframe.music.score.render import finish, render_events

    _check_tools()
    known = styles()
    if style_name not in known:
        raise ScoreUnavailable(f"there is no score style called {style_name!r}")
    style = known[style_name]
    root = samples or samples_dir()
    try:
        library = SampleLibrary(root)
        library.check()
    except SamplesMissing as exc:
        raise ScoreUnavailable(f"the score's sounds are not installed ({exc})") from exc

    speech, composition, events = events_for(words, voice, duration, style, seed, intensity)
    work_dir.mkdir(parents=True, exist_ok=True)
    groups = group_paths(output)
    raws = {group: work_dir / f"score.raw.{group}.wav" for group in groups}
    rms = render_events(events, duration, library, raws, seed)
    finals = {**groups, "sum": output}
    partials = {
        name: path.with_name(f"{path.stem}.partial{path.suffix}") for name, path in finals.items()
    }
    finished = finish(
        raws,
        partials,
        rms=rms,
        voice=voice,
        spans=speech.spans,
        pauses=speech.pauses,
        duration=duration,
        work_dir=work_dir,
    )
    for raw in raws.values():
        raw.unlink(missing_ok=True)
    for name, partial in partials.items():
        partial.replace(finals[name])
    tonic = NAMES[composition.tonic % 12]
    home = "minor" if style.home == "vi" else "major"
    key = f"{NAMES[(composition.tonic + 9) % 12]} minor" if home == "minor" else f"{tonic} major"
    log.info(
        "score.made",
        style=style.name,
        seed=seed,
        key=key,
        tempo=composition.tempo,
        sections=len(composition.sections),
        events=len(events),
    )
    return ScoreResult(
        path=output,
        groups=groups,
        landing=speech.landing,
        style=style.name,
        key=key,
        tempo=composition.tempo,
        sections=len(composition.sections),
        level_change_db=finished.level_change_db,
        pause_under_voice_db=finished.pause_under_voice_db,
    )
