"""Save a signature, reuse it on upload, and remove future choices."""
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_direction_render import directed_recording
from voxframe.config.captions import CAPTION_PRESETS
from voxframe.config.style import get_template
from voxframe.plan.audio_mix import AudioMix
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_save_reuse_and_delete_signature(server, page, caps):
    handle, work = server
    job = handle.store.create(audio_name="my-signature.mp4", options={"height": 240})
    folder = handle.store.job_directory(job.id)
    plan = directed_recording(caps, folder).model_copy(update={
        "caption_treatment": CAPTION_PRESETS["electric"], "audio_mix": AudioMix(music_arc="rise")})
    path = plan.save(folder / "source.plan.json")
    video = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             folder / "video.mp4", height=120).video_path
    handle.store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    handle.store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("my-signature.mp4", exact=True).first.click()
    page.get_by_role("tab", name="Export", exact=True).click()
    page.get_by_text("Keep this as your signature preset", exact=True).click()
    page.get_by_label("Preset name", exact=True).fill("My bold voice")
    page.get_by_role("button", name="Save creative preset", exact=True).click()
    fixtures.playwright_api.expect(page.get_by_text("“My bold voice” saved.", exact=False)).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(work / "signature-preset-phone.png"), full_page=True)
    fixtures._home(page, handle)
    page.locator('input[type="file"]').set_input_files(plan.audio_path)
    picker = page.get_by_role("region", name="Creative preset", exact=True)
    picker.get_by_label("Creative preset", exact=True).select_option(label="My bold voice")
    fixtures.playwright_api.expect(picker.get_by_text("pop captions · rise music arc · youtube sound")).to_be_visible()
    captured = []
    def capture(route):
        captured.append(route.request.post_data_json)
        route.fulfill(status=422, content_type="application/json", body='{"detail":"Captured for test"}')
    page.route("**/api/jobs", capture)
    page.get_by_role("button", name="Make the video", exact=True).click()
    page.wait_for_function("() => document.body.innerText.includes('Captured for test')")
    assert captured[0]["creative"]["caption_treatment"]["animation"] == "pop"
    assert captured[0]["creative"]["audio_mix"]["music_arc"] == "rise"
    # Return to settings after the deliberately refused submission.
    page.get_by_role("button", name="Make a video", exact=True).click()
    picker = page.get_by_role("region", name="Creative preset", exact=True)
    picker.get_by_label("Creative preset", exact=True).select_option(label="My bold voice")
    picker.get_by_text("Manage this preset", exact=True).click()
    picker.get_by_role("button", name="Delete selected preset", exact=True).click()
    fixtures.playwright_api.expect(picker.get_by_label("Creative preset", exact=True)).to_have_value("")
    page.reload()
    fixtures.playwright_api.expect(page.get_by_role("option", name="My bold voice", exact=True)).to_have_count(0)
    assert path.exists()
