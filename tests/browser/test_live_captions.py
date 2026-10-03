"""Live captions in the studio, in a real browser (D-196).

The player shows the video without its captions and draws them over it with
libass built for the browser, from the same document the render burns in.
These check that what is drawn is what the video has, and that a change shows
at once, before the video is made again.

Playwright's Chromium cannot decode H.264, so the studio copy is given to it
as VP9; the page does not care which codec it plays.
"""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
pytest.importorskip("fastapi", reason="web extra not installed")

from tests.browser.first_run import to_upload_screen  # noqa: E402
from voxframe.api.app import static_root  # noqa: E402

pytestmark = pytest.mark.browser

#: A frame in the middle of "Douala": spoken from 1.0 s to 1.5 s.
AT = 37.5 / 30


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities

    try:
        found = probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")
    encoders = subprocess.run(
        [found.ffmpeg_path, "-hide_banner", "-encoders"], capture_output=True, text=True
    ).stdout
    if "libvpx-vp9" not in encoders or not found.has_libass:
        pytest.skip("FFmpeg cannot make VP9 or burn captions")
    return found


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator:  # type: ignore[type-arg]
    if not (static_root() / "index.html").is_file():
        pytest.skip("frontend not built (cd web && npm run build)")
    import http.client
    import os

    from voxframe.api.serve import build_server, serve
    from voxframe.config.settings import get_settings

    work = tmp_path_factory.mktemp("live-captions")
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


@pytest.fixture(scope="module")
def job(server, caps):  # type: ignore[no-untyped-def]
    """A finished vertical video, its studio copy given as VP9."""
    from voxframe.config.settings import AspectRatio, QualityPreset
    from voxframe.config.style import get_template
    from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan
    from voxframe.render.compose import render_from_plan
    from voxframe.render.ffpath import run_ffmpeg

    handle, work = server
    store = handle.store
    created = store.create(audio_name="floods.wav", options={"height": 640, "quality": "draft"})
    directory = store.job_directory(created.id)
    audio = directory / "source.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-f", "lavfi", "-i",
                                  "sine=frequency=220:duration=3", "-y", str(audio)])
    spoken = [("Floods", 0.3, 0.7), ("hit", 0.75, 0.95), ("Douala", 1.0, 1.5),
              ("again", 1.55, 1.9), ("this", 2.0, 2.2), ("week", 2.25, 2.6)]
    plan = ScenePlan(
        audio_path=str(audio), audio_sha256="0" * 64, audio_duration=3.0, fps=30.0,
        total_frames=90, aspect=AspectRatio.VERTICAL,
        scenes=(
            PlannedScene(
                index=0, start_frame=0, end_frame=90, text="Floods hit Douala again this week",
                words=tuple(PlanWord(text=t, start=s, end=e) for t, s, e in spoken),
            ),
        ),
    )
    plan_path = plan.save(directory / "source.plan.json")
    result = render_from_plan(
        plan, audio, get_template(), caps, directory / "source.mp4",
        quality=QualityPreset.DRAFT, height=640, cache_dir=work / "cache" / "segments",
        studio_copy=True,
    )
    assert result.studio_path is not None
    studio = directory / "source.studio.webm"
    run_ffmpeg(caps.ffmpeg_path, [
        "-loglevel", "error", "-i", str(result.studio_path), "-c:v", "libvpx-vp9",
        "-deadline", "realtime", "-cpu-used", "8", "-b:v", "2M", "-c:a", "libopus",
        "-y", str(studio),
    ])
    store.submit(created, lambda _job: None)
    assert created.future is not None
    created.future.result(timeout=10)
    store.record_result(
        created,
        artifacts={"plan": plan_path, "video": result.video_path, "studio": studio},
        warnings=(),
        summary={"width": result.width, "height": result.height},
    )
    return created.id, result.video_path, plan_path


def _open_studio(page, handle) -> None:  # type: ignore[no-untyped-def]
    page.goto(handle.url, wait_until="networkidle")
    to_upload_screen(page)
    page.get_by_text("floods.wav").first.click()
    page.locator(".studio video").wait_for(timeout=30_000)
    page.locator(".studio-frame[data-captions='live'] canvas.JASSUB").wait_for(
        state="attached", timeout=30_000
    )
    page.get_by_role("tab", name="Captions").click()


