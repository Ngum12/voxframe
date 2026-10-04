"""Reuse a saved track, audition under voice, and render through the real app."""
from __future__ import annotations

# Fixtures intentionally imported for pytest.
# ruff: noqa: F811
from pathlib import Path

import pytest

from tests.browser.test_use_my_video import (  # noqa: F401
    _home,
    _recording,
    caps,
    page,
    server,
)

pytestmark = pytest.mark.browser


def _job(handle, caps, name):  # type: ignore[no-untyped-def]
    from voxframe.config.style import get_template
    from voxframe.plan.scene_plan import Footage, PlannedScene, ScenePlan
    from voxframe.plan.shots import attach_footage
    from voxframe.render.compose import render_from_plan

    store = handle.store
    job = store.create(audio_name=name, options={"height": 240, "quality": "draft"})
    directory = store.job_directory(job.id)
    recording = _recording(caps, directory / "source.mp4")
    plan = attach_footage(ScenePlan(
        audio_path=str(recording), audio_sha256="0" * 64, audio_duration=6, fps=30,
        total_frames=180, scenes=(PlannedScene(index=0, start_frame=0, end_frame=180,
                                              text="hello this is my talk"),)),
        Footage(path=str(recording), width=640, height=360, fps=30, duration=6),
        cutaways=False)
    path = plan.save(directory / "source.plan.json")
    cache = directory / "cache" / "segments"
    cache.mkdir(parents=True)
    video = render_from_plan(plan, recording, get_template(), caps,
                             directory / "render.mp4", height=240, cache_dir=cache).video_path
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    return job, path


def test_save_audition_render_and_reuse(server, page, caps):  # type: ignore[no-untyped-def]
    from voxframe.plan.scene_plan import ScenePlan
    from voxframe.render.ffpath import run_ffmpeg

    handle, work = server
    first, plan_path = _job(handle, caps, "First music project.mp4")
    second, second_path = _job(handle, caps, "Second music project.mp4")
    track_file = work / "Evening glow.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-f", "lavfi", "-i", "sine=f=880:d=8",
                                "-y", str(track_file)])
    _home(page, handle)
    page.get_by_role("button", name="Library", exact=True).click()
    panel = page.locator(".music-library")
    panel.get_by_text("Save a music track", exact=True).click()
    panel.get_by_label("Music file (up to 200 MB)").set_input_files(str(track_file))
    panel.get_by_label("Track credit", exact=True).fill("Glow by Composer, CC0")
    panel.get_by_label("Track mood", exact=True).select_option("calm")
    panel.get_by_role("button", name="Save track", exact=True).click()
    panel.get_by_text("Track saved. You can use it in any project.").wait_for(timeout=30_000)
    row = panel.locator(".music-track").filter(has_text="Evening glow")
    row.wait_for()
    row.locator("audio").evaluate("audio => audio.load()")
    page.wait_for_function("() => document.querySelector('.music-track audio').readyState >= 1")
    assert row.locator("audio").evaluate("audio => audio.duration") == pytest.approx(8, abs=.05)
    track_file.unlink()  # Future selection no longer relies on the original file.

    _home(page, handle)
    page.get_by_text(first.audio_name, exact=True).first.click()
    page.get_by_role("tab", name="Sound", exact=True).click()
    sound = page.locator(".sound-panel")
    sound.get_by_text("Choose from your music library", exact=True).click()
    sound.get_by_role("button", name="Audition under my voice", exact=True).click()
    preview = sound.get_by_label("Sound preview", exact=True)
    preview.wait_for(timeout=30_000)
    page.wait_for_function("() => document.querySelector('audio[aria-label=\"Sound preview\"]').readyState >= 1")
    assert preview.evaluate("audio => audio.duration") == pytest.approx(6, abs=.05)
    assert not ScenePlan.load(plan_path).music_path  # Audition does not save an edit.
    sound.get_by_role("button", name="Apply to the video", exact=True).click()
    page.locator(".studio-status").get_by_text("Updating your video", exact=False).wait_for()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=180_000)
    saved = ScenePlan.load(plan_path)
    owned = Path(saved.music_path)
    assert owned.is_file() and saved.music_credit == "Glow by Composer, CC0"
    assert saved.score is None
    assert sound.get_by_role("button", name="Your track: Evening glow", exact=True).count() == 1
    # Verify the exported file contains both the voice (330 Hz) and track (880 Hz).
    import numpy as np
    import soundfile as sf

    output = handle.store.artifact_path(first.id, "video")
    decoded = work / "exported-mix.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-i", str(output), "-vn", "-ac", "1",
                                "-y", str(decoded)])
    samples, rate = sf.read(decoded)
    assert len(samples) / rate == pytest.approx(6, abs=.05)
    power = abs(np.fft.rfft(samples))
    frequencies = np.fft.rfftfreq(len(samples), 1 / rate)
    for frequency in (330, 880):
        peak = power[abs(frequencies - frequency) < 2].max()
        background = power[(abs(frequencies - frequency) > 5)
                           & (abs(frequencies - frequency) < 20)].mean()
        assert peak > 20 * background

    _home(page, handle)
    page.get_by_text(second.audio_name, exact=True).first.click()
    page.get_by_role("tab", name="Sound", exact=True).click()
    sound.get_by_text("Choose from your music library", exact=True).click()
    sound.get_by_role("button", name="Audition under my voice", exact=True).click()
    sound.get_by_label("Sound preview", exact=True).wait_for(timeout=30_000)
    sound.get_by_role("button", name="Apply to the video", exact=True).click()
    page.locator(".studio-status").get_by_text("Updating your video", exact=False).wait_for()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=180_000)
    assert ScenePlan.load(second_path).music_path == str(owned)
    page.screenshot(path=str(work / "reusable-music.png"), full_page=True)
    assert page._voxframe_errors == []
