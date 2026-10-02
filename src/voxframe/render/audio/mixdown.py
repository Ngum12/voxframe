"""The sound of a video: stems, the person's mix, the loudness target, checks (D-171).

The mix is built from two stems laid on the video's timeline:

- **voice**: the recording, with the silences cards add (D-144);
- **music**: the bed at full level, already the right length -- cut on bars
  by the music director (D-170) or looped as before -- but not yet ducked.

Mixing then applies the person's settings from the plan (``AudioMix``): voice
and music level, and how far under the speech the music sits, ducked by the
words' own timings to exactly that distance. The result is brought to the
destination's loudness in two passes and limited below its true-peak ceiling.
Because the stems are kept, changing a setting re-mixes in seconds, and a
15-second preview in well under one, without touching a single picture.

Every mix is checked, and a failing one is flagged, never passed off as fine:
the speech over the music in every stretch, the integrated loudness, the true
peak, clipping, and a click at any edit in the music.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

if TYPE_CHECKING:
    # For annotations only: importing it at run time goes through the plan
    # package, which imports the renderer, which imports this module.
    from voxframe.plan.audio_mix import AudioMix
    from voxframe.render.encode.probe import FFmpegCapabilities

__all__ = ["RATE", "MixReport", "SpeechSpan", "Stems", "mixdown", "preview", "speech_levels"]

log = structlog.get_logger(__name__)

#: Every stem and the finished mix run at 48 kHz, video's own rate.
RATE = 48000

#: The bed's level before the person's music setting (D-100): 0.30, -10.5 dB.
BED_DB = 20 * math.log10(0.30)

#: The deepest the music is ever taken under speech.
DEEPEST_DB = -40.0

#: Mixing is done in blocks, so a long video never sits in memory whole.
BLOCK = 1 << 18

#: An edit in the music whose high-frequency energy jumps this far above its
#: surroundings is heard as a click.
CLICK_DB = 12.0

#: Loudness within this many LU of the target passes.
LOUDNESS_TOLERANCE = 1.0


@dataclass(frozen=True)
class SpeechSpan:
    """A stretch of speech, in video seconds, with both stems' levels over it.

    Levels are measured once, when the stems are made, at unity gain: the
    voice's typical level (median of its voiced 100 ms windows) and the
    music's loud moments (90th percentile), both dBFS.
    """

    start: float
    end: float
    voice_db: float
    music_db: float


@dataclass(frozen=True)
class Stems:
    """What a mix is made from, kept so the mix can change without a render."""

    voice: Path
    music: Path | None
    spans: tuple[SpeechSpan, ...]
    landing: float
    video_end: float
    fps: float
    #: Where the music was cut, video seconds: checked for clicks.
    joins: tuple[float, ...] = ()
    #: Integrated loudness of the voice alone, for a quick preview's level.
    voice_lufs: float = -23.0
    #: The polished voice (D-173), made once and kept like the other stems,
    #: with its own levels; ``None`` when polishing was not possible.
    voice_polished: Path | None = None
    spans_polished: tuple[SpeechSpan, ...] = ()
    voice_lufs_polished: float = -23.0
    #: What polishing measured and did (``PolishReport.as_dict``).
    polish: dict[str, Any] | None = None
    #: A generated score's group stems (D-179), the levels (dB) ``music`` was
    #: summed at, and each stretch of speech's power from each group at 0 dB.
    music_groups: tuple[tuple[str, Path], ...] = ()
    music_levels: tuple[tuple[str, float], ...] = ()
    group_power: tuple[tuple[float, ...], ...] = ()
    #: Gains to mix the groups at instead of reading ``music``: a preview of
    #: levels not yet applied. Never saved.
    play_groups: tuple[float, ...] = ()

    def for_mix(self, mix: AudioMix) -> Stems:
        """The stems this mix uses.

        The polished voice if polish is on and it was made; with polish off,
        the voice is the recording's own stem, untouched. A score's groups at
        the mix's levels, when they differ from the levels ``music`` holds.
        """
        stems = self
        if mix.voice_polish and self.voice_polished is not None:
            stems = replace(
                stems,
                voice=self.voice_polished,
                spans=self.spans_polished,
                voice_lufs=self.voice_lufs_polished,
            )
        levels = mix.score_levels.as_db()
        if self.music_groups and self.group_power and dict(levels) != dict(self.music_levels):
            stems = _at_levels(stems, dict(levels))
        return stems

    def save(self, path: Path) -> None:
        data = asdict(self)
        data["voice"] = str(self.voice)
        data["music"] = str(self.music) if self.music else None
        data["voice_polished"] = str(self.voice_polished) if self.voice_polished else None
        data["music_groups"] = [[group, str(p)] for group, p in self.music_groups]
        data.pop("play_groups")
        path.write_text(json.dumps(data), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Stems:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["voice"] = Path(data["voice"])
        data["music"] = Path(data["music"]) if data["music"] else None
        data["spans"] = tuple(SpeechSpan(**span) for span in data["spans"])
        data["joins"] = tuple(data["joins"])
        polished = data.get("voice_polished")
        data["voice_polished"] = Path(polished) if polished else None
        data["spans_polished"] = tuple(SpeechSpan(**s) for s in data.get("spans_polished", ()))
        data["music_groups"] = tuple((g, Path(p)) for g, p in data.get("music_groups", ()))
        data["music_levels"] = tuple((g, float(db)) for g, db in data.get("music_levels", ()))
        data["group_power"] = tuple(tuple(row) for row in data.get("group_power", ()))
        return cls(**data)


def _at_levels(stems: Stems, levels: dict[str, float]) -> Stems:
    """The stems with the score's groups at new levels, mixed on the fly.

    Each stretch's music level is estimated from its groups' powers, which is
    close enough for a preview. An applied change sums the groups again and
    measures the result exactly (``sum_groups``).
    """
    baked = dict(stems.music_levels)
    names = [group for group, _ in stems.music_groups]
    new = np.array([10 ** (levels.get(g, 0.0) / 10) for g in names])  # power gains
    old = np.array([10 ** (baked.get(g, 0.0) / 10) for g in names])
    adjusted = []
    for span, powers in zip(stems.spans, stems.group_power, strict=False):
        p = np.array(powers)
        before, after = float(np.sum(old * p)), float(np.sum(new * p))
        change = 10 * math.log10(after / before) if before > 0 and after > 0 else 0.0
        adjusted.append(replace(span, music_db=span.music_db + change))
    return replace(
        stems,
        spans=tuple(adjusted) + stems.spans[len(adjusted) :],
        play_groups=tuple(10 ** (levels.get(g, 0.0) / 20) for g in names),
    )


def group_powers(
    spans: Sequence[tuple[float, float]], groups: Sequence[tuple[str, Path]], sf: Any
) -> tuple[tuple[float, ...], ...]:
    """Each stretch's mean power from each group, for previewing new group levels."""
    found = []
    for start, end in spans:
        row = []
        for _, path in groups:
            rate = sf.info(str(path)).samplerate
            first = int(start * rate)
            part, _ = sf.read(
                str(path),
                start=first,
                stop=max(first + 1, int(end * rate)),
                dtype="float32",
                always_2d=True,
            )
            row.append(float(np.mean(part.astype(np.float64) ** 2)) if len(part) else 0.0)
        found.append(tuple(row))
    return tuple(found)


