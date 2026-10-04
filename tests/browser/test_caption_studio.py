"""Caption styling through the browser and a real saved-plan export."""
from __future__ import annotations

import pytest

from tests.browser import test_use_my_video as fixtures

pytestmark = pytest.mark.browser
caps = fixtures.caps
server = fixtures.server
page = fixtures.page


def test_style_preview_save_undo_and_export(server, page, caps) -> None:  # type: ignore[no-untyped-def]
    from voxframe.config.style import get_template
    from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan
    from voxframe.render.compose import render_from_plan

    handle, work = server
    store = handle.store
    job = store.create(audio_name="caption-studio.mp4", options={"height": 240, "quality": "draft"})
    directory = store.job_directory(job.id)
    recording = fixtures._recording(caps, directory / "source.mp4")
    plan = ScenePlan(
        audio_path=str(recording), audio_sha256="0" * 64, audio_duration=6, fps=30,
        total_frames=180, scenes=(PlannedScene(index=0, start_frame=0, end_frame=180,
            text="Make every word matter", words=tuple(PlanWord(text=t, start=a, end=b)
                for t, a, b in (("Make", .2, .7), ("every", 1, 1.5),
                                ("word", 2, 2.5), ("matter", 3, 4)))),),
    )
    plan_path = plan.save(directory / "source.plan.json")
    video = render_from_plan(plan, recording, get_template(), caps,
                             directory / "video.mp4", height=240).video_path
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": plan_path, "video": video}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("caption-studio.mp4", exact=True).first.click()
    page.get_by_role("tab", name="Captions", exact=True).click()
    panel = page.get_by_role("region", name="Caption studio")
    panel.get_by_role("button", name="Electric").click()
    panel.get_by_role("group", name="Emphasis words").get_by_role("button", name="matter", exact=True).click()
    panel.get_by_role("button", name="Render exact preview").click()
    preview = panel.get_by_label("Rendered caption preview")
    preview.wait_for(timeout=60_000)
    page.wait_for_function("document.querySelector('[aria-label=\"Rendered caption preview\"]').readyState >= 2")
    assert preview.evaluate("v => v.duration") == pytest.approx(6, abs=.1)
    panel.get_by_role("button", name="Save for this scene", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use video look")).to_be_visible()
    saved = ScenePlan.load(plan_path)
    assert saved.scenes[0].caption_treatment.animation == "pop"
    assert saved.scenes[0].caption_emphasis == (3,)
    page.get_by_role("button", name="Undo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use video look")).to_have_count(0)
    assert ScenePlan.load(plan_path).scenes[0].caption_treatment is None
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use video look")).to_be_visible()
    assert ScenePlan.load(plan_path).scenes[0].caption_treatment.animation == "pop"
    panel.get_by_role("button", name="Cinema").click()
    panel.get_by_role("button", name="Apply look to whole video", exact=True).click()
    page.wait_for_function("document.querySelector('.caption-studio select').value === 'typewriter'")
    page.screenshot(path=str(work / "caption-studio.png"), full_page=True)
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    exported = ScenePlan.load(plan_path)
    assert exported.caption_treatment.animation == "typewriter"
    assert exported.scenes[0].words == plan.scenes[0].words
    assert page._voxframe_errors == []
