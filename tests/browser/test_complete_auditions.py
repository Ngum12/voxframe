"""Compare complete music/visual treatments and choose the cached winner."""
import json
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_complete_audition_render import track
from tests.integration.test_direction_render import directed_recording
from voxframe.config.style import get_template
from voxframe.music.library import MusicLibrary
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_full_soundtrack_comparison_and_exact_cached_winner_save_undo_export(server, page, caps):  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    job = store.create(audio_name="complete-auditions.wav", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(job.id)
    original = directed_recording(caps, folder)
    music = track(folder / "music.wav")
    original = original.model_copy(update={"music_path": str(music), "music_credit": "Project credit"})
    library = MusicLibrary(work / "library" / "music")
    saved_track, _ = library.import_track(music, "My saved soundtrack", "Saved track credit", "calm")
    path = original.save(folder / "source.plan.json")
    result = render_from_plan(original, Path(original.audio_path), get_template(), caps,
        folder / "video.mp4", height=120, music=MusicSettings(path=music, credit="Project credit"))
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": result.video_path},
                        warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("complete-auditions.wav", exact=True).first.click()
    page.get_by_role("tab", name="Director", exact=True).click()
    page.locator(".complete-auditions > summary").click()
    panel = page.get_by_role("region", name="Complete auditions", exact=True)
    render = panel.get_by_role("button", name="Render complete audition", exact=True)
    with page.expect_response(lambda response: response.url.endswith("/complete-auditions/preview"),
                              timeout=120_000) as first:
        render.click()
    assert first.value.status == 200 and first.value.json()["has_music"]
    preview = panel.get_by_label("Complete audition preview", exact=True)
    preview.wait_for(timeout=120_000)
    page.wait_for_function("() => document.querySelector('[aria-label=\"Complete audition preview\"]').readyState >= 2")
    preview.evaluate("v => v.play()")
    page.wait_for_function("() => document.querySelector('[aria-label=\"Complete audition preview\"]').currentTime > .1")
    page.evaluate("window.completePlayer = document.querySelector('[aria-label=\"Complete audition preview\"]')")
    panel.get_by_role("button", name="High energy").click()
    fixtures.playwright_api.expect(preview).to_have_count(0)
    assert page.evaluate("window.completePlayer.paused")
    panel.get_by_label("Audition music source", exact=True).select_option("library")
    panel.get_by_label("Audition saved track", exact=True).select_option(saved_track.id)
    with page.expect_response(lambda response: response.url.endswith("/complete-auditions/preview"),
                              timeout=120_000) as second:
        render.click()
    assert second.value.status == 200 and second.value.json()["has_music"]
    assert second.value.json()["preview_id"] != first.value.json()["preview_id"]
    assert ScenePlan.load(path) == original
    # Current form changes must not rebuild or change the already-rendered B.
    panel.get_by_label("Audition music arc", exact=True).select_option("rise")
    panel.get_by_role("button", name="Compare complete auditions", exact=True).click()
    comparison = panel.get_by_role("region", name="Compare complete auditions", exact=True)
    if first.value.json()["music_note"]:
        notes = comparison.locator(".comparison-pair article").first.locator("details")
        notes.locator("summary").click()
        fixtures.playwright_api.expect(notes).to_contain_text(first.value.json()["music_note"])
    play = comparison.get_by_role("button", name="Play comparison", exact=True)
    fixtures.playwright_api.expect(play).to_be_enabled(timeout=30_000)
    play.click()
    page.wait_for_function("() => { const videos = [...document.querySelectorAll('.comparison-pair video')]; return videos.length === 2 && videos.every(v => !v.paused && v.currentTime > .2); }")
    a, b = comparison.get_by_label("Comparison preview A"), comparison.get_by_label("Comparison preview B")
    assert abs(a.evaluate("v => v.currentTime") - b.evaluate("v => v.currentTime")) < .15
    assert page.evaluate("[...document.querySelectorAll('video,audio')].filter(v => !v.paused && !v.muted && v.volume > 0).length") == 1
    comparison.get_by_label("Comparison sound").select_option("1")
    page.wait_for_function("() => { const [a,b] = document.querySelectorAll('.comparison-pair video'); return a.muted && !b.muted && !a.paused && !b.paused; }")
    comparison.get_by_role("button", name="Pause comparison", exact=True).click()
    comparison.get_by_label("Comparison playhead").fill("2")
    assert a.evaluate("v => v.currentTime") == pytest.approx(2, abs=.05)
    assert b.evaluate("v => v.currentTime") == pytest.approx(2, abs=.05)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "complete-auditions-phone.png"), full_page=True)
    manifest = json.loads((folder / "complete-previews" /
        f"{second.value.json()['preview_id']}.json").read_text())
    expected = ScenePlan.model_validate(manifest["plan"])
    comparison.get_by_role("button", name="Use version B", exact=True).click()
    undo = page.get_by_role("button", name="Undo", exact=True)
    fixtures.playwright_api.expect(undo).to_be_enabled()
    assert ScenePlan.load(path) == expected
    assert expected.audio_mix.music_arc == "punch"
    assert expected.music_path == str(library.file(saved_track))
    assert expected.music_credit == "Saved track credit"
    undo.click()
    fixtures.playwright_api.expect(page.get_by_role("button", name="Redo", exact=True)).to_be_enabled()
    assert ScenePlan.load(path) == original
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(undo).to_be_enabled()
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert ScenePlan.load(path) == expected
    assert page._voxframe_errors == []
