"""Place a cutaway/text span and export it with real playback and history."""
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_visual_placement_render import with_photo
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import ScenePlan, Shot
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_place_preview_clear_stale_preview_save_history_and_export(server, page, caps):  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    job = store.create(audio_name="visual-placement.wav", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(job.id)
    original = with_photo(caps, folder)
    path = original.save(folder / "source.plan.json")
    result = render_from_plan(original, Path(original.audio_path), get_template(), caps,
                             folder / "video.mp4", height=120)
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": result.video_path},
                        warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("visual-placement.wav", exact=True).first.click()
    page.get_by_role("tab", name="Director", exact=True).click()
    page.locator(".visual-placement > summary").click()
    panel = page.get_by_role("region", name="Visual placement", exact=True)
    panel.get_by_role("button", name="Add visual placement", exact=True).click()
    panel.get_by_label("Placement 1 first word", exact=True).select_option("3")
    panel.get_by_label("Placement 1 last word", exact=True).select_option("5")
    with page.expect_response(lambda response: "/thumbnail?asset_only=true" in response.url) as thumbnail:
        panel.get_by_label("Shot 1", exact=True).select_option("picture")
    assert thumbnail.value.status == 200
    image = Image.open(BytesIO(thumbnail.value.body())).convert("RGB")
    red, green, blue = image.getpixel((image.width // 2, image.height // 2))
    assert red > 100 and 40 < green < 110 and blue < 40
    panel.get_by_label("Text treatment 1", exact=True).check()
    panel.get_by_label("Placement text 1", exact=True).fill("THREE STRONG IDEAS")
    panel.get_by_label("Placement position 1", exact=True).select_option("top")
    panel.get_by_role("button", name="Add visual placement", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("alert")).to_contain_text("overlap")
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use visual placements")).to_be_disabled()
    panel.get_by_role("button", name="Remove placement 2", exact=True).click()
    panel.get_by_role("button", name="Preview visual placements", exact=True).click()
    preview = panel.get_by_label("Visual placement preview", exact=True)
    preview.wait_for(timeout=120_000)
    page.wait_for_function("() => document.querySelector('[aria-label=\"Visual placement preview\"]').readyState >= 2")
    preview.evaluate("v => v.play()")
    page.wait_for_function("() => document.querySelector('[aria-label=\"Visual placement preview\"]').currentTime > .1")
    assert ScenePlan.load(path) == original
    panel.get_by_text("Review rendered shot sequence", exact=True).click()
    fixtures.playwright_api.expect(panel.locator(".story-preview")).to_contain_text("picture")
    page.evaluate("window.placementPlayer = document.querySelector('[aria-label=\"Visual placement preview\"]')")
    panel.get_by_label("Placement text 1", exact=True).fill("THE STRONGEST IDEAS")
    fixtures.playwright_api.expect(preview).to_have_count(0)
    assert page.evaluate("window.placementPlayer.paused")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "visual-placement-phone.png"), full_page=True)
    panel.get_by_role("button", name="Use visual placements", exact=True).click()
    undo = page.get_by_role("button", name="Undo", exact=True)
    fixtures.playwright_api.expect(undo).to_be_enabled()
    saved = ScenePlan.load(path)
    assert saved.total_frames == original.total_frames
    assert any(s.shot == Shot.PICTURE and s.visual_beat.text == "THE STRONGEST IDEAS"
               for s in saved.scenes if s.visual_beat)
    undo.click()
    fixtures.playwright_api.expect(page.get_by_role("button", name="Redo", exact=True)).to_be_enabled()
    assert ScenePlan.load(path) == original
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(undo).to_be_enabled()
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert ScenePlan.load(path) == saved
    assert page._voxframe_errors == []
