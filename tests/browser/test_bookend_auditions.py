"""Review paired openings and closings and choose the exact cached treatment."""
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


def test_bookend_comparison_review_exact_winner_undo_and_export(server, page, caps):  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    job = store.create(audio_name="bookend-auditions.wav", options={"height": 240, "quality": "draft"})
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
    page.get_by_text("bookend-auditions.wav", exact=True).first.click()
    page.get_by_role("tab", name="Director", exact=True).click()
    page.locator(".bookend-auditions > summary").click()
    panel = page.get_by_role("region", name="Bookend auditions", exact=True)
    panel.get_by_label("Bookend opening words", exact=True).select_option("3")
    panel.get_by_label("Bookend closing words", exact=True).select_option("6")
    panel.get_by_label("Bookend closing shot", exact=True).select_option("speaker")
    responses = []
    page.on("response", lambda response: responses.append(response) if response.url.endswith("/bookend-auditions/preview") else None)
    # Reject overlapping phrases in the browser before any render request.
    panel.get_by_label("Bookend opening words", exact=True).select_option("6")
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Preview selected pair", exact=True)).to_be_disabled()
    fixtures.playwright_api.expect(panel.get_by_text("Opening and closing overlap.", exact=False)).to_be_visible()
    panel.get_by_label("Bookend opening words", exact=True).select_option("3")
    panel.get_by_label("Bookend opening treatment", exact=True).select_option("energy")
    panel.get_by_label("Bookend closing treatment", exact=True).select_option("cinema")
    panel.get_by_role("button", name="Preview selected pair", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_text("Bookend auditions ready.", exact=False)).to_be_visible(timeout=120_000)
    assert len(responses) == 1
    assert responses[0].json()["settings"]["opening_look"] == "energy"
    assert responses[0].json()["settings"]["closing_look"] == "cinema"
    panel.get_by_label("Bookend opening treatment", exact=True).select_option("authority")
    panel.get_by_label("Bookend closing treatment", exact=True).select_option("authority")
    panel.get_by_role("button", name="Render three coordinated pairs", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_text("Bookend auditions ready.", exact=False)).to_be_visible(timeout=120_000)
    assert len(responses) == 4 and all(response.status == 200 for response in responses)
    assert all(response.json()["has_music"] for response in responses)
    assert ScenePlan.load(path) == original
    preview = panel.get_by_label("Bookend audition preview", exact=True)
    fixtures.playwright_api.expect(panel.get_by_role("button", name="Use this pair", exact=True)).to_be_disabled()
    page.wait_for_function("() => document.querySelector('[aria-label=\"Bookend audition preview\"]').readyState >= 2")
    review_at = max(0, responses[1].json()["closing"]["start"] - 1)
    review = panel.get_by_role("button", name="Review the ending", exact=True)
    fixtures.playwright_api.expect(review).to_be_enabled()
    review.click()
    assert preview.evaluate("video => video.currentTime") == pytest.approx(review_at, abs=.05)
    preview.evaluate("video => video.play()")
    page.wait_for_function("at => document.querySelector('[aria-label=\"Bookend audition preview\"]').currentTime > at + .1", arg=review_at)
    page.evaluate("window.closingPlayer = document.querySelector('[aria-label=\"Bookend audition preview\"]')")
    panel.get_by_label("Bookend closing words", exact=True).select_option("7")
    fixtures.playwright_api.expect(preview).to_have_count(0)
    assert page.evaluate("window.closingPlayer.paused")
    # The same visual styles on different words must remain distinguishable.
    panel.get_by_role("button", name="Preview selected pair", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_text("Bookend auditions ready.", exact=False)).to_be_visible(timeout=120_000)
    assert len(responses) == 5
    assert responses[4].json()["closing"]["quote"] == "count now."
    # The comparison uses the already-rendered snapshots, regardless of new controls.
    panel.get_by_role("button", name="Compare bookend auditions", exact=True).click()
    comparison = panel.get_by_role("region", name="Compare complete auditions", exact=True)
    comparison.get_by_label("Comparison version A").select_option(label="Quiet confidence → Land the point · Start with 3 ideas / word count now.")
    comparison.get_by_label("Comparison version B").select_option(label="Word punch → Last-word punch · Start with 3 ideas / word count now.")
    fixtures.playwright_api.expect(comparison.get_by_role("button", name="Use version B", exact=True)).to_be_disabled()
    play = comparison.get_by_role("button", name="Play comparison", exact=True)
    fixtures.playwright_api.expect(play).to_be_enabled(timeout=30_000)
    comparison.get_by_role("button", name="Review the ending", exact=True).click()
    assert comparison.get_by_label("Comparison preview A").evaluate("video => video.currentTime") == pytest.approx(review_at, abs=.05)
    assert comparison.get_by_label("Comparison preview B").evaluate("video => video.currentTime") == pytest.approx(review_at, abs=.05)
    play.click()
    page.wait_for_function("at => [...document.querySelectorAll('.bookend-auditions .comparison-pair video')].every(video => !video.paused && video.currentTime > at + .2)", arg=review_at)
    assert page.evaluate("[...document.querySelectorAll('video,audio')].filter(video => !video.paused && !video.muted && video.volume > 0).length") == 1
    a, b = comparison.get_by_label("Comparison preview A"), comparison.get_by_label("Comparison preview B")
    assert abs(a.evaluate("video => video.currentTime") - b.evaluate("video => video.currentTime")) < .15
    comparison.get_by_label("Comparison sound", exact=True).select_option("1")
    page.wait_for_function("() => { const [a,b] = document.querySelectorAll('.bookend-auditions .comparison-pair video'); return a.muted && !b.muted; }")
    comparison.get_by_role("button", name="Pause comparison", exact=True).click()
    comparison.get_by_role("button", name="Review the opening", exact=True).click()
    assert a.evaluate("video => video.currentTime") == pytest.approx(0, abs=.05)
    assert b.evaluate("video => video.currentTime") == pytest.approx(0, abs=.05)
    panel.get_by_label("Reviewed opening Word punch → Last-word punch · Start with 3 ideas / word count now.", exact=True).check()
    fixtures.playwright_api.expect(comparison.get_by_role("button", name="Use version B", exact=True)).to_be_disabled()
    panel.get_by_label("Reviewed closing Word punch → Last-word punch · Start with 3 ideas / word count now.", exact=True).check()
    fixtures.playwright_api.expect(comparison.get_by_role("button", name="Use version B", exact=True)).to_be_enabled()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "bookend-phone.png"), full_page=True)
    manifest = json.loads((folder / "complete-previews" / f"{responses[2].json()['preview_id']}.json").read_text())
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