def _show(page, seconds: float) -> np.ndarray:  # type: ignore[no-untyped-def]
    """The player at ``seconds``, paused: its pixels."""
    from PIL import Image

    page.evaluate(
        """(at) => new Promise((done) => {
            const video = document.querySelector('.studio video');
            video.pause();
            video.addEventListener('seeked', () => done(), { once: true });
            video.currentTime = at;
        })""",
        seconds,
    )
    page.wait_for_timeout(1200)  # the worker draws the frame's captions
    # Only the picture and its captions: not the badge or the guide over them.
    page.evaluate("""() => {
        const style = document.createElement('style');
        style.id = 'shot-only';
        style.textContent = '.studio-preview-badge, .caption-guide { visibility: hidden }';
        document.head.append(style);
    }""")
    shot = page.locator(".studio-frame").screenshot()
    page.evaluate("() => document.getElementById('shot-only')?.remove()")
    return np.asarray(Image.open(BytesIO(shot)).convert("RGB")).astype(int)


def _burned(caps, video: Path, seconds: float, size: tuple[int, int], tmp: Path) -> np.ndarray:  # type: ignore[no-untyped-def]
    from PIL import Image

    from voxframe.render.ffpath import run_ffmpeg

    out = tmp / "burned.png"
    frame = int(seconds * 30)
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-i", str(video), "-vf",
                                  f"select=eq(n\\,{frame})", "-frames:v", "1", "-y", str(out)])
    picture = Image.open(out).convert("RGB").resize(size, Image.Resampling.LANCZOS)
    return np.asarray(picture).astype(int)


def _text(frame: np.ndarray) -> np.ndarray:
    """Caption pixels: bright, on the dark plain background. The player's
    rounded corners show the page behind them, so its edge is left out."""
    mask = frame.max(axis=2) > 170
    mask[:8, :] = mask[-8:, :] = False
    mask[:, :8] = mask[:, -8:] = False
    return mask


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    assert len(xs), "no captions drawn"
    return int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())


def test_the_live_captions_are_the_videos(server, page, caps, job, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    handle, _ = server
    _, video, _ = job
    _open_studio(page, handle)

    live = _show(page, AT)
    height, width = live.shape[:2]
    burned = _burned(caps, video, AT, (width, height), tmp_path)

    a, b = _text(live), _text(burned)
    for edge_live, edge_burned in zip(_bbox(a), _bbox(b), strict=True):
        assert abs(edge_live - edge_burned) <= max(4, 0.015 * height)
    assert (a & b).sum() / (a | b).sum() > 0.5
    assert page._voxframe_errors == []


def test_a_change_shows_before_the_video_is_made_again(server, page, caps, job) -> None:  # type: ignore[no-untyped-def]
    handle, _ = server
    _open_studio(page, handle)
    # Just before "hit" is said: highlighted, the whole caption is shown...
    before = _text(_show(page, 0.6)).sum()

    page.get_by_text("Each word pops in as it is said").click()
    playwright_api.expect(
        page.get_by_role("group", name="How the words move").get_by_role("radio", name="Pop")
    ).to_be_checked()
    page.locator(".studio-status").get_by_text("not yet in the video").wait_for(timeout=10_000)
    page.wait_for_timeout(500)
    # ...popping, only "Floods" is.
    after = _text(_show(page, 0.6)).sum()
    assert after < before / 2
    assert page._voxframe_errors == []


def test_captions_moved_in_the_player_stay_there(server, page, caps, job) -> None:  # type: ignore[no-untyped-def]
    from voxframe.plan.scene_plan import ScenePlan

    handle, _ = server
    _, _, plan_path = job
    _open_studio(page, handle)
    _, _, top_before, _ = _bbox(_text(_show(page, AT)))

    page.get_by_role("button", name="Move them in the player").click()
    guide = page.get_by_role("slider", name="Where the captions sit")
    guide.wait_for()
    box = page.locator(".studio-frame").bounding_box()
    handle_box = guide.bounding_box()
    assert box is not None and handle_box is not None
    page.mouse.move(box["x"] + box["width"] / 2, handle_box["y"] + 1)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] * 0.3, steps=8)
    page.mouse.up()
    page.wait_for_timeout(800)

    anchor = ScenePlan.load(plan_path).captions.anchor_y
    assert anchor is not None and 0.25 < anchor < 0.35
    _, _, top_after, _ = _bbox(_text(_show(page, AT)))
    assert top_after < top_before - 0.3 * box["height"]
    assert page._voxframe_errors == []
