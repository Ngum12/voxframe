"""Review real candidates, change word boundaries, preview and export in Chromium."""
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.unit.test_shorts import story
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.compose import render_from_plan
from voxframe.render.ffpath import run_ffmpeg

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_candidate_trim_real_preview_save_undo_and_export(server, page, caps) -> None:  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    job = store.create(audio_name="shorts-producer.wav", options={"height": 240, "quality": "draft"})
    directory = store.job_directory(job.id)
    audio = directory / "voice.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=72", "-y", str(audio)])
    plan = story().model_copy(update={"audio_path": str(audio)})
    path = plan.save(directory / "source.plan.json")
    video = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             directory / "video.mp4", height=240).video_path
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("shorts-producer.wav", exact=True).first.click()
    page.get_by_role("tab", name="Shorts", exact=True).click()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    panel = page.get_by_role("region", name="Shorts producer")
    choices = panel.locator(".shorts-candidate > button")
    fixtures.playwright_api.expect(choices).to_have_count(3)
    panel.locator(".shorts-heading").scroll_into_view_if_needed()
    page.screenshot(path=str(work / "shorts-options.png"), full_page=True)
    choices.nth(1).click()
    first = panel.get_by_label("First word", exact=True)
    original_first = int(first.input_value())
    first.select_option(str(original_first + 1))
    panel.get_by_role("button", name="Render short preview", exact=True).click()
    preview = panel.get_by_label("Rendered short preview")
    preview.wait_for(timeout=120_000)
    page.wait_for_function("() => document.querySelector('[aria-label=\"Rendered short preview\"]').readyState >= 2")
    duration = preview.evaluate("v => v.duration")
    assert 3 <= duration <= 60
    assert ScenePlan.load(path) == plan
    preview.scroll_into_view_if_needed()
    page.screenshot(path=str(work / "shorts-producer.png"), full_page=True)
    panel.get_by_role("button", name="Use this short", exact=True).click()
    page.get_by_role("button", name="Undo", exact=True).wait_for(state="visible")
    fixtures.playwright_api.expect(page.get_by_role("button", name="Undo", exact=True)).to_be_enabled()
    updated = ScenePlan.load(path)
    assert updated.aspect.value == "9:16" and updated.total_frames < plan.total_frames
    assert updated.scenes[0].audio_start > 0
    page.get_by_role("button", name="Undo", exact=True).click()
    fixtures.playwright_api.expect(panel.locator(".shorts-candidate > button")).to_have_count(3)
    assert ScenePlan.load(path) == plan
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(page.get_by_role("button", name="Undo", exact=True)).to_be_enabled()
    assert ScenePlan.load(path) == updated
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert ScenePlan.load(path).aspect.value == "9:16"
    assert page._voxframe_errors == []
