"""End-to-end tests for rendering from a scene plan.

The plan is the renderer's only input (D-011), so these check that a plan alone
produces correct output: right frame count, right dimensions, motion applied
where an asset exists, and a clean background where one does not.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from voxframe.config.settings import AspectRatio, QualityPreset
from voxframe.config.style import get_template
from voxframe.plan import MotionKind, PlanAsset, PlannedScene, ScenePlan
from voxframe.render.compose import RenderError, render_from_plan
from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import run_ffmpeg

pytestmark = pytest.mark.needs_ffmpeg


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


@pytest.fixture(scope="module")
def photo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An image with enough detail for motion to be measurable.

    A flat image would produce near-identical frames whether or not the camera
    moved, so the motion assertions would pass vacuously.
    """
    path = tmp_path_factory.mktemp("plan") / "detail.png"
    size = 900
    rng = np.random.default_rng(42)

    array = np.zeros((size, size, 3), dtype=np.uint8)
    for y in range(0, size, 12):
        for x in range(0, size, 12):
            array[y : y + 12, x : x + 12] = rng.integers(30, 225, 3)

    Image.fromarray(array).save(path)
    return path


@pytest.fixture
def audio(caps, tmp_path: Path) -> Path:  # type: ignore[no-untyped-def]
    path = tmp_path / "audio.wav"
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=4.37",
            "-ar", "16000", "-ac", "1", "-y", str(path),
        ],
    )
    return path


def _plan(
    photo: Path | None,
    *,
    fps: float = 30.0,
    duration: float = 4.37,
    aspect: AspectRatio = AspectRatio.HORIZONTAL,
    scene_count: int = 2,
) -> ScenePlan:
    """Build a plan with scenes tiling the timeline."""
    from math import ceil

    total = ceil(duration * fps)
    boundaries = [round(total * i / scene_count) for i in range(scene_count + 1)]

    asset = (
        PlanAsset(
            id="a1",
            path=str(photo),
            width=900,
            height=900,
            license_name="CC0-1.0",
            license_author="Test",
            license_source="local",
        )
        if photo
        else None
    )

    scenes = tuple(
        PlannedScene(
            index=index,
            start_frame=boundaries[index],
            end_frame=boundaries[index + 1],
            text=f"Scene {index} narration text here",
            asset=asset,
            motion=MotionKind.KEN_BURNS if asset else MotionKind.NONE,
        )
        for index in range(scene_count)
    )

    return ScenePlan(
        audio_path="audio.wav",
        audio_sha256="a" * 64,
        audio_duration=duration,
        fps=fps,
        total_frames=total,
        aspect=aspect,
        scenes=scenes,
    )


def _frame_count(caps, video: Path) -> int:  # type: ignore[no-untyped-def]
    result = run_ffmpeg(
        caps.ffprobe_path,
        [
            "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames",
            "-of", "default=nw=1:nk=1", str(video),
        ],
        check=False,
    )
    return int(result.stdout.strip())


class TestPlanRendering:
    def test_renders_with_imagery(
        self, caps, photo: Path, audio: Path, tmp_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        result = render_from_plan(
            _plan(photo), audio, get_template(), caps,
            tmp_path / "out.mp4",
            quality=QualityPreset.DRAFT, height=360,
        )

        assert result.video_path.stat().st_size > 0
        assert result.ass_path.exists()

    def test_frame_count_matches_the_plan(
        self, caps, photo: Path, audio: Path, tmp_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        """The frame-grid guarantee, restated for the plan path (D-013)."""
        if caps.ffprobe_path is None:
            pytest.skip("ffprobe required")

        plan = _plan(photo)
        result = render_from_plan(
            plan, audio, get_template(), caps, tmp_path / "out.mp4",
            quality=QualityPreset.DRAFT, height=360, write_sidecars=False,
        )

        assert _frame_count(caps, result.video_path) == plan.total_frames

    def test_scenes_without_assets_render_a_background(
        self, caps, audio: Path, tmp_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        """An unmatched scene is legitimate, not an error (D-042)."""
        result = render_from_plan(
            _plan(None), audio, get_template(), caps,
            tmp_path / "bg.mp4",
            quality=QualityPreset.DRAFT, height=360, write_sidecars=False,
        )

        assert result.video_path.stat().st_size > 0

    def test_missing_asset_file_degrades(
        self, caps, audio: Path, tmp_path: Path
    ) -> None:
        """A plan older than the library must still render."""
        plan = _plan(tmp_path / "deleted.png")

        result = render_from_plan(
            plan, audio, get_template(), caps, tmp_path / "missing.mp4",
            quality=QualityPreset.DRAFT, height=360, write_sidecars=False,
        )

        assert result.video_path.stat().st_size > 0

    def test_missing_audio_reports_clearly(
        self, caps, photo: Path, tmp_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(RenderError, match="not found"):
            render_from_plan(
                _plan(photo), tmp_path / "nope.wav", get_template(), caps,
                tmp_path / "out.mp4",
            )

    @pytest.mark.parametrize(
        ("aspect", "height", "expected_width"),
        [
            (AspectRatio.HORIZONTAL, 360, 640),
            (AspectRatio.VERTICAL, 640, 360),
            (AspectRatio.SQUARE, 360, 360),
        ],
    )
    def test_aspect_ratios(
        self, caps, photo: Path, audio: Path, tmp_path: Path,
        aspect: AspectRatio, height: int, expected_width: int,
    ) -> None:  # type: ignore[no-untyped-def]
        result = render_from_plan(
            _plan(photo, aspect=aspect), audio, get_template(), caps,
            tmp_path / f"{aspect.name}.mp4",
            quality=QualityPreset.DRAFT, height=height, write_sidecars=False,
        )

        assert (result.width, result.height) == (expected_width, height)


class TestMotionIsApplied:
    def test_frames_change_over_a_scene(
        self, caps, photo: Path, audio: Path, tmp_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        """A still image with Ken Burns must not produce identical frames.

        This is what distinguishes motion from a slideshow, and it would fail
        silently if the filter chain were dropped.
        """
        plan = _plan(photo, scene_count=1)
        result = render_from_plan(
            plan, audio, get_template(), caps, tmp_path / "motion.mp4",
            quality=QualityPreset.DRAFT, height=360, write_sidecars=False,
        )

        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()
        run_ffmpeg(
            caps.ffmpeg_path,
            [
                "-loglevel", "error", "-i", str(result.video_path),
                "-vf", "select='eq(n\\,10)+eq(n\\,60)+eq(n\\,110)'",
                "-vsync", "0", "-y", str(frames_dir / "f%02d.png"),
            ],
        )

        extracted = sorted(frames_dir.glob("f*.png"))
        if len(extracted) < 2:
            pytest.skip("could not extract enough frames")

        arrays = [
            np.asarray(Image.open(p).convert("L"), dtype=np.float32) for p in extracted
        ]
        difference = float(np.mean(np.abs(arrays[-1] - arrays[0])))

        assert difference > 1.0, (
            f"frames barely differ (mean {difference:.2f}); "
            "the camera does not appear to be moving"
        )
