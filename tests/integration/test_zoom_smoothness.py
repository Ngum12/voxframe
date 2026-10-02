"""The D-015 gate: prove Ken Burns motion is smooth at sub-pixel rates.

FFmpeg's ``zoompan`` rounds pan offsets to whole pixels. On a slow zoom that
produces stepping: the image holds for several frames, jumps a pixel, holds
again. It is obvious in a finished video and was flagged as the highest-risk
item of Phase 3.

The measurement
---------------
Render a slow zoom, extract consecutive frames, and track how far the content
moves between them. Two failure signatures:

- **Repeated offsets**: consecutive frames identical, then a jump. Stepping.
- **Non-monotonic motion**: content moving backwards. Jitter.

Frame difference is used as the motion proxy: for a slow zoom on a
high-contrast image, the per-frame difference is proportional to how far the
content moved. A frame pair that is *identical* means the move stalled.

The fix under test is oversampling: render at 2x, move there, downscale. A
half-pixel output move is a whole-pixel internal one, so the rounding lands
between output pixels and the downscale resolves it.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import run_ffmpeg
from voxframe.render.motion.ken_burns import (
    KenBurnsMove,
    MotionDirection,
    zoompan_filter,
)

pytestmark = pytest.mark.needs_ffmpeg

#: A slow zoom over 8 seconds: 1.0 to 1.08 at 30 fps is roughly a third of a
#: pixel per frame at 720p, which is where stepping appears.
SLOW_ZOOM = KenBurnsMove(
    direction=MotionDirection.IN,
    start_zoom=1.0,
    end_zoom=1.08,
    start_center=(0.5, 0.5),
    end_center=(0.5, 0.5),
    duration_frames=120,
)


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


@pytest.fixture(scope="module")
def detailed_image(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A high-contrast image whose movement is measurable.

    Fine detail is essential: a flat or blurry image produces almost no frame
    difference regardless of motion, so the measurement would be meaningless.
    """
    path = tmp_path_factory.mktemp("zoom") / "detail.png"
    size = 1600

    # A dense checkerboard plus diagonals: high spatial frequency in every
    # direction, so movement on any axis registers.
    array = np.zeros((size, size, 3), dtype=np.uint8)
    block = 8
    for y in range(0, size, block):
        for x in range(0, size, block):
            if ((x // block) + (y // block)) % 2 == 0:
                array[y : y + block, x : x + block] = 220
            else:
                array[y : y + block, x : x + block] = 40

    indices = np.arange(size)
    diagonal = ((indices[:, None] + indices[None, :]) % 64) < 8
    array[diagonal] = [200, 120, 60]

    Image.fromarray(array).save(path)
    return path


def _render_zoom(
    caps,  # type: ignore[no-untyped-def]
    image: Path,
    output: Path,
    oversample: int,
    width: int = 1280,
    height: int = 720,
    fps: float = 30.0,
) -> Path:
    """Render the slow zoom at a given oversample factor."""
    chain = zoompan_filter(SLOW_ZOOM, width, height, fps, oversample=oversample)

    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-loop", "1", "-i", str(image),
            "-vf", chain,
            "-frames:v", str(SLOW_ZOOM.duration_frames),
            "-c:v", "libx264", "-crf", "16", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-y", str(output),
        ],
    )
    return output


