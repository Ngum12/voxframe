"""Name, order and download a real collection of finished clip exports."""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from tests.browser import test_use_my_video as fixtures
from tests.integration.test_complete_audition_render import track
from tests.integration.test_direction_render import directed_recording
from voxframe.config.style import get_template
from voxframe.plan.shorts import build_short
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose import render_from_plan

caps = fixtures.caps
server = fixtures.server
page = fixtures.page
pytestmark = pytest.mark.browser


def test_finished_collection_has_exact_videos_subtitles_credits_and_custom_order(server, page, caps):  # type: ignore[no-untyped-def]
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
    for number, (first, last, name) in enumerate(((0, 4, "Opening export"), (5, 8, "Payoff export")), 1):
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
    fixtures._home(page, handle)
    page.get_by_text("collection-source.wav", exact=True).first.click()
    page.get_by_role("tab", name="Shorts", exact=True).click()
    page.locator(".batch-shorts > summary").click()
    panel = page.get_by_role("region", name="Shorts collection", exact=True)
    build = panel.get_by_role("button", name="Build collection", exact=True)
    fixtures.playwright_api.expect(build).to_be_disabled()
    panel.get_by_role("button", name="Select finished clips", exact=True).click()
    panel.get_by_label("Collection name", exact=True).fill("../Launch: pack")
    panel.get_by_label("Collection clip 1 name", exact=True).fill("Opening & promise")
    panel.get_by_label("Collection clip 2 name", exact=True).fill("Final payoff")
    panel.get_by_role("button", name="Move collection clip 2 earlier", exact=True).click()
    fixtures.playwright_api.expect(panel.get_by_label("Collection clip 1 name", exact=True)).to_have_value("Final payoff")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    with page.expect_response(lambda r: r.url.endswith("/shorts/batch/collections") and r.request.method == "POST") as packaged:
        build.click()
    assert packaged.value.status == 200, packaged.value.text()
    fixtures.playwright_api.expect(panel.get_by_text("2 clips packaged", exact=False)).to_be_visible()
    page.screenshot(path=str(work / "collection-phone.png"), full_page=True)
    link = panel.get_by_role("link", name="Download collection ZIP", exact=True)
    with page.expect_download() as downloaded:
        link.click()
    assert downloaded.value.suggested_filename == "launch-pack.zip"
    destination = work / "downloaded.zip"
    downloaded.value.save_as(str(destination))
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("launch-pack/manifest.json"))
        assert [c["title"] for c in manifest["clips"]] == ["Final payoff", "Opening & promise"]
        for item, child in zip(manifest["clips"], reversed(children), strict=True):
            for entry, kind in zip(item["files"], ("video", "srt", "vtt"), strict=True):
                expected = store.artifact_path(child.id, kind).read_bytes()
                assert archive.read(f"launch-pack/{entry['name']}") == expected
                assert entry["sha256"] == hashlib.sha256(expected).hexdigest()
        assert "Soundtrack artist (CC0)" in archive.read("launch-pack/CREDITS.txt").decode()
        assert str(folder) not in json.dumps(manifest)
        assert not any(".plan.json" in name or ".wav" in name for name in archive.namelist())
    # Names cannot accidentally download a collection built for the old form.
    panel.get_by_label("Collection clip 1 name", exact=True).fill("Updated payoff title")
    fixtures.playwright_api.expect(link).to_have_count(0)
    fixtures.playwright_api.expect(build).to_be_enabled()
    assert path.read_text() == original.model_dump_json(indent=2)
    assert page._voxframe_errors == []
