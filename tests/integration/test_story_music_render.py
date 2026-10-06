"""Story music changes the exported audio while reusing identical pictures."""
from pathlib import Path

import pytest

from tests.integration import test_footage_sync as sync
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_music_directed_render import generated_track
from tests.integration.test_render_from_plan import _frame_count
from voxframe.config.style import get_template
from voxframe.plan.audio_mix import AudioMix
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose import render_from_plan
from voxframe.render.ffpath import run_ffmpeg

caps = sync.caps
pytestmark = pytest.mark.needs_ffmpeg


def test_arcs_change_exported_music_keep_pictures_and_exact_duration(caps, tmp_path):
    plan = directed_recording(caps, tmp_path)
    track = tmp_path / "track.wav"
    generated_track(track, bars=8)
    plan = plan.model_copy(update={"music_path": str(track), "music_credit": "Test"})
    video_hashes, audio_hashes = [], []
    for arc in ("steady", "rise", "punch"):
        edited = plan.model_copy(update={"audio_mix": AudioMix(music_arc=arc, voice_polish=False)})
        result = render_from_plan(edited, Path(edited.audio_path), get_template(), caps,
            tmp_path / f"{arc}.mp4", height=120, music=MusicSettings(path=track), cache_dir=tmp_path / "cache" / "segments")
        assert _frame_count(caps, result.video_path) == edited.total_frames
        for stream, hashes in (("v", video_hashes), ("a", audio_hashes)):
            digest = tmp_path / f"{arc}-{stream}.hash"
            run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-i", str(result.video_path),
                "-map", f"0:{stream}:0", "-c", "copy", "-f", "hash", "-y", str(digest)])
            hashes.append(digest.read_text())
    assert len(set(video_hashes)) == 1
    assert len(set(audio_hashes)) == 3
