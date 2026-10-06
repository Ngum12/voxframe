"""Saved-edit cues link to the right scene, survive navigation and refresh after fixes."""
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_direction_render import directed_recording
from voxframe.config.captions import CaptionTreatment
from voxframe.config.style import CaptionPosition, get_template
from voxframe.config.visuals import VisualBeat
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_finish_review_navigation_fix_refresh_phone_and_export(server, page, caps):
    handle, work = server
    store = handle.store
    job = store.create(audio_name="finishing-review.mp4", options={"height": 720, "quality": "draft"})
    folder = store.job_directory(job.id)
    base = directed_recording(caps, folder)
    scene = base.scenes[0]
    first = scene.model_copy(update={"end_frame": 105, "words": scene.words[:5],
                                     "text": " ".join(w.text for w in scene.words[:5])})
    second = scene.model_copy(update={"index": 1, "start_frame": 105, "footage_start": 3.5,
        "words": scene.words[5:], "text": " ".join(w.text for w in scene.words[5:]),
        "caption_treatment": CaptionTreatment(position=CaptionPosition.CENTER),
        "visual_beat": VisualBeat(text="Make it count", position="center")})
    plan = base.model_copy(update={"scenes": (first, second)})
    path = plan.save(folder / "source.plan.json")
    video = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             folder / "video.mp4", height=240).video_path
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("finishing-review.mp4", exact=True).first.click()
    page.get_by_role("tab", name="Export", exact=True).click()
    report = page.get_by_role("region", name="Finishing review")
    cue = report.locator("li").filter(has_text="Two text layers share a placement")
    cue.wait_for()
    assert ScenePlan.load(path) == plan
    cue.get_by_role("button", name="Mark checked", exact=True).click()
    cue.get_by_role("button", name="Review in Director", exact=True).click()
    director = page.get_by_role("region", name="Visual director")
    fixtures.playwright_api.expect(director.get_by_label("Text beat", exact=True)).to_have_value("Make it count")
    page.get_by_role("tab", name="Export", exact=True).click()
    fixtures.playwright_api.expect(cue.get_by_role("button", name="Checked", exact=False)).to_have_attribute("aria-pressed", "true")
    cue.get_by_role("button", name="Review in Director", exact=True).click()
    director.get_by_label("Text placement", exact=True).select_option("auto")
    director.get_by_role("button", name="Save beat", exact=True).click()
    fixtures.playwright_api.expect(page.get_by_role("button", name="Undo", exact=True)).to_be_enabled()
    page.get_by_role("tab", name="Export", exact=True).click()
    fixtures.playwright_api.expect(cue).to_have_count(0)
    fixtures.playwright_api.expect(report.locator(".finish-receipt")).to_contain_text("0 checked")
    fixtures.playwright_api.expect(report).to_contain_text("waiting for Update video")
    report.get_by_role("button", name="Captions", exact=False).click()
    fixtures.playwright_api.expect(report).to_contain_text("No cues in this category")
    report.get_by_role("button", name="All", exact=False).click()
    page.set_viewport_size({"width": 390, "height": 844})
    report.scroll_into_view_if_needed()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "finishing-review-phone.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert ScenePlan.load(path).scenes[1].visual_beat.position == "auto"
    assert page._voxframe_errors == []
