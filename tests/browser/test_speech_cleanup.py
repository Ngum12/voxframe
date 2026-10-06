"""Review quoted removals, preview without saving, undo and export in Chromium."""
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.unit.test_speech_cleanup import speech
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import Footage, ScenePlan, Shot
from voxframe.render.compose import render_from_plan
from voxframe.render.ffpath import run_ffmpeg

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_review_speech_cuts_preview_save_undo_and_export(server, page, caps):
    handle, work = server
    store = handle.store
    job = store.create(audio_name="speech-cleanup.mp4", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(job.id)
    audio, speaker = folder / "voice.wav", folder / "speaker.mp4"
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=12", "-y", str(audio)])
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=160x90:rate=25:duration=12", "-an", "-c:v", "libx264",
        "-preset", "ultrafast", "-y", str(speaker)])
    plan = speech().model_copy(update={"audio_path": str(audio),
        "footage": Footage(path=str(speaker), width=160, height=90, fps=25, duration=12),
        "scenes": (speech().scenes[0].model_copy(update={"shot": Shot.SPEAKER}),)})
    path = plan.save(folder / "source.plan.json")
    video = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             folder / "video.mp4", height=120).video_path
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("speech-cleanup.mp4", exact=True).first.click()
    page.get_by_role("tab", name="Pacing", exact=True).click()
    panel = page.get_by_role("region", name="Shorts pacing")
    panel.get_by_role("checkbox").first.wait_for()
    assert panel.get_by_role("checkbox").count() == 3
    assert panel.get_by_role("checkbox", checked=True).count() == 0
    for checkbox in panel.get_by_role("checkbox").all():
        checkbox.check()
    panel.get_by_role("button", name="Preview selected cuts", exact=True).click()
    preview = panel.get_by_label("Pacing preview")
    preview.wait_for(timeout=120_000)
    page.wait_for_function("document.querySelector('[aria-label=\"Pacing preview\"]').readyState >= 2")
    assert 8 < preview.evaluate("v => v.duration") < 9
    assert ScenePlan.load(path) == plan
    panel.get_by_role("checkbox").first.uncheck()
    fixtures.playwright_api.expect(preview).to_have_count(0)
    panel.get_by_role("checkbox").first.check()
    page.set_viewport_size({"width": 390, "height": 844})
    panel.scroll_into_view_if_needed()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "speech-review-phone.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    panel.get_by_role("button", name="Save selected cuts", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("checkbox")).to_have_count(0)
    edited = ScenePlan.load(path)
    assert [w.text for s in edited.scenes for w in s.words] == "I think we can win.".split()
    page.get_by_role("button", name="Undo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("checkbox")).to_have_count(3)
    assert ScenePlan.load(path) == plan
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("checkbox")).to_have_count(0)
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert ScenePlan.load(path) == edited
    assert page._voxframe_errors == []
