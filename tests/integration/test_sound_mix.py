"""The sound path on a real render (D-171).

The public-domain sonnet with a generated track: the mix reaches the
destination's loudness under its true-peak ceiling, keeps the music the chosen
distance under every stretch of speech, and has no clicks; changing only the
mix re-renders only the sound, reusing the pictures whole; and a preview is
quick enough to follow a slider.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

pytest.importorskip("librosa", reason="music extra not installed")
soundfile = pytest.importorskip("soundfile")

from voxframe.plan.audio_mix import AudioMix, Destination  # noqa: E402
from voxframe.plan.scene_plan import ScenePlan  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SONNET = REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """The sonnet with music, then the same plan with only its mix changed."""
    from tests.integration.test_music_directed_render import generated_track
    from voxframe.config.settings import QualityPreset, get_settings
    from voxframe.config.style import get_template
    from voxframe.jobs.pipeline import JobOptions, render_plan, run_pipeline
    from voxframe.render.audio.music import MusicSettings
    from voxframe.render.encode.probe import probe_capabilities

    try:
        caps = probe_capabilities()
    except Exception as exc:  # pragma: no cover - FFmpeg is part of the dev setup
        pytest.skip(f"FFmpeg not available: {exc}")

    work = tmp_path_factory.mktemp("sound")
    track = work / "track.wav"
    generated_track(track, bars=24)
    settings = get_settings().model_copy(
        update={"transcribe_model": "base", "cache_path": work / "cache", "music_mode": "directed"}
    )
    output = work / "out" / "talk.mp4"
    first = run_pipeline(
        JobOptions(
            audio=SONNET, output=output, quality=QualityPreset.DRAFT, height=360,
            chapters=False, title="A Calendar of Sonnets", language="en",
            music=MusicSettings(path=track),
        ),
        settings, get_template("documentary"), caps,
    )
    pictures_after_first = sorted((work / "cache" / "pictures").glob("*.mp4"))
    polished_after_first = {
        path: path.stat().st_mtime_ns for path in (work / "cache" / "sound").glob("*_polish*.wav")
    }

    assert first.plan_path is not None
    plan = ScenePlan.load(first.plan_path)
    plan.model_copy(
        update={"audio_mix": AudioMix(music_db=4.0, speech_margin_db=10.0, destination=Destination.PODCAST)}
    ).save(first.plan_path)
    started = time.monotonic()
    second = render_plan(first.plan_path, output, settings, caps, quality=QualityPreset.DRAFT, height=360)
    remix_seconds = time.monotonic() - started
    pictures_after_second = sorted((work / "cache" / "pictures").glob("*.mp4"))
    return {
        "first": first, "second": second, "caps": caps, "output": output,
        "pictures": (pictures_after_first, pictures_after_second), "remix_seconds": remix_seconds,
        "polished": polished_after_first, "sound_cache": work / "cache" / "sound",
    }


def _frames(path: Path) -> int:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return int(json.loads(probe.stdout)["streams"][0]["nb_read_frames"])


def test_the_first_mix_passes_every_check(rendered) -> None:  # type: ignore[no-untyped-def]
    sound = rendered["first"].result.sound

    assert sound["passed"], sound["problems"]
    assert sound["integrated_lufs"] == pytest.approx(-14.0, abs=1.0)
    assert sound["true_peak"] <= -1.0 + 0.1
    assert sound["min_speech_margin_db"] == pytest.approx(15.0, abs=0.5)
    assert sound["clicks_at"] == []
    assert not any(w.startswith("Sound check") for w in rendered["first"].warnings)


def test_a_mix_change_reaches_its_new_settings(rendered) -> None:  # type: ignore[no-untyped-def]
    sound = rendered["second"].result.sound

    assert sound["passed"], sound["problems"]
    assert sound["destination"] == "podcast"
    assert sound["integrated_lufs"] == pytest.approx(-16.0, abs=1.0)
    assert sound["min_speech_margin_db"] == pytest.approx(10.0, abs=0.5)


def test_a_mix_change_reuses_the_pictures(rendered) -> None:  # type: ignore[no-untyped-def]
    """No picture work for a sound change: the same single captioned video."""
    before, after = rendered["pictures"]

    assert len(before) == 1
    assert after == before


def test_the_video_still_has_every_frame(rendered) -> None:  # type: ignore[no-untyped-def]
    plan = ScenePlan.load(rendered["second"].plan_path)

    assert _frames(rendered["output"]) == plan.total_frames


def test_a_preview_is_quick_enough_for_a_slider(rendered) -> None:  # type: ignore[no-untyped-def]
    from voxframe.render.audio.mixdown import Stems, preview
    from voxframe.render.compose.from_plan import stems_path

    stems = Stems.load(stems_path(rendered["output"]))
    target = rendered["output"].parent / "preview.wav"

    started = time.monotonic()
    preview(stems, AudioMix(music_db=-6.0), 10.0, 15.0, target)
    took = time.monotonic() - started

    audio, rate = soundfile.read(target)
    assert len(audio) == pytest.approx(15 * rate, abs=2)
    assert took < 2.0, f"a preview took {took:.2f}s"


# --- voice polish (D-173) --------------------------------------------------------


def test_the_voice_is_polished_by_default(rendered) -> None:  # type: ignore[no-untyped-def]
    sound = rendered["first"].result.sound
    assert sound["voice_polished"] is True
    assert sound["polish"] is not None and sound["polish"]["problems"] == []


def test_the_polished_voice_is_made_once(rendered) -> None:  # type: ignore[no-untyped-def]
    """A mix change finds the polished voice in the cache, and never polishes again."""
    after_first = rendered["polished"]
    assert len(after_first) == 1
    after_second = {
        path: path.stat().st_mtime_ns for path in rendered["sound_cache"].glob("*_polish*.wav")
    }
    assert after_second == after_first


def test_polish_off_is_the_recording_itself(rendered) -> None:  # type: ignore[no-untyped-def]
    """With polish off, the voice is the recording, only placed on the timeline.

    The stem is compared with the recording decoded at the same rate: it has to
    match sample for sample, up to the resampler's rounding, where it plays.
    """
    import numpy as np

    from voxframe.render.audio.mixdown import RATE, Stems
    from voxframe.render.compose.from_plan import stems_path

    stems = Stems.load(stems_path(rendered["output"]))
    assert stems.for_mix(AudioMix(voice_polish=False)).voice == stems.voice
    assert stems.voice_polished is not None and stems.voice_polished != stems.voice

    decoded = subprocess.run(
        [rendered["caps"].ffmpeg_path, "-v", "error", "-i", str(SONNET), "-af",
         f"aresample={RATE},aformat=channel_layouts=mono", "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    recording = np.frombuffer(decoded, dtype=np.float32)
    stem, _ = soundfile.read(str(stems.voice), dtype="float32")
    # Where the recording starts on the timeline: after any opening card.
    probe = recording[RATE : RATE * 3]
    scores = np.correlate(stem[: RATE * 20], probe, mode="valid")
    offset = int(np.argmax(scores)) - RATE
    length = min(len(recording), len(stem) - offset) - RATE
    difference = np.abs(stem[offset : offset + length] - recording[:length])
    assert float(np.max(difference)) < 1e-3
    assert float(np.mean(difference)) < 1e-5
