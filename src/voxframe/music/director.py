"""The music director, Stage 1: a bed edited to the speaker (D-170).

The person's own track, made to fit the talk the way an editor would:

- **Cut on bars only.** The bed is built from whole bars of the track. A talk
  longer than the track repeats whole phrases, from the loop point whose
  harmony matches best; a shorter talk drops whole phrases. Every join is a
  20 ms equal-power crossfade, too short to hear as a fade and long enough to
  hide a click.
- **Lands on the last word.** A phrase-starting downbeat is placed exactly on
  the frame where the last word ends, then the music rings out. The start
  shifts by less than one bar to make that exact, under the fade-in.
- **Ducks by measurement.** Under each stretch of speech the music is lowered
  just enough to sit 15 dB below that stretch's own measured level, so a quiet
  passage is not buried and a loud one is not fought. Nothing ducks between
  talks, and short gaps stay ducked so the bed does not pump.
- **Swells into pauses.** A pause of 1.5 s or more lets the music rise to its
  full level and fall back before the next word.
- **Frame-grid exact.** Every event is placed on a frame of the video
  (D-013), after the pauses cards add (D-144).

A track without a steady beat is not cut on bars it does not have: the caller
falls back to the plain looped bed (D-100) and says why.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import structlog

from voxframe.music.analysis import BARS_PER_PHRASE, TrackAnalysis, analyse_track

if TYPE_CHECKING:
    from voxframe.plan.scene_plan import ScenePlan
    from voxframe.render.audio.music import MusicSettings
    from voxframe.render.encode.probe import FFmpegCapabilities

__all__ = [
    "DIRECTOR_VERSION",
    "DirectedBed",
    "MusicPlan",
    "NotDirectable",
    "direct_music",
    "gain_curve",
    "joins_of",
    "paired_spans",
    "plan_edits",
    "plan_words",
    "render_bed",
    "speech_spans",
]

log = structlog.get_logger(__name__)

#: Bumped whenever the edit or the automation changes, so cached beds are not
#: reused.
DIRECTOR_VERSION = 3

#: Music sits at least this far under the speech during every stretch of it.
SPEECH_MARGIN_DB = 15.0

#: The deepest the music is ever taken, in case the measurement goes wrong.
DEEPEST_DB = -40.0

#: Words closer than this belong to one stretch of speech: ducking between
#: them would pump.
JOIN_GAP = 0.6

#: A pause this long gets a swell.
SWELL_MIN_GAP = 1.5
#: The swell starts this long after the last word, rises over this long, and
#: is back down this long before the next word.
SWELL_DELAY = 0.15
SWELL_RISE = 0.40
DUCK_LEAD = 0.30
DUCK_READY = 0.05

#: Crossfade at a join between bars that were not adjacent in the track.
JOIN_CROSSFADE = 0.020

FADE_IN = 1.5
#: After the landing the music rings on for at least this long, then is gone.
RING_OUT = 2.5

#: The bed is rendered in blocks of this many samples, so an hour-long track
#: never has to fit in memory.
BLOCK = 1 << 18


class NotDirectable(RuntimeError):
    """The track cannot be edited to the speech; the plain bed is used."""


@dataclass(frozen=True)
class Piece:
    """A stretch of the track, placed on the video's timeline."""

    source_start: float
    source_end: float
    at: float

    @property
    def length(self) -> float:
        return self.source_end - self.source_start


def joins_of(plan: MusicPlan) -> tuple[float, ...]:
    """Where the bed was cut, video seconds: every point one piece meets the next."""
    return tuple(piece.at for piece in plan.pieces[1:])


@dataclass(frozen=True)
class MusicPlan:
    """How the bed is cut: pieces of the track, and where it lands."""

    pieces: tuple[Piece, ...]
    landing: float
    repeats: int = 0
    removed_bars: int = 0
    lead_in: float = 0.0


@dataclass(frozen=True)
class DirectedBed:
    """A rendered bed, and what was done to make it."""

    path: Path
    plan: MusicPlan
    note: str = ""


@dataclass
class _Bars:
    starts: list[float]
    ends: list[float]
    chroma: list[tuple[float, ...]] = field(default_factory=list)

    def length(self, index: int) -> float:
        return self.ends[index] - self.starts[index]