def sum_groups(groups: Sequence[tuple[str, Path]], levels: dict[str, float], output: Path) -> Path:
    """The score's groups summed at ``levels`` (dB), as one float WAV."""
    import soundfile as sf

    gains = [10 ** (levels.get(g, 0.0) / 20) for g, _ in groups]
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    sources = [sf.SoundFile(str(path)) for _, path in groups]
    try:
        with sf.SoundFile(str(partial), "w", samplerate=RATE, channels=2, subtype="FLOAT") as sink:
            while True:
                chunks = [s.read(BLOCK, dtype="float32", always_2d=True) for s in sources]
                if not len(chunks[0]):
                    break
                sink.write(sum(c * np.float32(g) for c, g in zip(chunks, gains, strict=True)))
    finally:
        for source in sources:
            source.close()
    partial.replace(output)
    return output


@dataclass
class MixReport:
    """The finished mix's measurements, and whether it passed."""

    destination: str
    target_lufs: float
    target_true_peak: float
    integrated_lufs: float
    true_peak: float
    #: The smallest distance of voice over music in any stretch of speech, dB;
    #: ``None`` without music.
    min_speech_margin_db: float | None
    speech_margin_setting_db: float
    clicks_at: list[float] = field(default_factory=list)
    clipped: bool = False
    #: Whether the polished voice was used, and what polishing did (D-173).
    voice_polished: bool = False
    polish: dict[str, Any] | None = None

    @property
    def problems(self) -> list[str]:
        """What failed, in words a person can act on."""
        found: list[str] = []
        if self.voice_polished and self.polish:
            found.extend(self.polish.get("problems", []))
        if abs(self.integrated_lufs - self.target_lufs) > LOUDNESS_TOLERANCE:
            found.append(
                f"The loudness came out at {self.integrated_lufs:.1f} LUFS, not the "
                f"{self.target_lufs:.0f} LUFS aimed for."
            )
        if self.true_peak > self.target_true_peak + 0.1:
            found.append(
                f"The loudest peak is {self.true_peak:.1f} dBTP, above the "
                f"{self.target_true_peak:.1f} dBTP ceiling; it may distort when shared."
            )
        if self.clipped:
            found.append("The sound clips (reaches full scale) somewhere.")
        if (
            self.min_speech_margin_db is not None
            and self.min_speech_margin_db < self.speech_margin_setting_db - 1.0
        ):
            found.append(
                f"In one stretch the music is only {self.min_speech_margin_db:.1f} dB "
                f"under the voice, less than the {self.speech_margin_setting_db:.0f} dB set."
            )
        if self.clicks_at:
            times = ", ".join(f"{t:.1f}s" for t in self.clicks_at[:5])
            found.append(f"A click may be heard in the music at {times}.")
        return found

    @property
    def passed(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["problems"] = self.problems
        data["passed"] = self.passed
        return data


# --- measuring -------------------------------------------------------------------


def window_levels(signal: np.ndarray, rate: int, window: float = 0.1) -> np.ndarray:
    """RMS level of each ``window``-second window, dBFS."""
    size = max(1, int(rate * window))
    usable = len(signal) - len(signal) % size
    if usable <= 0:
        return np.zeros(0)
    frames = signal[:usable].reshape(-1, size).astype(np.float64)
    levels: np.ndarray = 20 * np.log10(np.maximum(np.sqrt(np.mean(frames**2, axis=1)), 1e-7))
    return levels


def speech_levels(
    spans: Sequence[tuple[float, float]], voice: Path, music: Path | None, sf: Any
) -> tuple[SpeechSpan, ...]:
    """Measure both stems over each stretch of speech."""
    info = sf.info(str(voice))
    measured: list[SpeechSpan] = []
    for start, end in spans:
        first, last = int(start * info.samplerate), int(end * info.samplerate)
        voice_part, _ = sf.read(str(voice), start=first, stop=max(first + 1, last), dtype="float32")
        levels = window_levels(
            voice_part if voice_part.ndim == 1 else voice_part.mean(axis=1), info.samplerate
        )
        voice_db = float(np.median(levels[levels > levels.max() - 30])) if levels.size else -60.0
        music_db = -90.0
        if music is not None:
            part, rate = sf.read(
                str(music), start=first, stop=max(first + 1, last), dtype="float32", always_2d=True
            )
            music_levels = window_levels(part.mean(axis=1), rate)
            music_db = float(np.percentile(music_levels, 90)) if music_levels.size else -90.0
        measured.append(SpeechSpan(start, end, voice_db, music_db))
    return tuple(measured)


# --- the mix ---------------------------------------------------------------------


def _depths(stems: Stems, mix: AudioMix) -> list[Any]:
    """How far each stretch ducks, so the music sits ``speech_margin_db`` under it."""
    from voxframe.music.director import Span

    spans = []
    for span in stems.spans:
        music = span.music_db + BED_DB + mix.music_db
        voice = span.voice_db + mix.voice_db
        duck = min(0.0, voice - mix.speech_margin_db - music)
        spans.append(Span(span.start, span.end, max(DEEPEST_DB, duck)))
    return spans


def _music_envelope(stems: Stems, mix: AudioMix) -> tuple[np.ndarray, np.ndarray]:
    """The music's gain curve (seconds, dB), everything included."""
    from voxframe.music.director import gain_curve

    curve = gain_curve(_depths(stems, mix), stems.video_end, stems.landing, stems.fps)
    times = np.array([t for t, _ in curve])
    levels = np.array([db for _, db in curve]) + BED_DB + mix.music_db
    return times, levels


def _mix_block(
    stems: Stems,
    mix: AudioMix,
    first: int,
    last: int,
    envelope: tuple[np.ndarray, np.ndarray],
    sf: Any,
    *,
    voice_only: bool = False,
) -> np.ndarray:
    """Samples ``[first, last)`` of the mix, stereo, before loudness."""
    count = last - first
    voice, _ = sf.read(str(stems.voice), start=first, stop=last, dtype="float32", always_2d=True)
    voice = np.pad(voice, ((0, count - len(voice)), (0, 0)))[:, :1] * 10 ** (mix.voice_db / 20)
    out = np.repeat(voice, 2, axis=1)
    if stems.play_groups and not voice_only:
        music = np.zeros((count, 2), dtype=np.float32)
        for (_, path), gain in zip(stems.music_groups, stems.play_groups, strict=True):
            part, _ = sf.read(str(path), start=first, stop=last, dtype="float32", always_2d=True)
            music[: len(part)] += part[:, :2] * np.float32(gain)
        seconds = np.arange(first, last) / RATE
        gain_curve = 10 ** (np.interp(seconds, *envelope) / 20)
        out += music * gain_curve[:, None].astype(np.float32)
    elif stems.music is not None and not voice_only:
        music, _ = sf.read(
            str(stems.music), start=first, stop=last, dtype="float32", always_2d=True
        )
        music = np.pad(music, ((0, count - len(music)), (0, 0)))
        if music.shape[1] == 1:
            music = np.repeat(music, 2, axis=1)
        seconds = np.arange(first, last) / RATE
        gain = 10 ** (np.interp(seconds, *envelope) / 20)
        out += music[:, :2] * gain[:, None].astype(np.float32)
    result: np.ndarray = out.astype(np.float32)
    return result


def mixdown(stems: Stems, mix: AudioMix, output: Path, caps: FFmpegCapabilities) -> MixReport:
    """Mix the stems with the person's settings, to the destination's loudness.

    Writes ``output`` (48 kHz float WAV) and returns what was measured.
    """
    import soundfile as sf

    polished = mix.voice_polish and stems.voice_polished is not None
    polish = stems.polish
    stems = stems.for_mix(mix)
    total = round(stems.video_end * RATE)
    raw = output.with_suffix(".unleveled.wav")
    envelope = _music_envelope(stems, mix)
    with sf.SoundFile(str(raw), "w", samplerate=RATE, channels=2, subtype="FLOAT") as sink:
        for first in range(0, total, BLOCK):
            sink.write(_mix_block(stems, mix, first, min(total, first + BLOCK), envelope, sf))

    target = mix.target
    measured = _loudnorm_measure(raw, target.lufs, target.true_peak, caps)
    _loudnorm_apply(raw, output, target.lufs, target.true_peak, measured, caps)
    raw.unlink(missing_ok=True)

    integrated, true_peak = _loudness(output, caps)
    peak = _sample_peak(output, sf)
    report = MixReport(
        destination=mix.destination.value,
        target_lufs=target.lufs,
        target_true_peak=target.true_peak,
        integrated_lufs=round(integrated, 2),
        true_peak=round(true_peak, 2),
        min_speech_margin_db=_min_margin(stems, mix),
        speech_margin_setting_db=mix.speech_margin_db,
        clicks_at=_clicks(stems, sf),
        clipped=peak >= 0.999,
        voice_polished=polished,
        polish=polish,
    )
    log.info(
        "sound.mixed",
        lufs=report.integrated_lufs,
        true_peak=report.true_peak,
        margin=report.min_speech_margin_db,
        passed=report.passed,
        problems=len(report.problems),
    )
    return report


def preview(
    stems: Stems,
    mix: AudioMix,
    start: float,
    seconds: float,
    output: Path,
    *,
    voice_only: bool = False,
) -> Path:
    """A short stretch of the mix, for hearing a setting before applying it.

    Leveled with one gain rather than two loudness passes, so it is ready in
    well under a second: close to, not exactly, the finished level.
    """
    import soundfile as sf

    stems = stems.for_mix(mix)
    start = max(0.0, min(start, stems.video_end - 1.0))
    first = round(start * RATE)
    last = min(round(stems.video_end * RATE), first + round(seconds * RATE))
    block = _mix_block(
        stems, mix, first, last, _music_envelope(stems, mix), sf, voice_only=voice_only
    )
    # The voice dominates a spoken video's loudness, so leveling by it lands
    # the preview within a dB or two of the finished mix.
    gain = 10 ** ((mix.target.lufs - (stems.voice_lufs + mix.voice_db)) / 20)
    block = block * gain
    fade = min(len(block) // 4, int(0.02 * RATE))
    if fade:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)[:, None]
        block[:fade] *= ramp
        block[-fade:] *= ramp[::-1]
    peak = float(np.max(np.abs(block))) if block.size else 0.0
    if peak > 0.97:
        block *= 0.97 / peak
    sf.write(str(output), block, RATE, subtype="PCM_16")
    return output


def _min_margin(stems: Stems, mix: AudioMix) -> float | None:
    """Voice over music in the closest stretch, from the levels and the gains applied."""
    if stems.music is None or not stems.spans:
        return None
    margins = []
    for span, ducked in zip(stems.spans, _depths(stems, mix), strict=True):
        voice = span.voice_db + mix.voice_db
        music = span.music_db + BED_DB + mix.music_db + ducked.duck_db
        margins.append(float(voice - music))
    return round(min(margins), 2)


def _clicks(stems: Stems, sf: Any) -> list[float]:
    """Edits in the music whose high frequencies jump well above their surroundings."""
    if stems.music is None or not stems.joins:
        return []
    found = []
    for at in stems.joins:
        first = max(0, int((at - 0.15) * RATE))
        part, _ = sf.read(
            str(stems.music),
            start=first,
            stop=first + int(0.3 * RATE),
            dtype="float32",
            always_2d=True,
        )
        if len(part) < int(0.2 * RATE):
            continue
        high = np.diff(part.mean(axis=1))  # a first difference: mostly the high frequencies
        levels = window_levels(high, RATE, window=0.01)
        centre = int((at - first / RATE) / 0.01)
        if not 1 <= centre < len(levels) - 1:
            continue
        around = np.delete(levels, [centre - 1, centre, centre + 1])
        if around.size and levels[centre - 1 : centre + 2].max() - np.median(around) > CLICK_DB:
            found.append(round(at, 3))
    return found


def _sample_peak(path: Path, sf: Any) -> float:
    peak = 0.0
    with sf.SoundFile(str(path)) as source:
        for block in source.blocks(blocksize=BLOCK, dtype="float32"):
            peak = max(peak, float(np.max(np.abs(block))) if block.size else 0.0)
    return peak


# --- loudness, by FFmpeg ----------------------------------------------------------


def _loudnorm_measure(
    path: Path, lufs: float, true_peak: float, caps: FFmpegCapabilities
) -> dict[str, str]:
    from voxframe.render.ffpath import run_ffmpeg

    result = run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-nostats",
            "-i",
            str(path.resolve()),
            "-af",
            f"loudnorm=I={lufs}:TP={true_peak}:LRA=20:print_format=json",
            "-f",
            "null",
            "-",
        ],
    )
    found = re.findall(r"\{[^{}]*\}", result.stderr)
    if not found:
        raise RuntimeError("FFmpeg's loudness measurement returned nothing")
    data: dict[str, str] = json.loads(found[-1])
    return data


