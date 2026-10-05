"""Play a real clip preview, select it, and export without its source audio."""
from __future__ import annotations

# ruff: noqa: F811
import shutil

import numpy as np
import pytest

from tests.browser.test_music_library import _job
from tests.browser.test_use_my_video import _home, caps, page, server  # noqa: F401

pytestmark = pytest.mark.browser


def test_clip_search_preview_selection_and_export(server, page, caps, monkeypatch):
    from voxframe.models.asset import AssetKind, LicenseInfo
    from voxframe.plan.scene_plan import ScenePlan
    from voxframe.render.ffpath import run_ffmpeg
    from voxframe.sourcing import fetcher, manual
    from voxframe.sourcing.base import Candidate

    handle, work = server
    job, path = _job(handle, caps, "Choose an online clip.mp4")
    clip = work / "source-clip.mp4"
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=6",
        "-f", "lavfi", "-i", "sine=f=880:d=6", "-shortest", "-pix_fmt", "yuv420p", "-y", str(clip)])
    candidate = Candidate(url="https://clips.example/blue.mp4", kind=AssetKind.VIDEO,
        width=640, height=360, duration=6, title="Blue clip",
        license=LicenseInfo(name="Pexels License", author="Ada Filmmaker", source="pexels",
                            source_url="https://pexels.com/video/blue", allows_commercial=True))

    def search(query, plan, settings, **kwargs):
        assert kwargs["kind"] is AssetKind.VIDEO
        return [candidate], []

    def download(candidate, directory, stem, **kwargs):
        target = directory / f"{stem}.mp4"
        shutil.copyfile(clip, target)
        return target

    monkeypatch.setattr(manual, "search", search)
    monkeypatch.setattr(fetcher, "_download_one", download)
    _home(page, handle)
    response = page.request.put(f"http://{handle.host}:{handle.port}/api/settings",
                               data={"sourcing_consent": True, "api_keys": {"pexels": "test-key"}})
    assert response.ok
    _home(page, handle)
    page.get_by_text(job.audio_name, exact=True).click()
    page.get_by_role("button", name="Search online", exact=True).click()
    search_form = page.locator(".online-search")
    search_form.get_by_label("Search media type", exact=True).select_option("video")
    search_form.get_by_label("Search online for", exact=True).fill("blue sea")
    search_form.get_by_role("button", name="Search", exact=True).click()
    preview = page.get_by_label("Preview clip: Blue clip", exact=True)
    preview.wait_for()
    preview.evaluate("video => video.play()")
    page.wait_for_function("() => !document.querySelector('.online-search video').paused")
    assert preview.evaluate("video => video.muted && video.duration > 5.9")
    search_form.get_by_role("button", name="Use this", exact=True).click()
    page.wait_for_function("() => document.querySelector('.studio-status').textContent.includes('not yet in the video')")
    selected = ScenePlan.load(path).scenes[0].asset
    assert selected.kind is AssetKind.VIDEO and selected.duration == pytest.approx(6, abs=.05)
    assert selected.license_author == "Ada Filmmaker"
    page.get_by_role("button", name="Update video", exact=True).click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=180_000)
    decoded = work / "clip-export-audio.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-i", str(handle.store.artifact_path(job.id, "video")),
                                "-vn", "-ac", "1", "-y", str(decoded)])
    import soundfile as sf

    samples, rate = sf.read(decoded)
    power = abs(np.fft.rfft(samples))
    frequencies = np.fft.rfftfreq(len(samples), 1 / rate)
    assert power[abs(frequencies - 880) < 2].max() < .001 * power[abs(frequencies - 330) < 2].max()
    assert page._voxframe_errors == []