def _bars(analysis: TrackAnalysis) -> _Bars:
    """Whole bars only: the partial bar at the end is left out."""
    downbeats = list(analysis.downbeats)
    starts = downbeats[:-1]
    ends = downbeats[1:]
    return _Bars(starts, ends, list(analysis.bar_chroma[: len(starts)]))


def plan_edits(analysis: TrackAnalysis, landing: float, video_end: float) -> MusicPlan:
    """Cut the track into a bed that lands on ``landing`` and lasts to ``video_end``.

    Args:
        analysis: The track's bars; it must be ``steady``.
        landing: Seconds into the video where a phrase must start: the end of
            the last word.
        video_end: Length of the video.

    Raises:
        NotDirectable: If the track is too short to have phrases to work with.
    """
    bars = _bars(analysis)
    count = len(bars.starts)
    phrase = BARS_PER_PHRASE
    if count < 2 * phrase:
        raise NotDirectable("the track is too short to cut into phrases")

    bar = float(np.median([bars.length(i) for i in range(count)]))
    needed = video_end + phrase * bar

    sequence = list(range(count))
    repeats = 0
    if _duration(bars, sequence) < needed:
        loop_start, loop_stop = _loop(bars, count, phrase)
        body = list(range(loop_start, loop_stop))
        head, tail = sequence[:loop_stop], sequence[loop_stop:]
        while _duration(bars, head + tail) < needed:
            head += body
            repeats += 1
        sequence = head + tail

    # The first phrase start at or after the landing, counted in the edited
    # sequence -- each phrase of it begins with a bar that began a phrase.
    times = np.cumsum([0.0] + [bars.length(i) for i in sequence])
    index = next(
        (i for i in range(0, len(sequence), phrase) if times[i] >= landing), None
    )
    if index is None:
        raise NotDirectable("the track could not be made long enough")
    excess = float(times[index] - landing)

    # Take the excess out in whole phrases from the middle, then whole bars
    # from the first phrase; what is left, under one bar, is where the music
    # starts before the video does, hidden by the fade-in.
    removed = 0
    while excess >= phrase * bar * 0.999 and index >= 2 * phrase:
        cut = list(range(phrase, 2 * phrase))
        span = sum(bars.length(sequence[i]) for i in cut)
        if span > excess + 1e-6:
            break
        sequence = sequence[:phrase] + sequence[2 * phrase :]
        index -= phrase
        excess -= span
        removed += phrase
    position = 1
    while position < phrase and position < index:
        length = bars.length(sequence[position])
        if length > excess + 1e-6:
            break
        del sequence[position]
        index -= 1
        excess -= length
        removed += 1
    lead_in = max(0.0, excess)

    pieces: list[Piece] = []
    at = -lead_in
    for bar_index in sequence:
        if at >= video_end:
            break
        start, end = bars.starts[bar_index], bars.ends[bar_index]
        if pieces and abs(pieces[-1].source_end - start) < 1e-6:
            last = pieces[-1]
            pieces[-1] = Piece(last.source_start, end, last.at)
        else:
            pieces.append(Piece(start, end, at))
        at += end - start

    return MusicPlan(tuple(pieces), landing, repeats, removed, lead_in)


def _duration(bars: _Bars, sequence: Sequence[int]) -> float:
    return float(sum(bars.length(i) for i in sequence))


