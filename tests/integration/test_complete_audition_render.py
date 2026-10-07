"""The complete draft contains audible music and matches the winning export mix."""
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tests.integration import test_footage_sync as sync
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_render_from_plan import _frame_count
from voxframe.config.settings import QualityPreset
from voxframe.config.style import get_template
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.complete_audition import CompleteChoice, audition
from voxframe.plan.shorts import revision
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose import render_from_plan
from voxframe.render.compose.complete_preview import complete_preview, winner
from voxframe.render.compose.short_preview import short_preview
from voxframe.render.ffpath import run_ffmpeg

caps = sync.caps
pytestmark = pytest.mark.needs_ffmpeg


def track(path: Path) -> Path:
    times = np.arange(12 * 48000) / 48000
    sound = .15 * np.sin(2 * np.pi * 220 * times)
    sf.write(path, sound, 48000)
    return path


def audio(caps, video: Path, output: Path) -> np.ndarray:  # type: ignore[no-untyped-def]
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-i", str(video), "-vn", "-ac", "1",
        "-ar", "48000", "-f", "f32le", "-y", str(output)])
    return np.fromfile(output, dtype=np.float32)


def test_music_is_audible_in_draft_and_exact_chosen_mix_matches_export(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = directed_recording(caps, tmp_path)
    music = track(tmp_path / "music.wav")
    source = source.model_copy(update={"music_path": str(music), "music_credit": "Local test track"})
    choice = CompleteChoice(revision=revision(source), look="energy", match_captions=True,
        mix=AudioMix(music_arc="punch", music_db=-6, voice_polish=False))
    draft = audition(source, choice)
    directory = tmp_path / "complete"
    snapshot = complete_preview(draft, revision(source), directory)
    video = directory / f"{snapshot['key']}.mp4"
    assert snapshot["has_music"]
    assert _frame_count(caps, video) == source.total_frames
    assert winner(directory, snapshot["key"], revision(source)) == draft
    before = video.stat().st_mtime_ns
    assert complete_preview(draft, revision(source), directory) == snapshot
    assert video.stat().st_mtime_ns == before
    voice_only = short_preview(draft, tmp_path / "voice-only")
    selected = winner(directory, snapshot["key"], revision(source))
    exported = render_from_plan(selected, Path(selected.audio_path), get_template(selected.style),
        caps, tmp_path / "final.mp4", music=MusicSettings(path=music, credit=selected.music_credit),
        height=480, quality=QualityPreset.DRAFT)
    actual = audio(caps, video, tmp_path / "preview.f32")
    final = audio(caps, exported.video_path, tmp_path / "export.f32")
    silent = audio(caps, voice_only, tmp_path / "voice.f32")
    quiet = slice(int(2.6 * 48000), int(2.8 * 48000))
    assert np.sqrt(np.mean(actual[quiet] ** 2)) > .001
    frequencies = np.fft.rfftfreq(len(actual[quiet]), 1 / 48000)
    peak = frequencies[np.argmax(np.abs(np.fft.rfft(actual[quiet])))]
    assert peak == pytest.approx(220, abs=5)
    assert np.sqrt(np.mean(silent[quiet] ** 2)) < 1e-5
    assert len(actual) == len(final)
    assert np.corrcoef(actual, final)[0, 1] > .999
    assert np.sqrt(np.mean((actual - final) ** 2)) < .001
    assert "Start" in exported.srt_path.read_text() and "now" in exported.srt_path.read_text()
    assert selected.audio_mix == choice.mix and selected.music_credit == "Local test track"


def test_explicit_no_music_draft_contains_only_the_recording(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = directed_recording(caps, tmp_path)
    source = source.model_copy(update={"music_path": str(track(tmp_path / "music.wav"))})
    choice = CompleteChoice(revision=revision(source), music_source="none",
                            mix=AudioMix(voice_polish=False))
    draft = audition(source, choice)
    directory = tmp_path / "complete"
    snapshot = complete_preview(draft, revision(source), directory)
    video = directory / f"{snapshot['key']}.mp4"
    assert not snapshot["has_music"] and "No added music" in snapshot["note"]
    assert _frame_count(caps, video) == source.total_frames
    sync._assert_in_sync(caps, video, list(sync.CLAPS))
