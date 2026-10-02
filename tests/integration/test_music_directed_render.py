"""The music director on real audio (D-170).

A generated track with known bars stands in for the owner's music, so the
test knows the truth and commits no one's recording. Measured the way D-100
measured ducking: on the isolated bed, because the mixed file is dominated by
the speech and cannot show what the music under it is doing.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("librosa", reason="music extra not installed")
soundfile = pytest.importorskip("soundfile")

from voxframe.music import director  # noqa: E402
from voxframe.music.analysis import analyse_track  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SONNET = REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav"
RATE = 44100


def generated_track(path: Path, *, bpm: float = 118.0, bars: int = 40, steady: bool = True) -> list[float]:
    """A 4/4 track: kick on 1 and 3, snare on 2 and 4, a chord per bar.

    With ``steady`` off, every beat lands at a random distance from where it
    should, as rubato playing does, and the director must refuse to cut it.

    Returns:
        The true start of every bar, in seconds.
    """
    generator = np.random.default_rng(7)
    beat = 60 / bpm
    starts: list[float] = []
    times: list[float] = []
    clock = 0.0
    for _bar in range(bars):
        starts.append(clock)
        for _ in range(4):
            times.append(clock)
            clock += beat * (generator.uniform(0.6, 1.4) if not steady else 1.0)
    total = int((clock + 1.0) * RATE)
    out = np.zeros(total, dtype=np.float32)
    t = np.arange(int(0.25 * RATE)) / RATE
    kick = (np.sin(2 * np.pi * 55 * t) * np.exp(-t * 18)).astype(np.float32)
    snare = (generator.normal(0, 1, len(t)) * np.exp(-t * 30) * 0.4).astype(np.float32)
    chords = [(261.6, 329.6, 392.0), (220.0, 261.6, 329.6), (174.6, 220.0, 261.6), (196.0, 246.9, 293.7)]
    for index, start in enumerate(times):
        position = int(start * RATE)
        hit = kick if index % 2 == 0 else snare
        out[position : position + len(hit)] += hit[: total - position]
    for bar, start in enumerate(starts):
        span = int(4 * beat * RATE)
        s = int(start * RATE)
        tt = np.arange(min(span, total - s)) / RATE
        out[s : s + len(tt)] += sum(np.sin(2 * np.pi * f * tt) for f in chords[bar % 4]) * 0.05
    soundfile.write(path, np.stack([out, out], axis=1) * 0.5, RATE)
    return starts


class TestAnalysis:
    def test_bars_are_found_where_they_are(self, tmp_path: Path) -> None:
        truth = generated_track(tmp_path / "steady.wav")

        analysis = analyse_track(tmp_path / "steady.wav")

        assert analysis.steady
        assert analysis.beats_per_bar == 4
        assert analysis.tempo == pytest.approx(118, abs=1.5)
        found = np.array(analysis.downbeats)
        errors = [float(np.min(np.abs(found - start))) for start in truth[1:-1]]
        within = sum(error < 0.020 for error in errors)
        assert within >= 0.95 * len(errors), f"{within}/{len(errors)} bar starts within 20 ms"

    def test_an_unsteady_track_is_not_cut_on_bars(self, tmp_path: Path) -> None:
        generated_track(tmp_path / "rubato.wav", steady=False)

        analysis = analyse_track(tmp_path / "rubato.wav")

        assert not analysis.steady
        assert analysis.why_not_steady


@pytest.fixture(scope="module")
def directed(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """The sonnet, rendered with a directed bed, and what the director did."""
    from voxframe.config.settings import QualityPreset, get_settings
    from voxframe.config.style import get_template
    from voxframe.jobs.pipeline import JobOptions, run_pipeline
    from voxframe.render.audio.music import MusicSettings
    from voxframe.render.encode.probe import probe_capabilities

    try:
        caps = probe_capabilities()
    except Exception as exc:  # pragma: no cover - FFmpeg is part of the dev setup
        pytest.skip(f"FFmpeg not available: {exc}")

    work = tmp_path_factory.mktemp("directed")
    track = work / "track.wav"
    generated_track(track, bars=24)  # 49 s: shorter than the video with its title card
    seen: dict[str, object] = {}
    real = director.direct_music

    def spy(plan, music, narration, caps, work_dir, cache_dir, **options):  # type: ignore[no-untyped-def]
        bed = real(plan, music, narration, caps, work_dir, cache_dir, **options)
        seen.update(bed=bed, plan=plan, caps=caps, cache=cache_dir)
        return bed

    patch = pytest.MonkeyPatch()
    patch.setattr(director, "direct_music", spy)
    settings = get_settings().model_copy(
        update={"transcribe_model": "base", "cache_path": work / "cache", "music_mode": "directed"}
    )
    try:
        outcome = run_pipeline(
            JobOptions(
                audio=SONNET, output=work / "out.mp4", quality=QualityPreset.DRAFT,
                height=360, chapters=False, title="A Calendar of Sonnets", language="en",
                music=MusicSettings(path=track),
            ),
            settings,
            get_template("documentary"),
            caps,
        )
    finally:
        patch.undo()
    assert "bed" in seen, "the director did not run"
    from voxframe.render.compose.from_plan import stems_path

    seen["stems"] = stems_path(work / "out.mp4")
    return outcome, seen


def _levels(signal: np.ndarray, rate: int, start: float, end: float) -> np.ndarray:
    return director._window_levels(signal[int(start * rate) : int(end * rate)], rate)


def test_the_video_is_made_with_no_music_warning(directed) -> None:  # type: ignore[no-untyped-def]
    outcome, _ = directed

    assert outcome.result.video_path.is_file()
    assert not any("music" in warning.lower() for warning in outcome.warnings), outcome.warnings


def test_the_music_lands_on_the_last_word(directed) -> None:  # type: ignore[no-untyped-def]
    _, seen = directed
    plan, bed = seen["plan"], seen["bed"]
    video_words, _ = director.plan_words(plan)
    last_word = max(end for _, end in video_words)

    assert abs(bed.plan.landing - last_word) <= 1 / plan.fps + 1e-9
    assert bed.plan.landing * plan.fps == pytest.approx(round(bed.plan.landing * plan.fps))


def _ducked(seen: dict[str, object]) -> tuple[object, np.ndarray, np.ndarray, int]:
    """The music as heard in the finished video, before loudness: the kept bed
    with the mix's gain curve applied (D-171), and the voice stem."""
    from voxframe.plan.audio_mix import AudioMix
    from voxframe.render.audio import mixdown

    stems = mixdown.Stems.load(seen["stems"])  # type: ignore[arg-type]
    assert stems.music is not None
    music, rate = soundfile.read(str(stems.music), always_2d=True)
    voice, _ = soundfile.read(str(stems.voice), always_2d=True)
    times, levels = mixdown._music_envelope(stems, AudioMix())
    gain = 10 ** (np.interp(np.arange(len(music)) / rate, times, levels) / 20)
    return stems, music.mean(axis=1) * gain, voice.mean(axis=1), rate


