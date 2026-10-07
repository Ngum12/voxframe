"""Review two real clips, queue separate exports and recover their status."""
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_complete_audition_render import track
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_render_from_plan import _frame_count
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_two_reviewed_clips_export_independently_and_survive_reload(server, page, caps):  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    job = store.create(audio_name="batch-source.wav", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(job.id)
    original = directed_recording(caps, folder, delay=.6)
    music = track(folder / "music.wav")
    original = original.model_copy(update={"music_path": str(music), "music_credit": "Test soundtrack"})
    path = original.save(folder / "source.plan.json")
    result = render_from_plan(original, Path(original.audio_path), get_template(), caps,
        folder / "video.mp4", height=120, music=MusicSettings(path=music, credit=original.music_credit))
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": result.video_path}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("batch-source.wav", exact=True).first.click()
    page.get_by_role("tab", name="Shorts", exact=True).click()
    page.locator(".batch-shorts > summary").click()
    panel = page.locator(".batch-shorts")
    panel.get_by_role("button", name="Add current passage", exact=True).click()
    panel.get_by_label("Clip 1 last word", exact=True).fill("5")
    panel.get_by_label("Clip 1 look", exact=True).select_option("energy")
    panel.get_by_role("button", name="Add current passage", exact=True).click()
    panel.get_by_label("Clip 2 first word", exact=True).fill("6")
    panel.get_by_label("Clip 2 last word", exact=True).fill("9")
    panel.get_by_label("Clip 2 look", exact=True).select_option("cinema")
    export = panel.get_by_role("button", name="Export reviewed clips", exact=True)
    fixtures.playwright_api.expect(export).to_be_disabled()
    with page.expect_response(lambda response: response.url.endswith("/shorts/batch/preview"),
                              timeout=120_000) as response:
        panel.get_by_role("button", name="Preview unrendered clips", exact=True).click()
    assert response.value.status == 200 and response.value.json()["has_music"]
    panel.get_by_text("Previews ready.", exact=False).wait_for(timeout=120_000)
    previews = panel.get_by_label("Batch clip preview", exact=True)
    fixtures.playwright_api.expect(previews).to_have_count(2)
    page.wait_for_function("() => [...document.querySelectorAll('[aria-label=\"Batch clip preview\"]')].every(v => v.readyState >= 2)")
    previews.nth(0).evaluate("v => v.play()")
    page.wait_for_function("() => document.querySelector('[aria-label=\"Batch clip preview\"]').currentTime > .1")
    previews.nth(1).evaluate("v => v.play()")
    page.wait_for_function("() => {const [a,b] = document.querySelectorAll('[aria-label=\"Batch clip preview\"]'); return a.paused && !b.paused;}")
    assert ScenePlan.load(path) == original
    for card in panel.get_by_role("article", name="Batch clip", exact=False).all():
        card.get_by_label("I checked this clip", exact=False).check()
    fixtures.playwright_api.expect(export).to_be_enabled()
    # Changing a reviewed choice pauses its old player and requires another review.
    page.evaluate("window.oldBatchPlayer = document.querySelectorAll('[aria-label=\"Batch clip preview\"]')[1]")
    panel.get_by_label("Clip 2 look", exact=True).select_option("energy")
    fixtures.playwright_api.expect(export).to_be_disabled()
    assert page.evaluate("window.oldBatchPlayer.paused")
    panel.get_by_role("button", name="Preview clip 2", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_label("Batch clip preview", exact=True)).to_have_count(2, timeout=120_000)
    panel.get_by_role("article", name="Batch clip 2", exact=True).get_by_label("I checked this clip", exact=False).check()
    fixtures.playwright_api.expect(export).to_be_enabled()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "batch-shorts-phone.png"), full_page=True)
    with page.expect_response(lambda response: response.url.endswith("/shorts/batch")
                              and response.request.method == "POST", timeout=120_000) as queued:
        export.click()
    assert queued.value.status == 202
    ids = [j["id"] for j in queued.value.json()["jobs"]]
    assert len(set(ids)) == 2
    downloads = panel.get_by_role("link", name="Download clip", exact=True)
    fixtures.playwright_api.expect(downloads).to_have_count(2, timeout=120_000)
    for child_id in ids:
        child = store.get(child_id)
        assert child.state.value == "succeeded", child.error
        child_plan = ScenePlan.load(store.artifact_path(child_id, "plan"))
        assert child_plan.music_path == original.music_path
        assert child_plan.music_credit == original.music_credit
        assert child.summary["height"] == 1280
        assert _frame_count(caps, store.artifact_path(child_id, "video")) == child_plan.total_frames
        response = page.request.get(f"http://{handle.host}:{handle.port}/api/jobs/{child_id}/artifacts/video",
                                    headers={"range": "bytes=0-31"})
        assert response.status == 206
    # Repeated clicks reuse the same jobs rather than exporting duplicate files.
    with page.expect_response(lambda r: r.url.endswith("/shorts/batch") and r.request.method == "POST") as repeat:
        export.click()
    assert [j["id"] for j in repeat.value.json()["jobs"]] == ids
    assert ScenePlan.load(path) == original
    fixtures._home(page, handle)
    page.get_by_text("batch-source.wav", exact=True).first.click()
    page.get_by_role("tab", name="Shorts", exact=True).click()
    page.locator(".batch-shorts > summary").click()
    fixtures.playwright_api.expect(page.locator(".batch-shorts").get_by_role("link", name="Download clip", exact=True)).to_have_count(2)
    assert page._voxframe_errors == []
