"""Three real director previews, apply, edit, undo and export in Chromium."""
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_direction_render import directed_recording
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_direction_real_previews_edit_undo_export(server, page, caps) -> None:  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    job = store.create(audio_name="visual-director.mp4", options={"height": 240, "quality": "draft"})
    directory = store.job_directory(job.id)
    plan = directed_recording(caps, directory)
    path = plan.save(directory / "source.plan.json")
    video = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             directory / "video.mp4", height=240).video_path
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("visual-director.mp4", exact=True).first.click()
    page.get_by_role("tab", name="Director", exact=True).click()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    panel = page.get_by_role("region", name="Visual director")
    for look in ("Clean authority", "Cinematic story", "High energy"):
        panel.get_by_role("button", name=look, exact=False).click()
        panel.get_by_role("button", name="Preview direction", exact=True).click()
        preview = panel.get_by_label("Rendered director preview")
        preview.wait_for(timeout=120_000)
        page.wait_for_function("document.querySelector('[aria-label=\"Rendered director preview\"]').readyState >= 2")
        assert preview.evaluate("v => v.duration") == pytest.approx(7, abs=.1)
        assert ScenePlan.load(path) == plan
    panel.get_by_role("button", name="Apply direction", exact=True).click()
    page.wait_for_function("() => document.querySelectorAll('.timeline-clip').length === 3")
    directed = ScenePlan.load(path)
    assert len(directed.scenes) == 3
    panel.get_by_role("button", name="Cinematic story", exact=False).click()
    panel.get_by_role("button", name="Apply direction", exact=True).click()
    page.wait_for_function("() => document.querySelectorAll('.timeline-clip').length === 2")
    page.get_by_role("button", name="Undo", exact=True).click()
    page.wait_for_function("() => document.querySelectorAll('.timeline-clip').length === 3")
    assert ScenePlan.load(path) == directed
    page.locator(".timeline-clip").nth(1).click()
    page.get_by_role("tab", name="Director", exact=True).click()
    panel.get_by_label("Text beat", exact=True).fill("MAKE EVERY WORD COUNT")
    panel.get_by_label("Text placement", exact=True).select_option("top")
    panel.get_by_label("Speaker zoom", exact=True).fill("1.22")
    panel.get_by_role("button", name="Preview beat edit", exact=True).click()
    preview.wait_for(timeout=120_000)
    assert ScenePlan.load(path) == directed
    panel.get_by_role("button", name="Save beat", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_text("Your pinned beat", exact=False)).to_be_visible()
    edited = ScenePlan.load(path)
    assert edited.scenes[1].visual_beat.text == "MAKE EVERY WORD COUNT"
    assert edited.scenes[1].visual_beat.zoom == 1.22
    page.screenshot(path=str(work / "visual-director.png"), full_page=True)
    page.get_by_role("button", name="Undo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_text("Your pinned beat", exact=False)).to_have_count(0)
    assert ScenePlan.load(path) == directed
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_text("Your pinned beat", exact=False)).to_be_visible()
    assert ScenePlan.load(path) == edited
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert page._voxframe_errors == []
