"""Save a reviewed recipe and reuse it with another recording's own media."""
import json
import re
import zipfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from PIL import Image

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_complete_audition_render import audio
from tests.integration.test_direction_render import directed_recording
from tests.integration.test_render_from_plan import _frame_count
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import PlanAsset, ScenePlan
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_collection_previews_each_recording_exports_exact_plans_and_packages_provenance(server, page, caps):  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    from voxframe.config.bookend_signatures import BookendStyle, save
    from voxframe.config.visuals import VisualBeat

    projects = []
    for name, text, color, frequency in (
        ("original-collection.wav", "Start with 3 ideas make each word count now.", (160, 80, 0), 220),
        ("fresh-collection.wav", "Build a bold entrance using your own voice now.", (0, 80, 160), 330),
    ):
        job = store.create(audio_name=name, options={"height": 240, "quality": "draft"})
        folder = store.job_directory(job.id)
        original = directed_recording(caps, folder)
        picture = folder / "picture.png"
        Image.new("RGB", (640, 360), color).save(picture)
        music = folder / "music.wav"
        times = np.arange(12 * 48000) / 48000
        sf.write(music, .15 * np.sin(2 * np.pi * frequency * times), 48000)
        plan = original.model_copy(update={"music_path": str(music), "music_credit": name + " soundtrack",
            "scenes": (original.scenes[0].model_copy(update={"text": text,
                "asset": PlanAsset(id=job.id, path=str(picture), width=640, height=360,
                    license_name="CC0", license_author=name + " picture author", license_source="local"),
                "words": tuple(word.model_copy(update={"text": new}) for word, new in
                    zip(original.scenes[0].words, text.split(), strict=True))}),)})
        if name.startswith("fresh"):
            plan = plan.model_copy(update={"scenes": (plan.scenes[0].model_copy(update={"visual_beat": VisualBeat(text="Pinned second recording", source="user")}),)})
        path = plan.save(folder / "source.plan.json")
        rendered = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
            folder / "video.mp4", height=120, music=MusicSettings(path=music, credit=plan.music_credit))
        store.submit(job, lambda _: None)
        job.future.result(timeout=10)
        store.record_result(job, artifacts={"plan": path, "video": rendered.video_path}, warnings=(), summary={})
        projects.append((job, plan, path, folder))
    recipe = save("Collection signature", BookendStyle(opening_look="energy", closing_look="cinema", opening_words=4, closing_words=3))
    anchor = projects[0][0]
    fixtures._home(page, handle)
    page.get_by_text(anchor.audio_name, exact=True).first.click()
    page.get_by_role("tab", name="Director", exact=True).click()
    page.locator(".bookend-collections > summary").click()
    panel = page.get_by_role("region", name="Bookend signature collection", exact=True)
    selector = panel.get_by_label("Collection signature", exact=True)
    fixtures.playwright_api.expect(selector.locator("option", has_text=recipe.name)).to_have_count(1)
    selector.select_option(recipe.id)
    for job, _, _, _ in projects:
        panel.get_by_role("checkbox", name=re.compile("Use recording .*" + re.escape(job.audio_name))).check()
    first = panel.get_by_role("region", name="Collection recording 1", exact=True)
    second = panel.get_by_role("region", name="Collection recording 2", exact=True)
    preview = panel.get_by_role("button", name="Preview signature collection", exact=True)
    export = panel.get_by_role("button", name="Export reviewed bookend collection", exact=True)
    fixtures.playwright_api.expect(preview).to_be_disabled()
    second.get_by_label("Allow replacing this recording's pinned opening text", exact=True).check()
    fixtures.playwright_api.expect(preview).to_be_disabled()
    second.get_by_label("Allow replacing this recording's pinned closing text", exact=True).check()
    responses = []
    page.on("response", lambda response: responses.append(response) if response.url.endswith("/bookend-collections/preview") else None)
    preview.click()
    fixtures.playwright_api.expect(panel.get_by_text("Collection previews ready.", exact=False)).to_be_visible(timeout=120_000)
    assert len(responses) == 2 and all(response.status == 200 and response.json()["has_music"] for response in responses)
    assert responses[0].json()["opening"]["quote"] == "Start with 3 ideas"
    assert responses[1].json()["opening"]["quote"] == "Build a bold entrance"
    assert responses[1].json()["closing"]["quote"] == "own voice now."
    fixtures.playwright_api.expect(export).to_be_disabled()
    player = first.get_by_label(projects[0][0].audio_name + " collection preview", exact=True)
    page.wait_for_function("() => document.querySelector('.bookend-collections video').readyState >= 2")
    first.get_by_role("button", name="Review the ending", exact=True).click()
    at = max(0, responses[0].json()["closing"]["start"] - 1)
    assert player.evaluate("video => video.currentTime") == pytest.approx(at, abs=.05)
    player.evaluate("video => video.play()")
    page.wait_for_function("at => document.querySelector('.bookend-collections video').currentTime > at + .2", arg=at)
    assert page.evaluate("[...document.querySelectorAll('video,audio')].filter(media => !media.paused && !media.muted && media.volume > 0).length") == 1
    page.evaluate("window.collectionPlayer = document.querySelector('.bookend-collections video')")
    first.get_by_label("Collection closing words").select_option("7")
    assert page.evaluate("window.collectionPlayer.paused")
    fixtures.playwright_api.expect(first.locator("video")).to_have_count(0)
    first.get_by_label("Collection closing words").select_option("6")
    for card in (first, second):
        card.get_by_label("I reviewed this opening and its return to the story", exact=True).check()
    fixtures.playwright_api.expect(export).to_be_disabled()
    first.get_by_label("I reviewed this ending and the full soundtrack", exact=True).check()
    fixtures.playwright_api.expect(export).to_be_disabled()
    second.get_by_label("I reviewed this ending and the full soundtrack", exact=True).check()
    fixtures.playwright_api.expect(export).to_be_enabled()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "bookend-collection-phone.png"), full_page=True)
    root = store.job_directory(anchor.id)
    expected = [ScenePlan.model_validate(json.loads((root / "complete-previews" / f"{response.json()['preview_id']}.json").read_text())["plan"]) for response in responses]
    with page.expect_response(lambda response: response.url.endswith("/bookend-collections") and response.request.method == "POST") as queued:
        export.click()
    assert queued.value.status == 202
    ids = [item["id"] for item in queued.value.json()["jobs"]]
    for job_id, draft, project, frequency in zip(ids, expected, projects, (220, 330), strict=True):
        child = store.get(job_id)
        child.future.result(timeout=120)
        assert child.state.value == "succeeded", child.error
        assert ScenePlan.load(store.artifact_path(job_id, "plan")) == draft
        assert ScenePlan.load(project[2]) == project[1]
        video = store.artifact_path(job_id, "video")
        assert _frame_count(caps, video) == draft.total_frames
        sound = audio(caps, video, work / f"{job_id}.f32")
        quiet = sound[int(2.6 * 48000):int(2.8 * 48000)]
        frequencies = np.fft.rfftfreq(len(quiet), 1 / 48000)
        assert frequencies[np.argmax(np.abs(np.fft.rfft(quiet)))] == pytest.approx(frequency, abs=5)
        assert np.sqrt(np.mean(quiet ** 2)) > .001
    with page.expect_response(lambda response: response.url.endswith("/bookend-collections") and response.request.method == "POST") as repeat:
        export.click()
    assert [item["id"] for item in repeat.value.json()["jobs"]] == ids
    bundle = panel.get_by_role("region", name="Bookend collection", exact=True)
    fixtures.playwright_api.expect(bundle.get_by_role("button", name="Select finished clips", exact=True)).to_be_enabled(timeout=30_000)
    bundle.get_by_role("button", name="Select finished clips", exact=True).click()
    bundle.get_by_label("Collection name", exact=True).fill("My signature stories")
    with page.expect_response(lambda response: response.url.endswith("/shorts/batch/collections") and response.request.method == "POST") as package:
        bundle.get_by_role("button", name="Build collection", exact=True).click()
    assert package.value.status == 200
    with page.expect_download() as download:
        bundle.get_by_role("link", name="Download collection ZIP", exact=True).click()
    assert download.value.suggested_filename == "my-signature-stories.zip"
    destination = work / "bookend-collection.zip"
    download.value.save_as(str(destination))
    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read("my-signature-stories/manifest.json"))
        assert len(manifest["clips"]) == 2
        assert all(item["bookend_signature"] == recipe.name for item in manifest["clips"])
        assert {item["source_project_id"] for item in manifest["clips"]} == {project[0].id for project in projects}
        assert len([name for name in archive.namelist() if name.endswith(".mp4")]) == 2
        assert not any(name.endswith(".plan.json") for name in archive.namelist())
    assert page._voxframe_errors == []