def test_the_music_stays_15_db_under_every_stretch_of_speech(directed) -> None:  # type: ignore[no-untyped-def]
    """Measured on the stems, as D-100 did: the mixed file is dominated by the
    speech and cannot show the music under it."""
    _, seen = directed
    stems, music, voice, rate = _ducked(seen)

    margins = []
    for span in stems.spans:  # type: ignore[attr-defined]
        heard = _levels(music, rate, span.start, span.end)
        spoken = _levels(voice, rate, span.start, span.end)
        spoken = spoken[spoken > spoken.max() - 30]
        margins.append(float(np.median(spoken) - np.percentile(heard, 90)))

    assert min(margins) >= 15.0 - 0.5, [round(m, 1) for m in margins]


def test_pauses_swell_and_are_quiet_again_by_the_next_word(directed) -> None:  # type: ignore[no-untyped-def]
    """Measured as the gain applied, so the track's own loudness from bar to bar
    does not stand in for the director's."""
    from voxframe.plan.audio_mix import AudioMix
    from voxframe.render.audio import mixdown

    _, seen = directed
    stems = mixdown.Stems.load(seen["stems"])  # type: ignore[arg-type]
    times, levels = mixdown._music_envelope(stems, AudioMix())
    full = mixdown.BED_DB

    def applied(start: float, end: float) -> np.ndarray:
        return np.interp(np.arange(start, end, 0.01), times, levels) - full

    spans = list(stems.spans)
    pauses = [
        (a.end, b.start) for a, b in pairwise(spans) if b.start - a.end >= director.SWELL_MIN_GAP
    ]

    assert pauses, "the sonnet has a long pause to swell into"
    for end, start in pauses:
        swell = applied(end + director.SWELL_DELAY + director.SWELL_RISE, start - director.DUCK_LEAD)
        at_next = applied(start, start + 0.3)
        assert np.max(swell) >= -1.0, "the music did not rise to its full level"
        assert np.max(at_next) <= -3.0, "the music was not ducked again by the next word"
