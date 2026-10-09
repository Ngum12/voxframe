"""Manual cuts, real soundtrack stems and history work together in the studio."""
import hashlib
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_render_from_plan import _frame_count
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import revision
from voxframe.plan.trim import trim
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_trim_preview_history_export_and_separate_audio_downloads(server, page, caps):
    handle, work = server
    store = handle.store
    job = store.create(audio_name="editable-soundtrack.mp4", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(job.id)
    original = directed_recording(caps, folder)
    music = folder / "independent-music.wav"
    times = np.arange(7 * 48000) / 48000
    sf.write(music, .15 * np.sin(2 * np.pi * 220 * times), 48000)
    plan = original.model_copy(update={"music_path": str(music), "music_credit": "Test soundtrack"})
    path = plan.save(folder / "source.plan.json")
    source_hash = hashlib.sha256(Path(plan.audio_path).read_bytes()).hexdigest()
    rendered = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                               folder / "video.mp4", height=240, music=MusicSettings(path=music),
                               cache_dir=folder / "cache" / "segments")
    store.submit(job, lambda _: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": rendered.video_path}, warnings=(),
                        summary={"pending_edits": 0, "rendered_revision": revision(plan), "sound": rendered.sound})
    fixtures._home(page, handle)
    page.get_by_text("editable-soundtrack.mp4", exact=True).first.click()
    page.get_by_role("button", name="Edit music track", exact=True).click()
    fixtures.playwright_api.expect(page.get_by_role("tab", name="Sound", exact=True)).to_have_attribute("aria-selected", "true")
    page.get_by_text("Separate voice and music tracks", exact=True).click()
    tracks = page.get_by_role("region", name="Separate audio tracks", exact=True)
    fixtures.playwright_api.expect(tracks.get_by_role("link", name="Download music WAV", exact=True)).to_be_visible()
    tracks.get_by_label("voice track", exact=True).evaluate("audio => audio.play()")
    page.wait_for_function("() => document.querySelector('[aria-label=\"voice track\"]').currentTime > .1")
    tracks.get_by_label("music track", exact=True).evaluate("audio => audio.play()")
    assert tracks.get_by_label("voice track", exact=True).evaluate("audio => audio.paused")
    page.get_by_role("tab", name="Pacing", exact=True).click()
    section = page.get_by_role("region", name="Manual section trim", exact=True)
    section.get_by_label("Trim start seconds", exact=True).fill("2")
    section.get_by_label("Trim end seconds", exact=True).fill("4")
    section.get_by_role("button", name="Preview section edit", exact=True).click()
    player = section.get_by_label("Section edit preview", exact=True)
    player.wait_for(timeout=120_000)
    page.wait_for_function("() => document.querySelector('[aria-label=\"Section edit preview\"]').readyState >= 2")
    assert player.evaluate("video => video.duration") == pytest.approx(5, abs=.05)
    assert ScenePlan.load(path) == plan
    section.get_by_role("button", name="Save section edit", exact=True).click()
    undo = page.get_by_role("button", name="Undo", exact=True)
    fixtures.playwright_api.expect(undo).to_be_enabled()
    expected = trim(plan, 60, 120, "remove")
    assert ScenePlan.load(path) == expected
    with page.expect_response(lambda response: response.url.endswith("/plan/undo")) as undone:
        undo.click()
    assert undone.value.status == 200
    redo = page.get_by_role("button", name="Redo", exact=True)
    fixtures.playwright_api.expect(redo).to_be_enabled()
    assert ScenePlan.load(path) == plan
    with page.expect_response(lambda response: response.url.endswith("/plan/redo")) as redone:
        redo.click()
    assert redone.value.status == 200
    fixtures.playwright_api.expect(redo).to_be_disabled()
    assert ScenePlan.load(path) == expected
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "manual-section-trim-phone.png"), full_page=True)
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert _frame_count(caps, store.artifact_path(job.id, "video")) == 150
    assert hashlib.sha256(Path(plan.audio_path).read_bytes()).hexdigest() == source_hash
    page.get_by_role("tab", name="Sound", exact=True).click()
    page.get_by_text("Separate voice and music tracks", exact=True).click()
    for kind in ("voice", "music"):
        with page.expect_download() as downloaded:
            tracks.get_by_role("link", name=f"Download {kind} WAV", exact=True).click()
        destination = work / f"trimmed-{kind}.wav"
        downloaded.value.save_as(str(destination))
        assert sf.info(destination).duration == pytest.approx(5, abs=1 / 48000)
        if kind == "music":
            data, rate = sf.read(destination)
            if data.ndim > 1:
                data = data[:, 0]
            samples = data[rate:2 * rate]
            peak = np.fft.rfftfreq(len(samples), 1 / rate)[np.argmax(np.abs(np.fft.rfft(samples)))]
            assert peak == pytest.approx(220, abs=2)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "separate-audio-tracks-phone.png"), full_page=True)
    assert page._voxframe_errors == []

    # Applying sound remounts the panel while the render is still running.
    track_statuses = []
    page.on("response", lambda response: track_statuses.append(response.status)
            if response.url.endswith(f"/api/jobs/{job.id}/mix/tracks") else None)
    delayed = False

    def delay_panel_reload(route):
        nonlocal delayed
        if route.request.method != "GET" or delayed:
            route.continue_()
            return
        delayed = True
        response = route.fetch()
        # A slow panel request can complete after the renderer has started.
        deadline = time.monotonic() + 5
        while store.get(job.id).state.value != "running" and time.monotonic() < deadline:
            time.sleep(.01)
        route.fulfill(response=response)

    page.route(f"**/api/jobs/{job.id}/mix", delay_panel_reload)
    page.locator("#destination").select_option("podcast")
    page.get_by_role("button", name="Apply to the video", exact=True).click()
    page.locator(".studio-status").get_by_text("Updating your video").wait_for(timeout=30_000)
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    fixtures.playwright_api.expect(page.locator(".notice-error")).to_have_count(0)
    page.get_by_text("Separate voice and music tracks", exact=True).click()
    fixtures.playwright_api.expect(tracks.get_by_role("link", name="Download music WAV", exact=True)).to_be_visible()
    page.unroute(f"**/api/jobs/{job.id}/mix", delay_panel_reload)
    assert track_statuses and all(status == 200 for status in track_statuses), track_statuses

    # Replacing the music follows the same update lifecycle and refreshes the WAV.
    replacement = folder / "replacement-music.wav"
    sf.write(replacement, .15 * np.sin(2 * np.pi * 330 * times), 48000)
    page.get_by_label("Choose a music track", exact=True).set_input_files(str(replacement))
    fixtures.playwright_api.expect(page.get_by_role("button", name="Your track: replacement-music.wav", exact=True)).to_have_attribute("aria-pressed", "true")
    delayed = False
    page.route(f"**/api/jobs/{job.id}/mix", delay_panel_reload)
    page.get_by_role("button", name="Apply to the video", exact=True).click()
    page.locator(".studio-status").get_by_text("Updating your video").wait_for(timeout=30_000)
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    fixtures.playwright_api.expect(page.locator(".notice-error")).to_have_count(0)
    page.get_by_text("Separate voice and music tracks", exact=True).click()
    with page.expect_download() as downloaded:
        tracks.get_by_role("link", name="Download music WAV", exact=True).click()
    destination = work / "replacement-music-export.wav"
    downloaded.value.save_as(str(destination))
    data, rate = sf.read(destination)
    if data.ndim > 1:
        data = data[:, 0]
    samples = data[rate:2 * rate]
    peak = np.fft.rfftfreq(len(samples), 1 / rate)[np.argmax(np.abs(np.fft.rfft(samples)))]
    assert peak == pytest.approx(330, abs=2)
    assert track_statuses and all(status == 200 for status in track_statuses), track_statuses
    assert page._voxframe_errors == []
    page.unroute(f"**/api/jobs/{job.id}/mix", delay_panel_reload)