def _loudnorm_apply(
    source: Path,
    output: Path,
    lufs: float,
    true_peak: float,
    measured: dict[str, str],
    caps: FFmpegCapabilities,
) -> None:
    from voxframe.render.ffpath import run_ffmpeg

    # A little under the ceiling for the limiter: it limits samples, and the
    # true peak between samples sits slightly above them.
    ceiling = 10 ** ((true_peak - 0.5) / 20)
    chain = (
        f"loudnorm=I={lufs}:TP={true_peak}:LRA=20"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true,"
        f"aresample={RATE},"
        f"alimiter=limit={ceiling:.4f}:level=disabled:attack=1:release=50"
    )
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel",
            "error",
            "-i",
            str(source.resolve()),
            "-af",
            chain,
            "-c:a",
            "pcm_f32le",
            "-y",
            str(output.resolve()),
        ],
    )


def _loudness(path: Path, caps: FFmpegCapabilities) -> tuple[float, float]:
    """Integrated loudness (LUFS) and true peak (dBTP) of a file."""
    from voxframe.render.ffpath import run_ffmpeg

    result = run_ffmpeg(
        caps.ffmpeg_path,
        ["-nostats", "-i", str(path.resolve()), "-af", "ebur128=peak=true", "-f", "null", "-"],
    )
    summary = result.stderr.rsplit("Summary:", 1)[-1]
    integrated = re.search(r"I:\s*(-?[\d.]+|-inf)\s*LUFS", summary)
    peak = re.search(r"True peak:\s*Peak:\s*(-?[\d.]+|-inf)", summary)
    return (
        float(integrated.group(1)) if integrated and integrated.group(1) != "-inf" else -70.0,
        float(peak.group(1)) if peak and peak.group(1) != "-inf" else -70.0,
    )


def voice_loudness(path: Path, caps: FFmpegCapabilities) -> float:
    """Integrated loudness of a stem, LUFS."""
    return _loudness(path, caps)[0]
