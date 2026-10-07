"""Review and export real destination variants with soundtrack and collection metadata."""
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_complete_audition_render import audio, track
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_render_from_plan import _frame_count
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import build_short
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_destinations_require_reviews_and_export_exact_versions(server, page, caps):  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    parent = store.create(audio_name="collection-source.wav", options={"height": 240, "quality": "draft"})
    folder = store.job_directory(parent.id)
    original = directed_recording(caps, folder)
    music = track(folder / "music.wav")
    original = original.model_copy(update={"music_path": str(music), "music_credit": "Soundtrack artist (CC0)"})
    path = original.save(folder / "source.plan.json")
    result = render_from_plan(original, Path(original.audio_path), get_template(), caps,
        folder / "video.mp4", height=120, music=MusicSettings(path=music, credit=original.music_credit))
    store.submit(parent, lambda _: None)
    parent.future.result(timeout=10)
    store.record_result(parent, artifacts={"plan": path, "video": result.video_path}, warnings=(), summary={})
    children = []
    for number, (first, last, name) in enumerate(((0, 4, "Opening export"),), 1):
        child = store.create(audio_name=name, options={"batch_parent": parent.id,
                                                      "batch_clip": str(number) * 24, "height": 240})
        draft = build_short(original, first, last)
        child_folder = store.job_directory(child.id)
        child_plan = draft.save(child_folder / "short.plan.json")
        rendered = render_from_plan(draft, Path(draft.audio_path), get_template(), caps,
            child_folder / "clip.mp4", height=240, music=MusicSettings(path=music, credit=draft.music_credit))
        store.submit(child, lambda _: None)
        child.future.result(timeout=10)
        store.record_result(child, artifacts={"plan": child_plan, "video": rendered.video_path,
            "srt": rendered.srt_path, "vtt": rendered.vtt_path}, warnings=(),
            summary={"width": rendered.width, "height": rendered.height,
                     "credits": list(draft.credits()), "pending_edits": 0})
        children.append(child)
    original_child = store.artifact_path(children[0].id, "plan").read_bytes()
    fixtures._home(page, handle)
    page.get_by_text("collection-source.wav", exact=True).first.click()
    page.get_by_role("tab", name="Shorts", exact=True).click()
    page.locator(".batch-shorts > summary").click()
    panel = page.locator(".destination-variants")
    panel.locator("summary").first.click()
    panel.get_by_role("button", name="Select finished source clips", exact=True).click()
    panel.get_by_label("Make WhatsApp Status version", exact=True).check()
    panel.get_by_label("YouTube Shorts export height", exact=True).select_option("1280")
    export = panel.get_by_role("button", name="Export reviewed destination versions", exact=True)
    fixtures.playwright_api.expect(export).to_be_disabled()
    panel.get_by_role("button", name="Preview destination versions", exact=True).click()
    youtube = panel.get_by_label("YouTube Shorts destination preview", exact=True)
    whatsapp = panel.get_by_label("WhatsApp Status destination preview", exact=True)
    fixtures.playwright_api.expect(whatsapp).to_be_visible(timeout=120_000)
    page.wait_for_function("() => [...document.querySelectorAll('.destination-media video')].every(v => v.readyState >= 2)")
    youtube.evaluate("v => v.play()")
    whatsapp.evaluate("v => v.play()")
    page.wait_for_function("() => document.querySelectorAll('.destination-media video')[0].paused")
    panel.get_by_label("I reviewed this YouTube Shorts version", exact=True).check()
    panel.get_by_label("I reviewed this WhatsApp Status version", exact=True).check()
    fixtures.playwright_api.expect(export).to_be_enabled()
    # Changing a reviewed version removes its old preview and requires review again.
    panel.get_by_label("YouTube Shorts export height", exact=True).select_option("1920")
    fixtures.playwright_api.expect(youtube).to_have_count(0)
    fixtures.playwright_api.expect(export).to_be_disabled()
    panel.get_by_label("YouTube Shorts export height", exact=True).select_option("1280")
    fixtures.playwright_api.expect(export).to_be_enabled()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    guide = panel.get_by_label("Caption and text placement guide").first
    assert guide.bounding_box()["width"] < panel.locator(".destination-media").first.bounding_box()["width"]
    page.screenshot(path=str(work / "destination-phone.png"), full_page=True)
    with page.expect_response(lambda response: response.url.endswith("/shorts/batch/variants") and response.request.method == "POST") as queued:
        export.click()
    assert queued.value.status == 202, queued.value.text()
    variants = queued.value.json()["jobs"]
    for snapshot in variants:
        job = store.get(snapshot["id"])
        job.future.result(timeout=120)
        assert job.state.value == "succeeded", job.error
        plan = ScenePlan.load(store.artifact_path(job.id, "plan"))
        video = store.artifact_path(job.id, "video")
        assert _frame_count(caps, video) == plan.total_frames
        decoded = audio(caps, video, work / f"{job.id}.f32")
        quiet = decoded[int(2.6 * 48000):int(2.8 * 48000)]
        frequencies = np.fft.rfftfreq(len(quiet), 1 / 48000)
        assert frequencies[np.argmax(np.abs(np.fft.rfft(quiet)))] == pytest.approx(220, abs=5)
        assert np.sqrt(np.mean(quiet ** 2)) > .001
        assert plan.short_export.height == 1280
        assert plan.scenes == ScenePlan.load(store.artifact_path(children[0].id, "plan")).scenes
        assert plan.music_credit == "Soundtrack artist (CC0)"
        assert job.summary["short_export"]["platform"] == plan.short_export.platform
        assert job.summary["audio_mix"]["destination"] == plan.audio_mix.destination.value
    assert {store.get(job["id"]).summary["short_export"]["platform"] for job in variants} == {"youtube", "whatsapp"}
    # Collection manifests identify the destinations of the actual rendered videos.
    response = page.request.post(f"{handle.url.split('/?')[0]}/api/jobs/{parent.id}/shorts/batch/collections",
        headers={"x-voxframe-token": handle.token.value}, data={"title": "Destinations", "clips": [
            {"job_id": job["id"], "title": job["audio_name"]} for job in variants]})
    assert response.status == 200, response.text()
    zipped = page.request.get(handle.url.split('/?')[0] + response.json()["url"], headers={"x-voxframe-token": handle.token.value})
    with zipfile.ZipFile(io.BytesIO(zipped.body())) as archive:
        manifest = json.loads(archive.read("destinations/manifest.json"))
        assert {clip["platform"] for clip in manifest["clips"]} == {"youtube", "whatsapp"}
        assert {clip["sound_destination"] for clip in manifest["clips"]} == {"youtube", "whatsapp"}
    assert store.artifact_path(children[0].id, "plan").read_bytes() == original_child
    assert page._voxframe_errors == []
