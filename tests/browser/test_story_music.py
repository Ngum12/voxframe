"""Audible story arcs, phone controls, draft undo and actual MP4 export."""
import json
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_music_directed_render import generated_track
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_story_arcs_preview_undo_apply_and_export(server, page, caps):
    handle, work = server
    store = handle.store
    job = store.create(audio_name="music-story.mp4", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(job.id)
    plan = directed_recording(caps, folder)
    track = folder / "music.wav"
    generated_track(track, bars=8)
    plan = plan.model_copy(update={"music_path": str(track), "music_credit": "Test music"})
    path = plan.save(folder / "source.plan.json")
    video = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             folder / "video.mp4", height=120, music=MusicSettings(path=track),
                             cache_dir=folder / "cache" / "segments").video_path
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("music-story.mp4", exact=True).first.click()
    page.get_by_role("tab", name="Sound", exact=True).click()
    panel = page.locator(".sound-panel")
    for title, arc in (("Cinematic rise", "rise"), ("Punch & breathe", "punch")):
        with page.expect_response(lambda response: response.url.endswith("/mix/preview")) as preview:
            panel.get_by_role("button", name=title, exact=False).click()
        assert preview.value.status == 200
        assert json.loads(preview.value.request.post_data)["mix"]["music_arc"] == arc
        audio = panel.get_by_label("Sound preview")
        audio.wait_for()
        page.wait_for_function("document.querySelector('[aria-label=\"Sound preview\"]').readyState >= 2")
        assert audio.evaluate("a => a.duration") > 0
        assert ScenePlan.load(path) == plan
    panel.get_by_role("button", name="Undo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Cinematic rise")).to_have_attribute("aria-pressed", "true")
    for title in ("Hear the opening", "Hear the ending"):
        with page.expect_response(lambda response: response.url.endswith("/mix/preview")) as preview:
            panel.get_by_role("button", name=title, exact=True).click()
        assert preview.value.status == 200
    page.set_viewport_size({"width": 390, "height": 844})
    panel.locator(".music-direction").scroll_into_view_if_needed()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "music-arc-phone.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    panel.get_by_role("button", name="Apply to the video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert ScenePlan.load(path).audio_mix.music_arc == "rise"
    assert video.is_file() and video.stat().st_size > 1000
    assert page._voxframe_errors == []
