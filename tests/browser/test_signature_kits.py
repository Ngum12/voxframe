"""A saved visual kit previews real media, saves its snapshot and undoes."""
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_direction_render import directed_recording
from voxframe.config.camera import CameraMove
from voxframe.config.captions import CAPTION_PRESETS
from voxframe.config.creative_presets import load
from voxframe.config.style import get_template
from voxframe.config.transitions import TRANSITION_PRESETS
from voxframe.config.visuals import VisualBeat
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import revision
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_save_kit_preview_snapshot_apply_and_undo(server, page, caps):  # type: ignore[no-untyped-def]
    handle, _work = server
    store = handle.store
    job = store.create(audio_name="signature-kit.wav", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(job.id)
    original = directed_recording(caps, folder)
    styled = original.model_copy(update={"scenes": tuple(s.model_copy(update={
        "caption_treatment": CAPTION_PRESETS["electric"],
        "transition_after": TRANSITION_PRESETS["cinematic"],
        "camera_move": CameraMove(direction="left"),
        "visual_beat": VisualBeat(text="Keep my words", look="cinema", source="user"),
    }) for s in original.scenes)})
    path = styled.save(folder / "source.plan.json")
    result = render_from_plan(styled, Path(styled.audio_path), get_template(), caps,
                              folder / "video.mp4", height=120)
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": result.video_path},
                        warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("signature-kit.wav", exact=True).first.click()
    page.get_by_role("tab", name="Export", exact=True).click()
    page.get_by_text("Keep this as your signature preset", exact=True).click()
    page.get_by_label("Preset name", exact=True).fill("Cinema signature")
    page.get_by_label("Include this scene", exact=False).check()
    page.get_by_role("button", name="Save creative preset", exact=True).click()
    page.get_by_text("“Cinema signature” saved.", exact=False).wait_for()
    kit = load()[0]
    assert kit.beat_style.look == "cinema" and kit.camera_move.direction == "left"
    assert "text" not in kit.beat_style.model_dump()
    assert ScenePlan.load(path) == styled
    # Preview onto the unstyled version: kit never imports the old beat words.
    original = original.model_copy(update={"scenes": tuple(s.model_copy(update={
        "visual_beat": VisualBeat(text="This project's words", look="energy", source="user"),
    }) for s in original.scenes)})
    original.save(path)
    fixtures._home(page, handle)
    page.get_by_text("signature-kit.wav", exact=True).first.click()
    page.get_by_role("tab", name="Director", exact=True).click()
    page.get_by_text("Signature style kits", exact=True).click()
    page.get_by_label("Signature kit", exact=True).select_option(kit.id)
    with page.expect_response(lambda r: r.url.endswith("/signature-kits/preview"),
                              timeout=120_000) as response:
        page.get_by_role("button", name="Preview signature kit", exact=True).click()
    assert response.value.status == 200, response.value.text()
    assert ScenePlan.load(path) == original
    video = page.get_by_label("Signature kit preview", exact=True)
    video.wait_for()
    page.wait_for_function("() => document.querySelector('[aria-label=\"Signature kit preview\"]').readyState >= 2")
    video.evaluate("v => v.play()")
    page.wait_for_function("() => document.querySelector('[aria-label=\"Signature kit preview\"]').currentTime > .1")
    page.evaluate("window.kitPlayer = document.querySelector('[aria-label=\"Signature kit preview\"]')")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="Apply previewed kit", exact=True).click()
    undo = page.get_by_role("button", name="Undo", exact=True)
    fixtures.playwright_api.expect(undo).to_be_enabled()
    assert page.evaluate("window.kitPlayer.paused")
    changed = ScenePlan.load(path)
    assert changed.scenes[0].visual_beat.text == "This project's words"
    assert changed.scenes[0].visual_beat.look == "cinema"
    assert changed.caption_treatment == kit.caption_treatment
    assert changed.total_frames == original.total_frames
    # The revision check rejects applying a stale audition.
    stale = page.request.post(f"http://{handle.host}:{handle.port}/api/jobs/{job.id}/signature-kits/preview",
        data={"revision": revision(original), "preset_id": kit.id})
    assert stale.status == 409
    undo.click()
    fixtures.playwright_api.expect(page.get_by_role("button", name="Redo", exact=True)).to_be_enabled()
    assert ScenePlan.load(path) == original
    assert page._voxframe_errors == []
