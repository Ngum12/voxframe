"""A finished export must not repeat speech samples into later silent sections."""
from pathlib import Path

import numpy as np
import pytest

sf = pytest.importorskip("soundfile")


def test_polished_export_has_no_recycled_speech_in_its_tail(tmp_path: Path):
    from voxframe.config.style import get_template
    from voxframe.plan.scene_plan import PlannedScene, ScenePlan
    from voxframe.render.compose import render_from_plan
    from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
    from voxframe.render.ffpath import run_ffmpeg

    try:
        caps = probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg unavailable")
    rate = 48000
    time = np.arange(4 * rate) / rate
    # Continuous voiced harmonics, then true silence. A globally selected
    # 'quietest two seconds' used to include the last syllable and loop it.
    speech = (.12 * np.sin(2 * np.pi * 180 * time)
              + .04 * np.sin(2 * np.pi * 540 * time))
    audio = tmp_path / "recording.wav"
    sf.write(audio, np.concatenate((speech, np.zeros(4 * rate))), rate, subtype="FLOAT")
    plan = ScenePlan(audio_path=str(audio), audio_sha256="1" * 64,
                     audio_duration=8, fps=30, total_frames=240,
                     scenes=(PlannedScene(index=0, start_frame=0, end_frame=240,
                                          text="A single phrase, followed by silence."),))
    result = render_from_plan(plan, audio, get_template(), caps, tmp_path / "export.mp4", height=240)
    decoded = tmp_path / "export-audio.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-i", str(result.video_path),
                                 "-vn", "-ac", "1", "-ar", str(rate), "-y", str(decoded)])
    samples, _ = sf.read(decoded)
    assert np.sqrt(np.mean(samples[:3 * rate] ** 2)) > .01
    assert np.max(np.abs(samples[6 * rate:7 * rate])) < 1e-6
