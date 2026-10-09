"""Audition three real looks, compare cached previews and save/export one story."""

import pytest
from PIL import Image

from tests.browser import test_use_my_video as fixtures
from tests.unit.test_shorts import story
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import Footage, PlanAsset, ScenePlan
from voxframe.plan.shots import choose_shots
from voxframe.plan.storyboard import audition
from voxframe.render.compose import render_from_plan
from voxframe.render.ffpath import run_ffmpeg

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_three_story_auditions_compare_save_undo_and_export(server, page, caps):  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    job = store.create(audio_name="story-audition.wav", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(job.id)
    audio, recording, photo = folder / "voice.wav", folder / "speaker.mp4", folder / "visual.png"
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=72", "-y", str(audio)])
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=160x90:rate=25:duration=72", "-an", "-c:v", "libx264",
        "-preset", "ultrafast", "-y", str(recording)])
    Image.new("RGB", (320, 180), "orange").save(photo)
    asset = PlanAsset(id="visual", path=str(photo), width=320, height=180,
                     license_name="CC0", license_author="Test", license_source="local")
    base = story()
    plan = choose_shots(base.model_copy(update={"audio_path": str(audio),
        "footage": Footage(path=str(recording), width=160, height=90, fps=25, duration=72),
        "scenes": tuple(s.model_copy(update={"asset": asset, "match_score": .9})
                        for s in base.scenes)}))
    path = plan.save(folder / "source.plan.json")
    video = render_from_plan(plan, audio, get_template(), caps, folder / "video.mp4", height=120)
    store.submit(job, lambda _job: None)
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": path, "video": video.video_path},
                        warnings=(), summary={})
    fixtures._home(page, handle)
    page.get_by_text("story-audition.wav", exact=True).first.click()
    page.get_by_role("tab", name="Shorts", exact=True).click()
    panel = page.get_by_role("region", name="Shorts producer")
    panel.locator(".story-payoff").first.wait_for()
    first = int(panel.get_by_label("First word", exact=True).input_value())
    last = int(panel.get_by_label("Last word", exact=True).input_value())
    requests = []
    page.on("request", lambda request: requests.append(request.url)
            if request.url.endswith("/shorts/preview") else None)
    urls = {}
    for label in ("Clean authority", "High energy", "Cinematic story"):
        panel.get_by_role("button", name=label).click()
        panel.get_by_label("Match captions to the direction").check()
        board = panel.get_by_role("region", name="Planned story")
        fixtures.playwright_api.expect(board.locator("li").first).to_contain_text("Speaker")
        fixtures.playwright_api.expect(board.locator("li").last).to_contain_text("Speaker")
        fixtures.playwright_api.expect(board.locator("li").filter(has_text="opening").first).to_contain_text("Speaker")
        panel.get_by_role("button", name="Render short preview", exact=True).click()
        preview = panel.get_by_label("Rendered short preview")
        preview.wait_for(timeout=120_000)
        page.wait_for_function("() => document.querySelector('[aria-label=\"Rendered short preview\"]').readyState >= 2")
        assert 3 <= preview.evaluate("v => v.duration") <= 60
        urls[label] = preview.get_attribute("src")
        assert ScenePlan.load(path) == plan
    assert len(set(urls.values())) == 3 and len(requests) == 3
    panel.get_by_role("button", name="High energy").click()
    fixtures.playwright_api.expect(panel.get_by_label("Rendered short preview")).to_have_attribute(
        "src", urls["High energy"])
    assert len(requests) == 3
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    panel.locator(".story-directions").scroll_into_view_if_needed()
    page.screenshot(path=str(work / "story-audition-phone.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    expected = audition(plan, first, last, look="energy", match_captions=True)
    panel.get_by_role("button", name="Use this short", exact=True).click()
    undo = page.get_by_role("button", name="Undo", exact=True)
    fixtures.playwright_api.expect(undo).to_be_enabled()
    assert ScenePlan.load(path) == expected
    redo = page.get_by_role("button", name="Redo", exact=True)
    # The Shorts controls were already visible; their visibility cannot signal undo completion.
    with page.expect_response(lambda response: response.url.endswith(f"/jobs/{job.id}/plan/undo")
                              and response.request.method == "POST") as undone:
        undo.click()
    assert undone.value.status == 200
    fixtures.playwright_api.expect(redo).to_be_enabled()
    assert ScenePlan.load(path) == plan
    with page.expect_response(lambda response: response.url.endswith(f"/jobs/{job.id}/plan/redo")
                              and response.request.method == "POST") as redone:
        redo.click()
    assert redone.value.status == 200
    fixtures.playwright_api.expect(redo).to_be_disabled()
    fixtures.playwright_api.expect(undo).to_be_enabled()
    assert ScenePlan.load(path) == expected
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    assert ScenePlan.load(path) == expected
    assert page._voxframe_errors == []
