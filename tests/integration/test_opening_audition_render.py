"""Opening drafts and exports keep audible music, narration and footage sync."""
from pathlib import Path

import numpy as np
import pytest

from tests.integration import test_footage_sync as sync
from tests.integration.test_complete_audition_render import audio, track
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_render_from_plan import _frame_count
from voxframe.config.style import get_template
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.opening_audition import OpeningChoice, audition
from voxframe.plan.shorts import revision
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose import render_from_plan
from voxframe.render.compose.complete_preview import complete_preview, winner

caps = sync.caps
pytestmark = pytest.mark.needs_ffmpeg


@pytest.mark.parametrize("look", ["authority", "energy", "cinema"])
def test_opening_snapshot_export_has_same_narration_frames_and_real_music(caps, tmp_path, look):  # type: ignore[no-untyped-def]
    original = directed_recording(caps, tmp_path, delay=.6)
    music = track(tmp_path / "music.wav")
    original = original.model_copy(update={"music_path": str(music), "music_credit": "Original artist",
        "audio_mix": AudioMix(voice_polish=False, music_db=-6)})
    draft, opening = audition(original, OpeningChoice(revision=revision(original), last_word=3,
                                                    look=look, shot="speaker"))
    folder = tmp_path / "previews"
    snapshot = complete_preview(draft, revision(original), folder)
    assert snapshot["has_music"] and opening["quote"] == "Start with 3 ideas"
    chosen = winner(folder, snapshot["key"], revision(original))
    settings = MusicSettings(path=music, credit=original.music_credit)
    baseline = render_from_plan(original, Path(original.audio_path), get_template(), caps,
        tmp_path / "baseline.mp4", height=240, music=settings)
    exported = render_from_plan(chosen, Path(chosen.audio_path), get_template(), caps,
        tmp_path / "exported.mp4", height=240, music=settings)
    preview = folder / f"{snapshot['key']}.mp4"
    assert _frame_count(caps, preview) == _frame_count(caps, exported.video_path) == original.total_frames
    actual = audio(caps, preview, tmp_path / "preview.f32")
    final = audio(caps, exported.video_path, tmp_path / "export.f32")
    reference = audio(caps, baseline.video_path, tmp_path / "baseline.f32")
    assert len(actual) == len(final) == len(reference)
    assert np.corrcoef(actual, final)[0, 1] > .999
    assert np.corrcoef(reference, final)[0, 1] > .995
    quiet = actual[int(2.6 * 48000):int(2.8 * 48000)]
    frequencies = np.fft.rfftfreq(len(quiet), 1 / 48000)
    assert frequencies[np.argmax(np.abs(np.fft.rfft(quiet)))] == pytest.approx(220, abs=5)
    assert np.sqrt(np.mean(quiet ** 2)) > .001
    assert chosen.music_credit == original.music_credit and chosen.audio_mix == original.audio_mix
    assert "Dialogue: 2," in exported.ass_path.read_text()
    assert "now" in exported.srt_path.read_text()


def test_two_beat_opening_keeps_delayed_speaker_claps_in_sync(caps, tmp_path):  # type: ignore[no-untyped-def]
    original = directed_recording(caps, tmp_path, delay=.6)
    draft, _ = audition(original, OpeningChoice(revision=revision(original), last_word=3, look="energy"))
    exported = render_from_plan(draft, Path(draft.audio_path), get_template(), caps,
                               tmp_path / "opening.mp4", height=480)
    sync._assert_in_sync(caps, exported.video_path, list(sync.CLAPS))
    assert _frame_count(caps, exported.video_path) == original.total_frames
