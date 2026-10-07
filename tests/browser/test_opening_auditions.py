"""Review competing openings and choose the exact cached treatment."""
import json
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


def test_opening_comparison_review_exact_winner_undo_and_export(server, page, caps):  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    job = store.create(audio_name="opening-auditions.wav", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(job.id)
    original = directed_recording(caps, folder)
    music = track(folder / "music.wav")
    original = original.model_copy(update={"music_path": str(music), "music_credit": "Project credit"})
    path = original.save(folder / "source.plan.json")
    result = render_from_plan(original, Path(original.audio_path), get_template(), caps,
        folder / "video.mp4", height=120, music=MusicSettings(path=music, credit="Project credit"))
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": result.video_path},
                        warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("opening-auditions.wav", exact=True).first.click()
    page.get_by_role("tab", name="Director", exact=True).click()
    page.locator(".opening-auditions > summary").click()
    panel = page.get_by_role("region", name="Opening auditions", exact=True)
    panel.get_by_label("Opening words", exact=True).select_option("3")
    panel.get_by_label("Opening shot", exact=True).select_option("speaker")
    responses = []
    page.on("response", lambda response: responses.append(response) if response.url.endswith("/opening-auditions/preview") else None)
    panel.get_by_role("button", name="Render three opening auditions", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_text("Opening auditions ready.", exact=False)).to_be_visible(timeout=120_000)
    assert len(responses) == 3 and all(response.status == 200 for response in responses)
    assert all(response.json()["has_music"] for response in responses)
    assert ScenePlan.load(path) == original
    preview = panel.get_by_label("Opening audition preview", exact=True)
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use this opening", exact=True)).to_be_disabled()
    page.wait_for_function("() => document.querySelector('[aria-label=\"Opening audition preview\"]').readyState >= 2")
    preview.evaluate("video => video.play()")
    page.wait_for_function("() => document.querySelector('[aria-label=\"Opening audition preview\"]').currentTime > .1")
    page.evaluate("window.openingPlayer = document.querySelector('[aria-label=\"Opening audition preview\"]')")
    panel.get_by_label("Opening words", exact=True).select_option("4")
    fixtures.playwright_api.expect(preview).to_have_count(0)
    assert page.evaluate("window.openingPlayer.paused")
    # The comparison uses the already-rendered snapshots, regardless of new controls.
    panel.get_by_role("button", name="Compare opening auditions", exact=True).click()
    comparison = panel.get_by_role("region", name="Compare complete auditions", exact=True)
    fixtures.playwright_api.expect(comparison.get_by_role("button", name="Use version B", exact=True)).to_be_disabled()
    play = comparison.get_by_role("button", name="Play comparison", exact=True)
    fixtures.playwright_api.expect(play).to_be_enabled(timeout=30_000)
    play.click()
    page.wait_for_function("() => [...document.querySelectorAll('.opening-auditions .comparison-pair video')].every(video => !video.paused && video.currentTime > .2)")
    assert page.evaluate("[...document.querySelectorAll('video,audio')].filter(video => !video.paused && !video.muted && video.volume > 0).length") == 1
    a, b = comparison.get_by_label("Comparison preview A"), comparison.get_by_label("Comparison preview B")
    assert abs(a.evaluate("video => video.currentTime") - b.evaluate("video => video.currentTime")) < .15
    comparison.get_by_label("Comparison sound", exact=True).select_option("1")
    page.wait_for_function("() => { const [a,b] = document.querySelectorAll('.opening-auditions .comparison-pair video'); return a.muted && !b.muted; }")
    comparison.get_by_role("button", name="Pause comparison", exact=True).click()
    panel.get_by_label("Reviewed Word punch · Start with 3 ideas", exact=True).check()
    fixtures.playwright_api.expect(comparison.get_by_role("button", name="Use version B", exact=True)).to_be_enabled()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "opening-phone.png"), full_page=True)
    manifest = json.loads((folder / "complete-previews" / f"{responses[1].json()['preview_id']}.json").read_text())
    expected = ScenePlan.model_validate(manifest["plan"])
    comparison.get_by_role("button", name="Use version B", exact=True).click()
    undo = page.get_by_role("button", name="Undo", exact=True)
    fixtures.playwright_api.expect(undo).to_be_enabled()
    assert ScenePlan.load(path) == expected
    undo.click()
    fixtures.playwright_api.expect(page.get_by_role("button", name="Redo", exact=True)).to_be_enabled()
    assert ScenePlan.load(path) == original
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(undo).to_be_enabled()
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert _frame_count(caps, store.artifact_path(job.id, "video")) == original.total_frames
    assert ScenePlan.load(path) == expected
    assert page._voxframe_errors == []
