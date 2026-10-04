"""Finish a recording through pacing, Shorts, direction and a saved platform export."""
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.integration import test_footage_sync as sync
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_render_from_plan import _frame_count
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import PlanWord, ScenePlan
from voxframe.render.compose import render_from_plan
from voxframe.render.encode.probe import probe_media

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_full_producer_to_platform_export_with_guides_and_undo(server, page, caps) -> None:  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    job = store.create(audio_name="finished-short.mp4", options={"height": 240, "quality": "draft"})
    directory = store.job_directory(job.id)
    plan = directed_recording(caps, directory)
    times = (.2, .7, 1.2, 2, 4.2, 4.6, 5.2, 5.7, 6.3)
    scene = plan.scenes[0].model_copy(update={"words": tuple(PlanWord(text=w.text, start=t, end=t + .4)
        for w, t in zip(plan.scenes[0].words, times, strict=True))})
    plan = plan.model_copy(update={"scenes": (scene,)})
    path = plan.save(directory / "source.plan.json")
    video = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                             directory / "video.mp4", height=240).video_path
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": video}, warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("finished-short.mp4", exact=True).first.click()
    page.get_by_role("tab", name="Pacing", exact=True).click()
    pacing = page.get_by_role("region", name="Shorts pacing")
    pacing.get_by_role("checkbox").first.check()
    pacing.get_by_role("button", name="Save selected cuts", exact=True).click()
    fixtures.playwright_api.expect(page.get_by_role("button", name="Undo", exact=True)).to_be_enabled()
    assert ScenePlan.load(path).total_frames < plan.total_frames
    page.get_by_role("tab", name="Shorts", exact=True).click()
    shorts = page.get_by_role("region", name="Shorts producer")
    shorts.get_by_role("button", name="Use this short", exact=True).click()
    page.get_by_role("tab", name="Director", exact=True).click()
    director = page.get_by_role("region", name="Visual director")
    director.get_by_role("checkbox", name="Match the captions to this look").check()
    director.get_by_role("button", name="Apply direction", exact=True).click()
    page.get_by_role("tab", name="Export", exact=True).click()
    panel = page.get_by_role("region", name="Shorts export")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    fixtures.playwright_api.expect(panel.get_by_label("Export platform")).to_be_enabled()
    baseline = ScenePlan.load(path)
    for platform in ("youtube", "tiktok", "reels", "whatsapp"):
        panel.get_by_label("Export platform").select_option(platform)
        panel.get_by_role("button", name="Preview export look", exact=True).click()
        preview = panel.get_by_label("Rendered export preview")
        preview.wait_for(timeout=120_000)
        page.wait_for_function("() => document.querySelector('[aria-label=\"Rendered export preview\"]').readyState >= 2")
        assert preview.evaluate("v => v.duration") == pytest.approx(baseline.total_frames / baseline.fps, abs=.1)
        assert ScenePlan.load(path) == baseline
    fixtures.playwright_api.expect(panel.get_by_label("Text safe area")).to_be_visible()
    frame_box = panel.locator(".export-player").bounding_box()
    guide_box = panel.get_by_label("Text safe area").bounding_box()
    assert frame_box["width"] / frame_box["height"] == pytest.approx(9 / 16, abs=.002)
    assert guide_box["x"] - frame_box["x"] == pytest.approx(frame_box["width"] * .06, abs=1)
    assert guide_box["y"] - frame_box["y"] == pytest.approx(frame_box["height"] * .12, abs=1)
    panel.get_by_role("checkbox", name="Show text guides").uncheck()
    assert panel.get_by_label("Text safe area").count() == 0
    panel.get_by_role("checkbox", name="Show text guides").check()
    preview.evaluate("v => v.play()")
    page.wait_for_function("() => document.querySelector('[aria-label=\"Rendered export preview\"]').readyState >= 4")
    preview.evaluate("v => { v.pause(); v.currentTime = 2.6; }")
    page.wait_for_function("() => { const v = document.querySelector('[aria-label=\"Rendered export preview\"]'); return !v.seeking && v.readyState >= 2; }")
    panel.locator(".export-player").screenshot(path=str(work / "short-export-guides.png"))
    panel.get_by_role("button", name="Save export settings", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use project output settings")).to_be_visible()
    exported = ScenePlan.load(path)
    assert exported.short_export.platform == "whatsapp"
    assert exported.audio_mix.destination.value == "whatsapp"
    page.get_by_role("button", name="Undo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use project output settings")).to_have_count(0)
    assert ScenePlan.load(path) == baseline
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use project output settings")).to_be_visible()
    assert ScenePlan.load(path) == exported
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    media = probe_media(store.artifact_path(job.id, "video"))
    assert media.width == 720 and media.height == 1280
    assert media.duration == pytest.approx(exported.total_frames / exported.fps, abs=.1)
    output = store.artifact_path(job.id, "video")
    assert _frame_count(caps, output) == exported.total_frames
    expected = []
    for clap in sync.CLAPS:
        piece = next(s for s in exported.scenes if
                     s.audio_start <= clap < s.audio_start + s.duration_frames / exported.fps)
        expected.append(piece.start_frame / exported.fps + clap - piece.audio_start)
    sync._assert_in_sync(caps, output, expected)
    page.locator(".studio-menu summary").click()
    with page.expect_download() as received:
        page.get_by_role("menuitem", name="Video (MP4)", exact=True).click()
    download = received.value
    assert download.suggested_filename.endswith(".mp4")
    downloaded = work / "downloaded-short.mp4"
    download.save_as(downloaded)
    assert downloaded.read_bytes() == output.read_bytes()
    assert page._voxframe_errors == []
