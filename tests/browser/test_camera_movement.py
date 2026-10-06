"""Direct a photo, preview without edits, save, undo and render the final video."""
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_camera_render import camera_photo
from tests.integration.test_direction_render import directed_recording
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import MotionKind, PlanAsset, ScenePlan, Shot
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_photo_camera_preview_save_undo_and_export(server, page, caps):
    handle, work = server
    store = handle.store
    job = store.create(audio_name="photo-camera.mp4", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(job.id)
    source = directed_recording(caps, folder)
    photo = camera_photo(folder / "camera-photo.png")
    asset = PlanAsset(id="camera-photo", path=str(photo), width=900, height=900,
                     license_name="CC0", license_author="Test", license_source="local")
    plan = source.model_copy(update={"footage": None, "scenes": tuple(
        s.model_copy(update={"asset": asset, "shot": Shot.PICTURE, "motion": MotionKind.KEN_BURNS}) for s in source.scenes)})
    path = plan.save(folder / "source.plan.json")
    video = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             folder / "video.mp4", height=120).video_path
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("photo-camera.mp4", exact=True).first.click()
    panel = page.get_by_role("region", name="Photo camera movement", exact=True)
    select = panel.get_by_label("Camera direction", exact=True)
    fixtures.playwright_api.expect(select).to_be_enabled()
    select.select_option("right")
    panel.get_by_label("Movement strength", exact=True).fill("75")
    panel.get_by_role("button", name="Preview camera movement", exact=True).click()
    player = panel.get_by_label("Rendered camera preview", exact=True)
    player.wait_for(timeout=60_000)
    page.wait_for_function("() => document.querySelector('[aria-label=\"Rendered camera preview\"]').readyState >= 2")
    assert ScenePlan.load(path) == plan
    assert player.evaluate("v => v.muted")
    player.evaluate("v => v.play()")
    page.wait_for_function("() => document.querySelector('[aria-label=\"Rendered camera preview\"]').currentTime > .1")
    player.evaluate("v => { window.oldCameraPlayer = v; }")
    panel.get_by_label("Movement strength", exact=True).fill("50")
    fixtures.playwright_api.expect(player).to_have_count(0)
    assert page.evaluate("window.oldCameraPlayer.paused")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(work / "camera-controls-phone.png"), full_page=True)
    panel.get_by_role("button", name="Save camera movement", exact=True).click()
    fixtures.playwright_api.expect(select).to_be_enabled()
    assert ScenePlan.load(path).scenes[0].camera_move.direction == "right"
    assert ScenePlan.load(path).scenes[0].camera_move.strength == .5
    page.get_by_role("button", name="Undo", exact=True).click()
    fixtures.playwright_api.expect(select).to_have_value("auto")
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(select).to_have_value("right")
    page.get_by_role("button", name="Update video", exact=True).click()
    fixtures.playwright_api.expect(page.get_by_text("Your video is ready", exact=True)).to_be_visible(timeout=120_000)
    assert ScenePlan.load(path).scenes[0].camera_move.direction == "right"
    select.select_option("still")
    panel.get_by_role("button", name="Save camera movement", exact=True).click()
    fixtures.playwright_api.expect(select).to_be_enabled()
    assert ScenePlan.load(path).scenes[0].motion.value == "none"
    select.select_option("auto")
    panel.get_by_role("button", name="Save camera movement", exact=True).click()
    fixtures.playwright_api.expect(select).to_be_enabled()
    assert ScenePlan.load(path).scenes[0].camera_move is None
