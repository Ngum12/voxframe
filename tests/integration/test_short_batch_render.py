"""Distinct batch drafts preserve voice clocks and the reviewed music mix."""
from pathlib import Path

import numpy as np
import pytest

from tests.integration import test_footage_sync as sync
from tests.integration.test_complete_audition_render import audio, track
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_render_from_plan import _frame_count
from voxframe.config.settings import QualityPreset
from voxframe.config.style import get_template
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.short_batch import approved, record_preview
from voxframe.plan.shorts import bounds, revision, tokens
from voxframe.plan.storyboard import audition
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose import render_from_plan
from voxframe.render.compose.complete_preview import complete_preview

caps = sync.caps
pytestmark = pytest.mark.needs_ffmpeg


@pytest.mark.parametrize("first,last,clap", [(0, 4, 1.5), (5, 8, 4.5)])
def test_each_clip_preserves_speaker_sync_and_exact_reviewed_sound(
    caps, tmp_path: Path, first: int, last: int, clap: float,
) -> None:  # type: ignore[no-untyped-def]
    source = directed_recording(caps, tmp_path, delay=.6)
    source = source.model_copy(update={"audio_mix": AudioMix(voice_polish=False)})
    draft = audition(source, first, last, look="energy", match_captions=True)
    start, _ = bounds(source, tokens(source), first, last)
    voice = render_from_plan(draft, Path(draft.audio_path), get_template(), caps,
                             tmp_path / "voice.mp4", height=240)
    sync._assert_in_sync(caps, voice.video_path, [clap - start / draft.fps])
    music = track(tmp_path / "music.wav")
    draft = draft.model_copy(update={"music_path": str(music), "music_credit": "Test credit"})
    snapshot = complete_preview(draft, revision(source), tmp_path / "previews")
    clip_id = record_preview(tmp_path / "approvals", snapshot, first, last)
    chosen = approved(tmp_path / "approvals", tmp_path / "previews", [clip_id], revision(source))[0][1]
    exported = render_from_plan(chosen, Path(chosen.audio_path), get_template(), caps,
        tmp_path / "final.mp4", height=240, quality=QualityPreset.STANDARD,
        music=MusicSettings(path=music, credit=chosen.music_credit))
    assert _frame_count(caps, exported.video_path) == draft.total_frames
    assert chosen == draft and chosen.music_credit == "Test credit"
    preview_audio = audio(caps, tmp_path / "previews" / f"{snapshot['key']}.mp4", tmp_path / "preview.f32")
    final_audio = audio(caps, exported.video_path, tmp_path / "export.f32")
    count = min(len(preview_audio), len(final_audio))
    assert np.corrcoef(preview_audio[:count], final_audio[:count])[0, 1] > .99
    # Music must be audible between voice claps, rather than silently omitted.
    quiet = final_audio[:4800]
    spectrum = np.abs(np.fft.rfft(quiet))
    frequency = np.fft.rfftfreq(len(quiet), 1 / 48000)[int(np.argmax(spectrum))]
    assert frequency == pytest.approx(220, abs=10)
    assert np.sqrt(np.mean(quiet ** 2)) > .0001
