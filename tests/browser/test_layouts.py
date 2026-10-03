"""Split screen and picture-in-picture in the studio, in a real browser (D-197).

No transcription model is needed: the settings screen is reached by uploading,
and the studio is opened on a video made from a plan written here, through the
same job store the app uses. "Update video" then renders it for real.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
pytest.importorskip("fastapi", reason="web extra not installed")

from tests.browser.first_run import to_upload_screen  # noqa: E402
from voxframe.api.app import static_root  # noqa: E402

pytestmark = pytest.mark.browser


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities

    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator:  # type: ignore[type-arg]
    if not (static_root() / "index.html").is_file():
        pytest.skip("frontend not built (cd web && npm run build)")
    import http.client
    import os

    from voxframe.api.serve import build_server, serve
    from voxframe.config.settings import get_settings

    work = tmp_path_factory.mktemp("layouts")
    os.environ["VOXFRAME_CONFIG_DIR"] = str(work / "config")
    settings = get_settings().model_copy(
        update={
            "cache_path": work / "cache",
            "output_path": work / "out",
            "library_path": work / "library",
        }
    )
    handle = build_server(port=_free_port(), settings=settings)
    threading.Thread(target=serve, args=(handle,), daemon=True).start()
    for _ in range(120):
        try:
            connection = http.client.HTTPConnection(handle.host, handle.port, timeout=2)
            connection.request("GET", "/api/health")
            ready = connection.getresponse().status == 200
            connection.close()
            if ready:
                break
        except OSError:
            time.sleep(0.25)
    else:
        pytest.skip("server did not start")
    yield handle, work
    handle.store.shutdown()


@pytest.fixture(scope="module")
def page(server) -> Iterator:  # type: ignore[no-untyped-def,type-arg]
    try:
        with playwright_api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium unavailable: {str(exc)[:80]}")
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page._voxframe_errors = errors  # type: ignore[attr-defined]
            yield page
            context.close()
            browser.close()
    except Exception as exc:
        pytest.skip(f"playwright unavailable: {str(exc)[:80]}")


def _job(server, caps):  # type: ignore[no-untyped-def]
    """A finished vertical video of a recording with a red picture in scene 1."""
    from PIL import Image

    from voxframe.config.settings import AspectRatio
    from voxframe.config.style import get_template
    from voxframe.plan.scene_plan import Footage, PlanAsset, PlannedScene, ScenePlan
    from voxframe.plan.shots import attach_footage
    from voxframe.render.compose import render_from_plan
    from voxframe.render.ffpath import run_ffmpeg

    handle, _ = server
    store = handle.store
    job = store.create(audio_name="flood.mp4", options={"height": 320, "quality": "draft"})
    directory = store.job_directory(job.id)
    recording = directory / "source.mp4"
    run_ffmpeg(caps.ffmpeg_path, [
        "-loglevel", "error", "-f", "lavfi", "-i", "color=blue:s=640x360:r=30:d=4",
        "-f", "lavfi", "-i", "sine=f=330:d=4", "-shortest", "-pix_fmt", "yuv420p",
        "-y", str(recording),
    ])
    picture = directory / "flood.png"
    Image.new("RGB", (900, 900), (220, 20, 20)).save(picture)
    asset = PlanAsset(id="flood", path=str(picture), width=900, height=900,
                      license_name="CC0", license_author="Test", license_source="Test")
    plan = attach_footage(
        ScenePlan(
            audio_path=str(recording), audio_sha256="0" * 64, audio_duration=4.0, fps=30.0,
            total_frames=120, aspect=AspectRatio.VERTICAL,
            scenes=(
                PlannedScene(index=0, start_frame=0, end_frame=60, text="floods in douala"),
                PlannedScene(index=1, start_frame=60, end_frame=120, text="the river rose",
                             asset=asset, match_score=0.9),
            ),
        ),
        Footage(path=str(recording), width=640, height=360, fps=30.0, duration=4.0),
        cutaways=False,
    )
    plan_path = plan.save(directory / "source.plan.json")
    result = render_from_plan(
        plan, recording, get_template(), caps, directory / "source.mp4.out.mp4", height=320,
        studio_copy=True,
    )
    store.submit(job, lambda _job: None)
    assert job.future is not None
    job.future.result(timeout=10)
    store.record_result(
        job,
        artifacts={"plan": plan_path, "video": result.video_path},
        warnings=(), summary={"width": result.width, "height": result.height},
    )
    return job.id, plan_path


def test_a_scene_is_split_previewed_and_made(server, page, caps) -> None:  # type: ignore[no-untyped-def]
    import numpy as np
    from PIL import Image

    from voxframe.plan.scene_layout import LayoutKind
    from voxframe.plan.scene_plan import ScenePlan
    from voxframe.render.ffpath import run_ffmpeg

    handle, work = server
    job_id, plan_path = _job(server, caps)
    page.goto(handle.url, wait_until="networkidle")
    to_upload_screen(page)
    page.get_by_text("flood.mp4").first.click()
    page.locator(".studio video").wait_for(timeout=30_000)

    # Scene 2 has the picture.
    page.keyboard.press("ArrowRight")
    page.get_by_text("The picture above, you below").click()
    page.locator(".studio-status").get_by_text("not yet in the video").wait_for(timeout=10_000)
    saved = ScenePlan.load(plan_path).scenes[1].layout
    assert saved.kind is LayoutKind.SPLIT

    # The player previews the scene as it will look.
    preview = page.locator(".studio-preview")
    preview.wait_for(timeout=10_000)
    page.wait_for_function(
        "() => document.querySelector('.studio-preview').complete && "
        "document.querySelector('.studio-preview').naturalWidth > 0", timeout=15_000,
    )

    page.get_by_text("You on top").click()
    page.wait_for_timeout(600)
    assert ScenePlan.load(plan_path).scenes[1].layout.speaker_first

    page.get_by_role("button", name="Update video").click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=300_000)

    video = handle.store.artifact_path(job_id, "video")
    assert video is not None
    frame_png = work / "frame.png"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-i", str(video), "-vf",
                                  "select=eq(n\\,90)", "-frames:v", "1", "-y", str(frame_png)])
    frame = np.asarray(Image.open(frame_png).convert("RGB")).astype(int)
    height = frame.shape[0]
    top, bottom = frame[height // 5, 20], frame[4 * height // 5 - 40, 20]
    assert top[2] > 150 and top[0] < 90  # you, on top
    assert bottom[0] > 150 and bottom[2] < 90  # the picture below
    assert page._voxframe_errors == []
