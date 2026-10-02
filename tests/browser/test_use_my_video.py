"""Use my video, in a real browser (D-192).

No transcription model is needed: the settings screen is reached by uploading,
and the studio is opened on a video made from a plan written here, through the
same job store the app uses. "Update video" then renders it for real.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
pytest.importorskip("fastapi", reason="web extra not installed")

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

    work = tmp_path_factory.mktemp("use-my-video")
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


def _recording(caps, path: Path) -> Path:  # type: ignore[no-untyped-def]
    from voxframe.render.ffpath import run_ffmpeg

    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        caps.ffmpeg_path,
        ["-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6",
         "-f", "lavfi", "-i", "sine=f=330:d=6", "-shortest", "-pix_fmt", "yuv420p",
         "-y", str(path)],
    )
    return path


def _home(page, handle) -> None:  # type: ignore[no-untyped-def]
    """The upload screen, past what a first run shows before it.

    Without the models downloaded, the app opens on "Getting ready", and the
    first time, on the online-search question: both are answered "Not now".
    """
    page.goto(handle.url, wait_until="networkidle")
    upload = page.get_by_text("Turn a recording into a video")
    not_now = page.get_by_role("button", name="Not now", exact=True)
    for _ in range(20):
        if upload.count():
            break
        if not_now.count():
            not_now.first.click(timeout=5_000)
        page.wait_for_timeout(250)
    upload.wait_for(timeout=15_000)


def test_a_video_file_offers_use_my_video(server, page, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    handle, _ = server
    _home(page, handle)
    page.set_input_files("input[type=file]", str(_recording(caps, tmp_path / "talk.mp4")))
    page.wait_for_selector("text=How should it look?", timeout=60_000)

    assert page.get_by_role("radio", name="Use my video").is_checked()
    page.get_by_text("Vertical", exact=True).click()
    assert page.get_by_text("follows you as you move").count() == 1
    assert page._voxframe_errors == []


def test_a_sound_file_does_not(server, page, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from voxframe.render.ffpath import run_ffmpeg

    handle, _ = server
    sound = tmp_path / "talk.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-f", "lavfi", "-i", "sine=d=2", "-y", str(sound)])
    _home(page, handle)
    page.set_input_files("input[type=file]", str(sound))
    page.wait_for_selector("text=How should it look?", timeout=60_000)

    assert page.get_by_role("radio", name="Use my video").count() == 0


def test_the_studio_switches_a_scene_between_you_and_its_picture(  # type: ignore[no-untyped-def]
    server, page, caps
) -> None:
    from voxframe.config.style import get_template
    from voxframe.plan.scene_plan import Footage, PlannedScene, ScenePlan, Shot
    from voxframe.plan.shots import attach_footage
    from voxframe.render.compose import render_from_plan

    handle, _ = server
    store = handle.store
    job = store.create(audio_name="talk.mp4", options={"height": 240, "quality": "draft"})
    directory = store.job_directory(job.id)
    recording = _recording(caps, directory / "source.mp4")
    plan = attach_footage(
        ScenePlan(
            audio_path=str(recording), audio_sha256="0" * 64, audio_duration=6.0, fps=30.0,
            total_frames=180,
            scenes=(
                PlannedScene(index=0, start_frame=0, end_frame=90, text="hello there this"),
                PlannedScene(index=1, start_frame=90, end_frame=180, text="is my talk"),
            ),
        ),
        Footage(path=str(recording), width=640, height=360, fps=30.0, duration=6.0),
        cutaways=False,
    )
    plan_path = plan.save(directory / "source.plan.json")
    video = render_from_plan(
        plan, recording, get_template(), caps, directory / "source.mp4.out.mp4", height=240,
    ).video_path
    store.submit(job, lambda _job: None)
    assert job.future is not None
    job.future.result(timeout=10)
    store.record_result(job, artifacts={"plan": plan_path, "video": video}, warnings=(), summary={})

    _home(page, handle)
    page.get_by_text("talk.mp4").first.click()
    page.locator(".studio video").wait_for(timeout=30_000)

    you = page.get_by_role("radio", name="You")
    you.wait_for(timeout=15_000)
    assert you.is_checked()
    assert page.get_by_text("You are on screen").count() >= 1

    # Saved first, then shown: the switch reflects the plan, not the click.
    page.get_by_role("radio", name="A plain background").click()
    page.locator(".studio-status").get_by_text("1 change not yet in the video").wait_for(
        timeout=10_000
    )
    assert page.get_by_role("radio", name="A plain background").is_checked()
    saved = ScenePlan.load(plan_path)
    assert saved.scenes[0].shot is Shot.PICTURE and saved.scenes[0].shot_source == "user"

    page.get_by_role("button", name="Update video").click()
    page.locator(".studio-status").get_by_text("Your video is ready").wait_for(timeout=300_000)
    assert page.get_by_role("radio", name="A plain background").is_checked()
    assert page._voxframe_errors == []
