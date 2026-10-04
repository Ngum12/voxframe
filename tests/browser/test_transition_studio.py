"""A real transition preview, edit history and saved-plan export in Chromium."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_join_preview_save_history_and_export(server, page, caps) -> None:  # type: ignore[no-untyped-def]
    from tests.integration.test_transition_studio_render import plan as make_plan
    from voxframe.config.style import get_template
    from voxframe.plan.scene_plan import ScenePlan
    from voxframe.render.compose import render_from_plan

    handle, work = server
    store = handle.store
    job = store.create(audio_name="transition-studio.wav", options={"height": 240, "quality": "draft"})
    directory = store.job_directory(job.id)
    source = make_plan.__wrapped__(caps, directory)
    path = source.save(directory / "source.plan.json")
    video = render_from_plan(source, Path(source.audio_path), get_template(), caps,
                             directory / "video.mp4", height=240).video_path
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("transition-studio.wav", exact=True).first.click()
    page.get_by_role("tab", name="Transitions", exact=True).click()
    panel = page.get_by_role("region", name="Transition studio")
    panel.get_by_role("button", name="Slide", exact=False).first.click()
    panel.get_by_label("Direction", exact=True).select_option("right")
    panel.get_by_role("button", name="Preview this join", exact=True).click()
    preview = panel.get_by_label("Rendered transition preview")
    preview.wait_for(timeout=60_000)
    page.wait_for_function("document.querySelector('[aria-label=\"Rendered transition preview\"]').readyState >= 2")
    assert 1.9 <= preview.evaluate("v => v.duration") <= 2.2
    panel.get_by_role("button", name="Save for this join", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use video default")).to_be_visible()
    chosen = ScenePlan.load(path).scenes[0].transition_after
    assert chosen.kind == "slide" and chosen.direction == "right"
    page.get_by_role("button", name="Undo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use video default")).to_have_count(0)
    assert ScenePlan.load(path).scenes[0].transition_after is None
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use video default")).to_be_visible()
    panel.get_by_label("Choose join").select_option("1")
    panel.get_by_role("button", name="Cinematic", exact=False).click()
    panel.get_by_role("button", name="Apply to all joins", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Reset all to template")).to_be_visible()
    assert panel.get_by_label("Choose join").input_value() == "1"
    page.screenshot(path=str(work / "transition-studio.png"), full_page=True)
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    updated = ScenePlan.load(path)
    assert updated.transition_treatment.kind == "dip_to_black"
    assert all(s.transition_after is None for s in updated.scenes)
    assert updated.total_frames == source.total_frames
    assert updated.scenes[2].words == source.scenes[2].words
    assert page._voxframe_errors == []