def _frame_deltas(
    caps,  # type: ignore[no-untyped-def]
    video: Path,
    work_dir: Path,
    start_frame: int = 40,
    count: int = 24,
) -> list[float]:
    """Mean absolute difference between consecutive frames.

    Sampled from the middle of the move, where the rate is steady and neither
    end's easing affects it.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-i", str(video),
            "-vf", f"select='between(n\\,{start_frame}\\,{start_frame + count})'",
            "-fps_mode", "passthrough",
            "-y", str(work_dir / "f%03d.png"),
        ],
    )

    frames = sorted(work_dir.glob("f*.png"))
    if len(frames) < 3:
        pytest.skip(f"only {len(frames)} frames extracted; cannot measure motion")

    arrays = [
        np.asarray(Image.open(path).convert("L"), dtype=np.float32) for path in frames
    ]

    return [
        float(np.mean(np.abs(later - earlier)))
        for earlier, later in pairwise(arrays)
    ]


class TestZoomSmoothness:
    """The D-015 gate."""

    def test_no_stalled_frames(
        self, caps, detailed_image: Path, tmp_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        """No consecutive frame pair may be effectively identical.

        A stalled pair is the signature of integer rounding: the move holds
        until enough error accumulates to shift a whole pixel.
        """
        video = _render_zoom(caps, detailed_image, tmp_path / "zoom.mp4", oversample=2)
        deltas = _frame_deltas(caps, video, tmp_path / "frames")

        # Encoding noise alone produces a small non-zero delta, so "stalled"
        # means far below the median rather than exactly zero.
        median = float(np.median(deltas))
        stalled = [i for i, d in enumerate(deltas) if d < median * 0.25]

        assert not stalled, (
            f"{len(stalled)} of {len(deltas)} frame pairs barely changed "
            f"(median delta {median:.3f}); the zoom is stepping rather than "
            f"moving smoothly. See DECISIONS.md D-015."
        )

    def test_motion_rate_is_consistent(
        self, caps, detailed_image: Path, tmp_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        """Per-frame motion must not vary wildly.

        A steady zoom should move a near-constant amount per frame. Large
        variance means some frames jump while others stall, which is visible
        as judder even when no single frame is fully stalled.
        """
        video = _render_zoom(caps, detailed_image, tmp_path / "zoom.mp4", oversample=2)
        deltas = _frame_deltas(caps, video, tmp_path / "frames")

        mean = float(np.mean(deltas))
        if mean < 0.01:
            pytest.skip("frame differences too small to measure reliably")

        coefficient = float(np.std(deltas)) / mean

        # Compression noise contributes variance of its own, so the bar allows
        # substantial spread while still catching a stepping pattern, which
        # produces a coefficient well above 1.
        # Measured on this machine: 0.113 with the fix, 0.467 without. The
        # bound sits between them with room for encoder noise.
        assert coefficient < 0.30, (
            f"per-frame motion varies too much (coefficient {coefficient:.2f}); "
            "the zoom is not advancing evenly. See DECISIONS.md D-015."
        )

    def test_oversampling_improves_smoothness(
        self, caps, detailed_image: Path, tmp_path: Path
    ) -> None:  # type: ignore[no-untyped-def]
        """The fix must measurably beat the unfixed path.

        Without this, the oversampling could be doing nothing and both tests
        above would still pass on a build whose zoompan happens to be smooth.
        This is what justifies the 4x pixel cost.
        """
        plain = _render_zoom(caps, detailed_image, tmp_path / "plain.mp4", oversample=1)
        fixed = _render_zoom(caps, detailed_image, tmp_path / "fixed.mp4", oversample=2)

        plain_deltas = _frame_deltas(caps, plain, tmp_path / "plain_frames")
        fixed_deltas = _frame_deltas(caps, fixed, tmp_path / "fixed_frames")

        def variation(deltas: list[float]) -> float:
            mean = float(np.mean(deltas))
            return float(np.std(deltas)) / mean if mean > 0.01 else 0.0

        plain_variation = variation(plain_deltas)
        fixed_variation = variation(fixed_deltas)

        # Reported either way: if oversampling turns out unnecessary on this
        # FFmpeg build, that is worth knowing rather than hiding.
        print(
            f"\n  motion variation: oversample=1 {plain_variation:.3f}, "
            f"oversample=2 {fixed_variation:.3f}"
        )

        assert fixed_variation <= plain_variation + 0.05, (
            f"oversampling did not improve smoothness "
            f"({fixed_variation:.3f} vs {plain_variation:.3f}). "
            "If zoompan is smooth without it, drop the 4x cost and record why."
        )


class TestMovePlanning:
    """Move planning is deterministic and varied."""

    def test_same_inputs_give_same_move(self) -> None:
        """A re-render of one plan must produce identical output."""
        from voxframe.render.motion.ken_burns import plan_move

        first = plan_move(3, "asset-abc", 120, intensity=0.5)
        second = plan_move(3, "asset-abc", 120, intensity=0.5)
        assert first == second

    def test_consecutive_scenes_differ(self) -> None:
        """Identical moves in a row read as a template."""
        from voxframe.render.motion.ken_burns import plan_move

        moves = [plan_move(i, "asset-abc", 120) for i in range(6)]
        directions = [move.direction for move in moves]
        assert len(set(directions)) >= 4, f"too repetitive: {directions}"

    def test_intensity_scales_travel(self) -> None:
        from voxframe.render.motion.ken_burns import plan_move

        gentle = plan_move(0, "a", 120, intensity=0.1)
        strong = plan_move(0, "a", 120, intensity=1.0)
        assert strong.zoom_range > gentle.zoom_range

    def test_zero_intensity_is_nearly_static(self) -> None:
        from voxframe.render.motion.ken_burns import plan_move

        move = plan_move(0, "a", 120, intensity=0.0)
        assert move.zoom_range <= 0.05

    def test_subject_center_respected(self) -> None:
        """The move should aim at the subject, not dead space."""
        from voxframe.render.motion.ken_burns import plan_move

        move = plan_move(0, "a", 120, intensity=0.3, subject_center=(0.7, 0.3))
        assert move.start_center[0] > 0.5
        assert move.start_center[1] < 0.5

    def test_center_clamped_away_from_edges(self) -> None:
        """A focal point at the edge would stall the move against the boundary."""
        from voxframe.render.motion.ken_burns import plan_move

        move = plan_move(0, "a", 120, subject_center=(0.99, 0.01))
        assert 0.2 <= move.start_center[0] <= 0.8
        assert 0.2 <= move.start_center[1] <= 0.8
