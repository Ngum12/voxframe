"""Choose a pause in Chromium, restore it, and export the shorter real video."""
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.unit.test_pacing import source
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.compose import render_from_plan
from voxframe.render.ffpath import run_ffmpeg

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_review_pause_save_undo_redo_and_export(server, page, caps) -> None:  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    job = store.create(audio_name="shorts-pacing.wav", options={"height": 240, "quality": "draft"})
    directory = store.job_directory(job.id)
    audio = directory / "voice.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=7", "-y", str(audio)])
    plan = source().model_copy(update={"audio_path": str(audio)})
    path = plan.save(directory / "source.plan.json")
    video = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             directory / "video.mp4", height=240).video_path
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("shorts-pacing.wav", exact=True).first.click()
    page.get_by_role("tab", name="Pacing", exact=True).click()
    panel = page.get_by_role("region", name="Shorts pacing")
    checkbox = panel.get_by_role("checkbox")
    fixtures.playwright_api.expect(checkbox).not_to_be_checked()
    panel.get_by_role("button", name="Listen at", exact=False).click()
    checkbox.check()
    fixtures.playwright_api.expect(panel.get_by_role("status").filter(has_text="removed")).to_contain_text("4.7 s")
    page.screenshot(path=str(work / "shorts-pacing.png"), full_page=True)
    panel.get_by_role("button", name="Save selected cuts").click()
    fixtures.playwright_api.expect(panel.get_by_text("No long pauses found.", exact=False)).to_be_visible()
    assert ScenePlan.load(path).total_frames == 141
    page.get_by_role("button", name="Undo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("checkbox")).to_be_visible()
    assert ScenePlan.load(path).total_frames == 210
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("checkbox")).to_have_count(0)
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert ScenePlan.load(path).scenes[1].audio_start == 4.3
    assert page._voxframe_errors == []
