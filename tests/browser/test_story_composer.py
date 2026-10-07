"""Compose, preview and export a story through the real browser and renderer."""
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


def test_compose_preview_save_undo_redo_and_export(server, page, caps):  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    job = store.create(audio_name="compose-story.wav", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(job.id)
    original = directed_recording(caps, folder)
    scene = original.scenes[0]
    words = tuple(w.model_copy(update={"text": "ideas."}) if i == 3 else w
                  for i, w in enumerate(scene.words))
    original = original.model_copy(update={"scenes": (scene.model_copy(update={
        "text": " ".join(w.text for w in words), "words": words}),)})
    path = original.save(folder / "source.plan.json")
    result = render_from_plan(original, Path(original.audio_path),
                             get_template(), caps, folder / "video.mp4", height=120)
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": result.video_path},
                        warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("compose-story.wav", exact=True).first.click()
    page.get_by_role("tab", name="Shorts", exact=True).click()
    page.locator(".story-composer > summary").click()
    panel = page.get_by_role("region", name="Story Composer", exact=True)
    fixtures.playwright_api.expect(panel.locator(".story-block")).to_have_count(2)
    panel.get_by_role("button", name="Move passage 2 up", exact=True).click()
    fixtures.playwright_api.expect(panel.locator("blockquote").first).to_contain_text("make")
    panel.get_by_label("Role 1", exact=True).select_option("hook")
    panel.get_by_label("Role 2", exact=True).select_option("ending")
    panel.get_by_role("button", name="Add passage", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("alert")).to_contain_text("overlap")
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use this sequence")).to_be_disabled()
    panel.get_by_role("button", name="Remove passage 3", exact=True).click()
    panel.get_by_label("Last word 2", exact=True).fill("3")
    fixtures.playwright_api.expect(panel.locator("blockquote").last).not_to_contain_text("ideas")
    panel.get_by_label("Last word 2", exact=True).fill("4")
    panel.get_by_text("Review surrounding words", exact=True).first.click()
    fixtures.playwright_api.expect(panel.locator(".story-block").first).to_contain_text("Before:")
    panel.get_by_role("button", name="Preview story sequence", exact=True).click()
    preview = panel.get_by_label("Story sequence preview")
    preview.wait_for(timeout=120_000)
    page.wait_for_function("() => document.querySelector('[aria-label=\"Story sequence preview\"]').readyState >= 2")
    preview.evaluate("v => v.play()")
    page.wait_for_function("() => document.querySelector('[aria-label=\"Story sequence preview\"]').currentTime > .1")
    assert ScenePlan.load(path) == original
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "story-composer-phone.png"), full_page=True)
    panel.get_by_role("button", name="Use this sequence", exact=True).click()
    undo = page.get_by_role("button", name="Undo", exact=True)
    fixtures.playwright_api.expect(undo).to_be_enabled()
    saved = ScenePlan.load(path)
    assert saved.scenes[0].audio_start > saved.scenes[-1].audio_start
    assert saved.scenes[0].story_role == "hook"
    undo.click()
    page.locator(".story-composer > summary").click()
    fixtures.playwright_api.expect(panel.locator("blockquote").first).to_contain_text("Start")
    assert ScenePlan.load(path) == original
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(undo).to_be_enabled()
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert ScenePlan.load(path) == saved
    page.locator(".story-composer > summary").click()
    fixtures.playwright_api.expect(panel.locator("blockquote").first).to_contain_text("make")
    fixtures.playwright_api.expect(panel.get_by_label("Role 1", exact=True)).to_have_value("hook")
    assert page._voxframe_errors == []
