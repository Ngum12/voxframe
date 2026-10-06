"""Compare real auditions in sync, with only one audible player, then choose one."""
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_direction_render import directed_recording
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.storyboard import audition
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_comparison_shared_transport_audio_switch_cleanup_and_choose(server, page, caps):
    handle, work = server
    store = handle.store
    job = store.create(audio_name="compare-story.mp4", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(job.id)
    plan = directed_recording(caps, folder)
    path = plan.save(folder / "source.plan.json")
    video = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             folder / "video.mp4", height=120).video_path
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("compare-story.mp4", exact=True).first.click()
    page.get_by_role("tab", name="Shorts", exact=True).click()
    panel = page.get_by_role("region", name="Shorts producer")
    panel.get_by_label("First word", exact=True).wait_for()
    first, last = (int(panel.get_by_label(label, exact=True).input_value()) for label in ("First word", "Last word"))
    for look in ("Clean authority", "High energy", "Cinematic story"):
        panel.get_by_role("button", name=look).click()
        panel.get_by_label("Match captions to the direction").check()
        panel.get_by_role("button", name="Render short preview", exact=True).click()
        panel.get_by_label("Rendered short preview").wait_for(timeout=120_000)
        page.wait_for_function("document.querySelector('[aria-label=\"Rendered short preview\"]').readyState >= 2")
    assert ScenePlan.load(path) == plan
    panel.get_by_role("button", name="Compare rendered edits", exact=True).click()
    comparison = panel.get_by_role("region", name="Compare story edits")
    play = comparison.get_by_role("button", name="Play comparison", exact=True)
    fixtures.playwright_api.expect(play).to_be_enabled(timeout=30_000)
    a, b = comparison.get_by_label("Comparison preview A"), comparison.get_by_label("Comparison preview B")
    play.click()
    page.wait_for_function("() => { const v = [...document.querySelectorAll('.comparison-pair video')]; return v.length === 2 && v.every(p => !p.paused && p.currentTime > .2); }")
    assert abs(a.evaluate("v => v.currentTime") - b.evaluate("v => v.currentTime")) < .15
    assert a.evaluate("v => v.muted") is False and b.evaluate("v => v.muted") is True
    comparison.get_by_label("Comparison sound").select_option("1")
    page.wait_for_function("() => { const [a,b] = document.querySelectorAll('.comparison-pair video'); return a.muted && !b.muted && !a.paused && !b.paused; }")
    assert page.evaluate("[...document.querySelectorAll('video,audio')].filter(v => !v.paused && !v.muted && v.volume > 0).length") == 1
    comparison.get_by_label("Comparison playhead").fill("2")
    assert a.evaluate("v => v.paused") and b.evaluate("v => v.paused")
    assert a.evaluate("v => v.currentTime") == pytest.approx(2, abs=.05)
    assert b.evaluate("v => v.currentTime") == pytest.approx(2, abs=.05)
    slider = comparison.get_by_label("Comparison playhead")
    slider.press("End")
    for _ in range(3):
        slider.press("ArrowLeft")
    play.click()
    page.wait_for_function("() => { const [a,b] = document.querySelectorAll('.comparison-pair video'); return a.paused && b.paused && Math.min(a.currentTime,b.currentTime) > Math.min(a.duration,b.duration) - .2; }")
    play.click()
    page.wait_for_function("() => [...document.querySelectorAll('.comparison-pair video')].every(v => !v.paused && v.currentTime > .2 && v.currentTime < 1)")
    comparison.get_by_role("button", name="Pause comparison", exact=True).click()
    comparison.get_by_label("Comparison version B").select_option(label="Cinematic story · matched captions")
    fixtures.playwright_api.expect(play).to_be_enabled(timeout=30_000)
    assert a.evaluate("v => v.currentTime") == pytest.approx(0, abs=.05)
    assert b.evaluate("v => v.currentTime") == pytest.approx(0, abs=.05)
    page.set_viewport_size({"width": 390, "height": 844})
    comparison.scroll_into_view_if_needed()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "story-comparison-phone.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    play.click()
    page.wait_for_function("document.querySelector('.comparison-pair video').paused === false")
    page.evaluate("window.detachedComparison = [...document.querySelectorAll('.comparison-pair video')]")
    panel.get_by_role("button", name="Close comparison", exact=True).click()
    assert page.evaluate("window.detachedComparison.every(v => v.paused)")
    panel.get_by_role("button", name="Compare rendered edits", exact=True).click()
    fixtures.playwright_api.expect(play).to_be_enabled(timeout=30_000)
    # A different passage has no interchangeable comparison previews.
    panel.get_by_label("First word", exact=True).select_option(str(first + 1))
    fixtures.playwright_api.expect(comparison).to_have_count(0)
    panel.get_by_label("First word", exact=True).select_option(str(first))
    comparison.get_by_label("Comparison version A").select_option(label="High energy · matched captions")
    comparison.get_by_label("Comparison version B").select_option(label="Clean authority · matched captions")
    comparison.get_by_role("button", name="Use version B", exact=True).click()
    expected = audition(plan, first, last, look="authority", match_captions=True)
    fixtures.playwright_api.expect(page.get_by_role("button", name="Undo", exact=True)).to_be_enabled()
    assert ScenePlan.load(path) == expected
    page.get_by_role("button", name="Undo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_label("First word", exact=True)).to_be_visible()
    assert ScenePlan.load(path) == plan
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(page.get_by_role("button", name="Undo", exact=True)).to_be_enabled()
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert ScenePlan.load(path) == expected
    assert page._voxframe_errors == []
