"""Browser consent, search, preview under voice, explicit selection and real export.

Provider responses and download bytes are controlled fixtures; the app,
preview renderer and final render run unchanged.
"""
from __future__ import annotations

# ruff: noqa: F811
from pathlib import Path

import pytest

from tests.browser.test_music_library import _job
from tests.browser.test_use_my_video import _home, caps, page, server  # noqa: F401
from tests.unit.test_music_discovery import ITEM
from voxframe.music import discovery

pytestmark = pytest.mark.browser


def test_search_audition_choose_and_export(server, page, caps, monkeypatch):  # type: ignore[no-untyped-def]
    import shutil

    from voxframe.plan.scene_plan import ScenePlan
    from voxframe.render.ffpath import run_ffmpeg

    handle, work = server
    job, plan_path = _job(handle, caps, "Online music project.mp4")
    source = work / "online-track.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-f", "lavfi", "-i", "sine=f=880:d=8",
                                "-y", str(source)])
    calls = []
    def get(self, path, params):  # type: ignore[no-untyped-def]
        calls.append(("search", params["q"]))
        return {"results": [ITEM, dict(ITEM, license="by-nc"), dict(ITEM, license="by-nd")],
                "page_count": 1}
    def download(result, directory):  # type: ignore[no-untyped-def]
        calls.append(("download", result.id))
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "source.wav"
        shutil.copyfile(source, target)
        return target
    monkeypatch.setattr(discovery.OpenverseAdapter, "_get", get)
    monkeypatch.setattr(discovery, "download", download)

    _home(page, handle)
    page.get_by_text(job.audio_name, exact=True).first.click()
    page.get_by_role("tab", name="Sound", exact=True).click()
    sound = page.locator(".sound-panel")
    sound.get_by_text("Choose from your music library", exact=True).click()
    sound.get_by_text("Discover openly licensed music", exact=True).click()
    search = sound.locator(".music-discovery")
    allow = search.get_by_label("Allow online music search", exact=True)
    allow.wait_for()
    assert not allow.is_checked() and calls == []
    allow.check()
    search.get_by_label("Find music", exact=True).fill("gentle piano")
    search.get_by_label("Online mood", exact=True).select_option("calm")
    search.get_by_label("Tagged instrumental only", exact=True).check()
    search.get_by_role("button", name="Search Openverse", exact=True).click()
    search.get_by_text("Gentle Piano", exact=True).wait_for()
    assert calls == [("search", "gentle piano")]
    assert search.locator(".music-track").count() == 1
    assert not ScenePlan.load(plan_path).music_path
    search.get_by_role("button", name="Preview track", exact=True).click()
    preview = search.get_by_label("Online preview: Gentle Piano", exact=True)
    preview.wait_for(timeout=30_000)
    page.wait_for_function("() => document.querySelector('audio[aria-label=\"Online preview: Gentle Piano\"]').readyState >= 1")
    assert preview.evaluate("audio => audio.duration") == pytest.approx(8, abs=.05)
    assert not ScenePlan.load(plan_path).music_path
    assert not list((work / "library" / "music").glob("*/audio.wav"))
    search.get_by_role("button", name="Hear under my voice", exact=True).click()
    sound.get_by_label("Sound preview", exact=True).wait_for(timeout=30_000)
    assert not ScenePlan.load(plan_path).music_path
    search.get_by_role("button", name="Use this track", exact=True).click()
    search.get_by_text("Saved Gentle Piano with its credit and license.", exact=True).wait_for()
    assert not ScenePlan.load(plan_path).music_path
    sound.get_by_role("button", name="Apply to the video", exact=True).click()
    page.locator(".studio-status").get_by_text("Updating your video", exact=False).wait_for()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=180_000)
    plan = ScenePlan.load(plan_path)
    assert Path(plan.music_path).is_file()
    assert "Ada Composer" in plan.music_credit and "CC-BY-4.0" in plan.music_credit
    assert "openverse.org/audio" in plan.music_credit
    assert any(plan.music_credit in credit for credit in job.summary["credits"])
    source.unlink()
    sound.get_by_text("Choose from your music library", exact=True).click()
    sound.get_by_text("Discover openly licensed music", exact=True).click()
    search.get_by_label("Allow online music search", exact=True).uncheck()
    assert search.get_by_role("button", name="Search Openverse", exact=True).count() == 0
    assert Path(ScenePlan.load(plan_path).music_path).is_file()
    page.screenshot(path=str(work / "online-music-selected.png"), full_page=True)
    assert page._voxframe_errors == []