def _loop(bars: _Bars, count: int, phrase: int) -> tuple[int, int]:
    """The phrase span to repeat: where jumping back sounds most like carrying on.

    Jumping from the end of bar ``stop - 1`` to bar ``start`` replaces the bar
    that would have followed (``stop``) with ``start``; the pair whose harmony
    is most alike is the least audible edit. The first phrase (an intro) and
    the last (an ending) are kept out of the loop when the track has room.
    """
    last_start = count - phrase
    starts = range(phrase if count >= 3 * phrase else 0, last_start, phrase)
    best = (0, (count // phrase) * phrase)
    best_score = -2.0
    for start in starts:
        for stop in range(start + phrase, last_start + 1, phrase):
            if stop >= count or not bars.chroma:
                continue
            score = float(np.dot(bars.chroma[stop], bars.chroma[start]))
            # A longer loop repeats less often: slightly preferred.
            score += 0.01 * (stop - start) / phrase
            if score > best_score:
                best, best_score = (start, stop), score
    return best


# --- the gain curve -------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """A stretch of speech, in video seconds, and how far to duck under it."""

    start: float
    end: float
    duck_db: float = 0.0


def speech_spans(words: Sequence[tuple[float, float]], join_gap: float = JOIN_GAP) -> list[Span]:
    """Words (video seconds) merged into stretches of speech."""
    spans: list[Span] = []
    for start, end in sorted(words):
        if spans and start - spans[-1].end < join_gap:
            spans[-1] = Span(spans[-1].start, max(spans[-1].end, end))
        else:
            spans.append(Span(start, end))
    return spans


def paired_spans(
    video_words: Sequence[tuple[float, float]], source_words: Sequence[tuple[float, float]]
) -> list[tuple[Span, Span]]:
    """Stretches of speech in video time and in the recording's time, grouped once.

    Grouping each timeline separately could disagree: a gap of almost exactly
    ``JOIN_GAP`` merges on one side of a float rounding and not the other, and
    from there on every stretch was measured against the wrong stretch of voice
    (found in testing: 11 video stretches against 10 recorded ones, and one
    stretch 3.3 dB louder than intended). The groups come from the recording
    and are carried to the video.
    """
    order = sorted(range(len(source_words)), key=lambda i: source_words[i])
    groups: list[list[int]] = []
    for index in order:
        if groups and source_words[index][0] - max(
            source_words[i][1] for i in groups[-1]
        ) < JOIN_GAP:
            groups[-1].append(index)
        else:
            groups.append([index])
    return [
        (
            Span(min(video_words[i][0] for i in group), max(video_words[i][1] for i in group)),
            Span(min(source_words[i][0] for i in group), max(source_words[i][1] for i in group)),
        )
        for group in groups
    ]


def gain_curve(
    spans: Sequence[Span], video_end: float, landing: float, fps: float
) -> list[tuple[float, float]]:
    """Breakpoints ``(seconds, dB)`` of the bed's level, on the frame grid.

    Ducked under each span by its own depth; a swell to full level in every
    pause of ``SWELL_MIN_GAP`` or more; short gaps stay ducked. Before the
    first word and after the landing the level is full, and the fades are
    applied separately.
    """

    def snap(seconds: float) -> float:
        return round(seconds * fps) / fps

    points: list[tuple[float, float]] = [(0.0, 0.0)]
    for index, span in enumerate(spans):
        down_start = max(points[-1][0], span.start - DUCK_LEAD)
        down_end = max(down_start, span.start - DUCK_READY)
        if index == 0 or points[-1][1] >= -0.01:
            points.append((snap(down_start), points[-1][1]))
        points.append((snap(down_end), span.duck_db))
        points.append((snap(span.end), span.duck_db))

        following = spans[index + 1] if index + 1 < len(spans) else None
        if following is None:
            break
        gap = following.start - span.end
        if gap >= SWELL_MIN_GAP:
            rise_start = span.end + SWELL_DELAY
            rise_end = rise_start + SWELL_RISE
            points.append((snap(rise_start), span.duck_db))
            points.append((snap(rise_end), 0.0))
            # Down again before the next stretch: its own lead handles that.
        # A short gap: the next span's lead starts from here, still ducked.

    last = spans[-1] if spans else None
    if last is not None:
        release = max(points[-1][0], last.end + SWELL_DELAY)
        points.append((snap(release), points[-1][1]))
        points.append((snap(min(release + SWELL_RISE, max(landing, release + 0.05))), 0.0))
    points.append((snap(video_end), points[-1][1]))

    # Monotonic times for interpolation.
    cleaned: list[tuple[float, float]] = []
    for time, level in points:
        if cleaned and time < cleaned[-1][0]:
            time = cleaned[-1][0]
        cleaned.append((time, level))
    return cleaned


# --- rendering ------------------------------------------------------------------


def direct_music(
    plan: ScenePlan,
    music: MusicSettings,
    narration: Path,
    caps: FFmpegCapabilities,
    work_dir: Path,
    cache_dir: Path,
    *,
    automate: bool = True,
    rate: int | None = None,
) -> DirectedBed:
    """Render the directed bed for this plan, reusing a cached one if unchanged.

    Raises:
        NotDirectable: With a plain reason, when the plain bed should be used.
    """
    from voxframe.components import activate_music

    if not activate_music():
        raise NotDirectable("the music-fitting tools are not downloaded yet")

    return render_bed(
        video_words=_video_words(plan),
        source_words=_source_words(plan),
        source_end=plan.audio_duration,
        video_end=plan.total_frames / plan.fps,
        fps=plan.fps,
        narration=narration,
        track_file=music.path,
        gain=music.gain,
        caps=caps,
        cache_dir=cache_dir,
        automate=automate,
        rate=rate,
    )


def render_bed(
    *,
    video_words: Sequence[tuple[float, float]],
    source_words: Sequence[tuple[float, float]],
    video_end: float,
    fps: float,
    narration: Path,
    source_end: float | None = None,
    track_file: Path,
    gain: float,
    caps: FFmpegCapabilities,
    cache_dir: Path,
    automate: bool = True,
    rate: int | None = None,
) -> DirectedBed:
    """The directed bed for these words, without needing a scene plan.

    Args:
        video_words: Each word's start and end where it plays in the video.
        source_words: The same words in the recording's own time, to measure
            the voice.
        source_end: Length of the recording. Whisper can time the last words
            past the end of the audio (measured: 2.6 s past a 45 s recording);
            they are clamped to it, so the music never lands after the voice
            has stopped, or after the video has ended.
        video_end: Length of the video, seconds.
        fps: The video's frame rate; every event lands on its frame grid.
        narration: The recording.
        track_file: The music, in any format FFmpeg reads.
        gain: The bed's level before ducking.
        caps: Probed FFmpeg.
        cache_dir: Where decoded audio, analyses and beds are kept.
        automate: Duck and swell the bed here. Off gives the bed cut, faded
            and landed but at full level, for a mix that applies the person's
            own settings to it (D-171).
        rate: Render at this sample rate; ``None`` keeps the track's own.

    Raises:
        NotDirectable: With a plain reason, when the plain bed should be used.
    """
    import soundfile

    words, source_words = _clamped(list(video_words), list(source_words), source_end, video_end)
    if not words:
        raise NotDirectable("the plan has no timed words")
    last_frame = int(video_end * fps) - 1
    landing = min(round(max(end for _, end in words) * fps), last_frame) / fps

    cache = cache_dir / "music"
    track = _decoded(track_file, caps, cache, limit=video_end + 180.0, rate=rate)
    analysis = analyse_track(track, cache_dir=cache / "analysis")
    if not analysis.steady:
        raise NotDirectable(f"the track was looped as before, because {analysis.why_not_steady}")

    key = _bed_key(track, words if automate else [], video_end, fps, gain if automate else 1.0)
    bed = cache / "beds" / f"{key}.wav"
    edits = plan_edits(analysis, landing, video_end)
    if bed.is_file():
        log.info("music.bed.reused", bed=bed.name)
        return DirectedBed(bed, edits)

    if automate:
        speech = _decoded(narration, caps, cache / "speech", rate=16000, mono=True)
        spans = _duck_depths(
            paired_spans(words, list(source_words)), speech, track, edits, gain, soundfile
        )
        curve = gain_curve(spans, video_end, landing, fps)
    else:
        spans = []
        curve = [(0.0, 0.0), (video_end, 0.0)]
        gain = 1.0
    bed.parent.mkdir(parents=True, exist_ok=True)
    _render(track, edits, curve, video_end, gain, bed, soundfile)
    log.info(
        "music.directed",
        tempo=round(analysis.tempo, 1),
        bars=len(analysis.downbeats),
        pieces=len(edits.pieces),
        repeats=edits.repeats,
        removed_bars=edits.removed_bars,
        lead_in=round(edits.lead_in, 3),
        landing=landing,
        swells=sum(
            1 for a, b in pairwise(spans) if b.start - a.end >= SWELL_MIN_GAP
        ),
    )
    return DirectedBed(bed, edits)


def plan_words(plan: ScenePlan) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """The plan's words in video time and in the recording's time, clamped."""
    return _clamped(
        _video_words(plan), _source_words(plan), plan.audio_duration, plan.total_frames / plan.fps
    )


def _clamped(
    video: list[tuple[float, float]],
    source: list[tuple[float, float]],
    source_end: float | None,
    video_end: float,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Both timelines' words, none past the end of the recording or the video."""
    if len(video) != len(source):
        raise NotDirectable("the words' timings do not line up")
    kept_video: list[tuple[float, float]] = []
    kept_source: list[tuple[float, float]] = []
    for (v_start, v_end), (s_start, s_end) in zip(video, source, strict=True):
        overrun = 0.0
        if source_end is not None:
            overrun = max(0.0, s_end - source_end)
        overrun = max(overrun, v_end - video_end)
        if s_end - overrun <= s_start or v_end - overrun <= v_start:
            continue
        kept_video.append((v_start, v_end - overrun))
        kept_source.append((s_start, s_end - overrun))
    return kept_video, kept_source


def _video_words(plan: ScenePlan) -> list[tuple[float, float]]:
    """Every spoken word's start and end, moved to where it plays in the video.

    Cards add silence to the speech where they are inserted (D-144), so a word
    plays later by the length of every card before it.
    """
    pauses = sorted(plan.card_pauses())

    def shift(seconds: float) -> float:
        return seconds + sum(length for at, length in pauses if at <= seconds + 1e-6)

    return [
        (shift(word.start), shift(word.end))
        for scene in plan.scenes
        if not scene.is_card
        for word in scene.words
    ]


def _source_words(plan: ScenePlan) -> list[tuple[float, float]]:
    return [
        (word.start, word.end)
        for scene in plan.scenes
        if not scene.is_card
        for word in scene.words
    ]


def _duck_depths(
    pairs: list[tuple[Span, Span]],
    speech: Path,
    track: Path,
    edits: MusicPlan,
    gain: float,
    soundfile: object,
) -> list[Span]:
    """How deep each span ducks: 15 dB under that stretch's measured speech."""
    sf = soundfile
    voice, voice_rate = sf.read(str(speech), dtype="float32", always_2d=False)  # type: ignore[attr-defined]
    info = sf.info(str(track))  # type: ignore[attr-defined]

    depths: list[Span] = []
    for span, original in pairs:
        voice_db = _speech_level(voice, voice_rate, original.start, original.end)
        music_db = _music_level(track, edits, span.start, span.end, info.samplerate, gain, sf)
        duck = min(0.0, voice_db - SPEECH_MARGIN_DB - music_db)
        depths.append(Span(span.start, span.end, max(DEEPEST_DB, duck)))
    return depths


def _speech_level(voice: np.ndarray, rate: int, start: float, end: float) -> float:
    """Typical level of the voiced parts of a stretch, dBFS (RMS, 100 ms)."""
    chunk = voice[int(start * rate) : int(end * rate)]
    levels = _window_levels(chunk, rate)
    if levels.size == 0:
        return -30.0
    voiced = levels[levels > levels.max() - 30.0]
    return float(np.median(voiced))


def _music_level(
    track: Path, edits: MusicPlan, start: float, end: float, rate: int, gain: float, sf: object
) -> float:
    """The bed's loud moments over a stretch, before ducking, dBFS (90th percentile)."""
    samples = _assemble(track, edits, int(start * rate), int(end * rate), rate, sf)
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    levels = _window_levels(mono * gain, rate)
    if levels.size == 0:
        return -80.0
    return float(np.percentile(levels, 90))


def _window_levels(signal: np.ndarray, rate: int, window: float = 0.1) -> np.ndarray:
    size = max(1, int(rate * window))
    usable = len(signal) - len(signal) % size
    if usable <= 0:
        return np.zeros(0)
    frames = signal[:usable].reshape(-1, size)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    levels: np.ndarray = 20 * np.log10(np.maximum(rms, 1e-7))
    return levels


def _assemble(
    track: Path, edits: MusicPlan, first: int, last: int, rate: int, sf: object
) -> np.ndarray:
    """Samples ``[first, last)`` of the edited bed, joins crossfaded."""
    channels = sf.info(str(track)).channels  # type: ignore[attr-defined]
    out = np.zeros((max(0, last - first), channels), dtype=np.float32)
    half = int(JOIN_CROSSFADE * rate / 2)
    pieces = edits.pieces
    for index, piece in enumerate(pieces):
        at = round(piece.at * rate)
        length = round(piece.length * rate)
        joins_before = index > 0
        joins_after = index + 1 < len(pieces)
        begin = at - (half if joins_before else 0)
        end = at + length + (half if joins_after else 0)
        if end <= first or begin >= last:
            continue
        source_begin = round(piece.source_start * rate) - (at - begin)
        lo, hi = max(begin, first), min(end, last)
        read_from = source_begin + (lo - begin)
        if read_from < 0:
            lo += -read_from
            read_from = 0
        if hi <= lo:
            continue
        data, _ = sf.read(  # type: ignore[attr-defined]
            str(track), start=read_from, stop=read_from + (hi - lo),
            dtype="float32", always_2d=True,
        )
        if len(data) < hi - lo:
            data = np.pad(data, ((0, hi - lo - len(data)), (0, 0)))
        envelope = np.ones(hi - lo, dtype=np.float32)
        positions = np.arange(lo, hi)
        if joins_before:
            fade = (positions - (at - half)) / max(1, 2 * half)
            mask = fade < 1
            envelope[mask] = np.sin(np.clip(fade[mask], 0, 1) * np.pi / 2)
        if joins_after:
            fade = ((at + length + half) - positions) / max(1, 2 * half)
            mask = fade < 1
            envelope[mask] *= np.sin(np.clip(fade[mask], 0, 1) * np.pi / 2)
        out[lo - first : hi - first] += data[:, :channels] * envelope[:, None]
    return out


def _render(
    track: Path,
    edits: MusicPlan,
    curve: list[tuple[float, float]],
    video_end: float,
    gain: float,
    output: Path,
    sf: object,
) -> None:
    info = sf.info(str(track))  # type: ignore[attr-defined]
    rate = int(info.samplerate)
    total = round(video_end * rate)
    times = np.array([t for t, _ in curve])
    levels = np.array([db for _, db in curve])
    ring_end = min(video_end, edits.landing + RING_OUT)
    partial = output.with_suffix(".partial.wav")
    with sf.SoundFile(  # type: ignore[attr-defined]
        str(partial), "w", samplerate=rate, channels=info.channels, subtype="FLOAT"
    ) as sink:
        for first in range(0, total, BLOCK):
            last = min(total, first + BLOCK)
            block = _assemble(track, edits, first, last, rate, sf)
            seconds = np.arange(first, last) / rate
            envelope = 10 ** (np.interp(seconds, times, levels) / 20) * gain
            envelope *= np.clip(seconds / FADE_IN, 0, 1)
            if ring_end > edits.landing:
                fade = 1 - (seconds - edits.landing) / (ring_end - edits.landing)
                envelope *= np.where(seconds < edits.landing, 1.0, np.clip(fade, 0, 1))
            sink.write(block * envelope[:, None].astype(np.float32))
    partial.replace(output)


def _decoded(
    source: Path,
    caps: FFmpegCapabilities,
    folder: Path,
    *,
    limit: float | None = None,
    rate: int | None = None,
    mono: bool = False,
) -> Path:
    """The file as uncompressed WAV, decoded by FFmpeg once and kept.

    Any format FFmpeg reads works, and the analysis and the render read the
    same samples.
    """
    from voxframe.render.ffpath import run_ffmpeg

    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    digest.update(f"|{limit}|{rate}|{mono}".encode())
    target = folder / f"{digest.hexdigest()[:24]}.wav"
    if target.is_file():
        return target
    folder.mkdir(parents=True, exist_ok=True)
    arguments = ["-loglevel", "error", "-i", str(source.resolve())]
    if limit is not None:
        arguments += ["-t", f"{limit:.3f}"]
    if rate is not None:
        arguments += ["-ar", str(rate)]
    if mono:
        arguments += ["-ac", "1"]
    partial = target.with_suffix(".partial.wav")
    run_ffmpeg(caps.ffmpeg_path, [*arguments, "-c:a", "pcm_f32le", "-y", str(partial.resolve())])
    partial.replace(target)
    return target


def _bed_key(
    track: Path, words: list[tuple[float, float]], video_end: float, fps: float, gain: float
) -> str:
    payload = json.dumps(
        {
            "track": track.name,
            "words": [(round(a, 4), round(b, 4)) for a, b in words],
            "end": round(video_end, 4),
            "fps": fps,
            "gain": gain,
            "version": DIRECTOR_VERSION,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:24]
