"""Save a reviewed recipe and reuse it with another recording's own media."""
import json
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
from voxframe.config.userprefs import config_path
from voxframe.plan.scene_plan import PlanAsset, ScenePlan
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_signature_uses_reviewed_recipe_and_new_words_picture_music_then_undo_export_remove(server, page, caps):  # type: ignore[no-untyped-def]
    handle, work = server
    store = handle.store
    projects = []
    for name, text, color, frequency in (
        ("original-opening.wav", "Start with 3 ideas make each word count now.", (160, 80, 0), 220),
        ("fresh-opening.wav", "Build a bold entrance using your own voice now.", (0, 80, 160), 330),
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
        path = plan.save(folder / "source.plan.json")
        rendered = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
            folder / "video.mp4", height=120, music=MusicSettings(path=music, credit=plan.music_credit))
        store.submit(job, lambda _: None)
        job.future.result(timeout=10)
        store.record_result(job, artifacts={"plan": path, "video": rendered.video_path}, warnings=(), summary={})
        projects.append((job, plan, path, folder))
    first, old_plan, old_path, _ = projects[0]
    second, new_plan, new_path, new_folder = projects[1]
    fixtures._home(page, handle)
    page.get_by_text(first.audio_name, exact=True).first.click()
    page.get_by_role("tab", name="Director", exact=True).click()
    page.locator(".opening-auditions > summary").click()
    panel = page.get_by_role("region", name="Opening auditions", exact=True)
    panel.get_by_label("Opening words", exact=True).select_option("3")
    panel.get_by_role("button", name="Word punch", exact=False).click()
    panel.get_by_label("Opening shot", exact=True).select_option("picture")
    panel.get_by_label("Opening picture", exact=True).select_option("0")
    with page.expect_response(lambda response: response.url.endswith("/opening-auditions/preview"), timeout=120_000) as first_preview:
        panel.get_by_role("button", name="Preview selected opening", exact=True).click()
    assert first_preview.value.status == 200
    save = panel.get_by_role("button", name="Save reviewed opening signature", exact=True)
    panel.get_by_label("Opening signature name", exact=True).fill("My entrance")
    fixtures.playwright_api.expect(save).to_be_disabled()
    panel.get_by_label("Reviewed Word punch · Start with 3 ideas", exact=True).check()
    # Change current controls; saving must still use the reviewed original recipe.
    panel.get_by_role("button", name="Slow reveal", exact=False).click()
    panel.get_by_label("Opening to save", exact=True).select_option(first_preview.value.json()["preview_id"])
    with page.expect_response(lambda response: response.url.endswith("/opening-signatures") and response.request.method == "POST") as saved:
        save.click()
    assert saved.value.status == 201, saved.value.text()
    signature = saved.value.json()
    assert signature["look"] == "energy" and signature["shot"] == "picture"
    assert signature["preferred_words"] == 4
    assert set(signature) == {"id", "name", "look", "shot", "match_captions", "preferred_words"}
    assert ScenePlan.load(old_path) == old_plan
    fixtures._home(page, handle)
    page.get_by_text(second.audio_name, exact=True).first.click()
    page.get_by_role("tab", name="Director", exact=True).click()
    if not page.locator(".opening-auditions").evaluate("element => element.open"):
        page.locator(".opening-auditions > summary").click()
    selector = panel.get_by_label("Opening signature", exact=True)
    fixtures.playwright_api.expect(selector.locator("option", has_text="My entrance")).to_have_count(1)
    selector.select_option(signature["id"])
    fixtures.playwright_api.expect(panel.get_by_label("Opening words", exact=True)).to_have_value("3")
    fixtures.playwright_api.expect(panel.get_by_label("Opening picture", exact=True)).to_have_value("")
    preview = panel.get_by_role("button", name="Preview saved opening signature", exact=True)
    fixtures.playwright_api.expect(preview).to_be_disabled()
    panel.get_by_label("Opening picture", exact=True).select_option("0")
    with page.expect_response(lambda response: response.url.endswith("/opening-signatures/preview"), timeout=120_000) as fresh:
        preview.click()
    assert fresh.value.status == 200 and fresh.value.json()["has_music"]
    result = fresh.value.json()
    assert result["opening"]["quote"] == "Build a bold entrance"
    actual = ScenePlan.model_validate(json.loads((new_folder / "complete-previews" /
        f"{result['preview_id']}.json").read_text())["plan"])
    assert actual.music_path == new_plan.music_path and actual.music_credit == new_plan.music_credit
    assert any(scene.asset.id == second.id and scene.shot.value == "picture" for scene in actual.scenes)
    assert "Start" not in " ".join(scene.visual_beat.text for scene in actual.scenes if scene.visual_beat)
    use = panel.get_by_role("button", name="Use this opening", exact=True)
    fixtures.playwright_api.expect(use).to_be_disabled()
    panel.get_by_label("Reviewed My entrance · Word punch · Build a bold entrance", exact=True).check()
    # Duplicate-name failure must preserve the preview and existing saved signature.
    panel.get_by_label("Opening signature name", exact=True).fill("MY ENTRANCE")
    save.click()
    fixtures.playwright_api.expect(panel.get_by_role("alert")).to_contain_text("already used")
    fixtures.playwright_api.expect(use).to_be_enabled()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(work / "opening-signature-phone.png"), full_page=True)
    use.click()
    undo = page.get_by_role("button", name="Undo", exact=True)
    fixtures.playwright_api.expect(undo).to_be_enabled()
    assert ScenePlan.load(new_path) == actual
    undo.click()
    fixtures.playwright_api.expect(page.get_by_role("button", name="Redo", exact=True)).to_be_enabled()
    assert ScenePlan.load(new_path) == new_plan
    page.get_by_role("button", name="Redo", exact=True).click()
    fixtures.playwright_api.expect(undo).to_be_enabled()
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=120_000)
    video = store.artifact_path(second.id, "video")
    assert _frame_count(caps, video) == new_plan.total_frames
    sound = audio(caps, video, work / "new-export.f32")
    quiet = sound[int(2.6 * 48000):int(2.8 * 48000)]
    frequencies = np.fft.rfftfreq(len(quiet), 1 / 48000)
    assert frequencies[np.argmax(np.abs(np.fft.rfft(quiet)))] == pytest.approx(330, abs=5)
    assert np.sqrt(np.mean(quiet ** 2)) > .001
    assert ScenePlan.load(old_path) == old_plan
    if not page.locator(".opening-auditions").evaluate("element => element.open"):
        page.locator(".opening-auditions > summary").click()
    selector.select_option(signature["id"])
    fixtures.playwright_api.expect(panel.get_by_label("Allow replacing my pinned opening text", exact=True)).not_to_be_checked()
    before = new_path.read_bytes()
    panel.get_by_role("button", name="Remove saved signature", exact=True).click()
    fixtures.playwright_api.expect(selector.locator("option", has_text="My entrance")).to_have_count(0)
    assert new_path.read_bytes() == before
    assert json.loads(config_path().with_name("opening-signatures.json").read_text()) == []
    assert page._voxframe_errors == []
