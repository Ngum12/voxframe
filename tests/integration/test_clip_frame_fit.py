"""Downloaded clips fill their exact slots before saved transitions are applied."""
from pathlib import Path

import pytest

from tests.integration import test_render_from_plan as fixtures
from voxframe.config.style import get_template
from voxframe.config.transitions import TransitionTreatment
from voxframe.models.asset import AssetKind
from voxframe.plan.scene_plan import PlanAsset, PlannedScene, ScenePlan
from voxframe.plan.transition_studio import set_transition
from voxframe.render.compose import render_from_plan
from voxframe.render.ffpath import run_ffmpeg

caps = fixtures.caps
pytestmark = pytest.mark.needs_ffmpeg


@pytest.mark.parametrize("rate,source_frames,duration,retime", [
    (30, 59, 59 / 30, None),  # Slightly short: within the 'no slowdown' tolerance.
    (24, 24, None, None),  # Unknown duration: source EOF must not shorten the slot.
    (24, 24, 1.0, None),  # Slow motion at a different input frame rate.
    (60, 12, .2, None),  # Very short: limited slowdown, then hold.
    (60, 1, None, None),  # Even one decoded frame can cover the slot.
    (25, 100, 4.0, None),  # Long: automatic trimming.
    (25, 25, 12.0, None),  # Provider metadata overstates the actual duration.
    (24, 36, None, r"setpts=if(lt(N\,24)\,N/(24*TB)\,(1+(N-24)/12)/TB)"),  # VFR.
    (30, 59, None, "setpts=PTS+3/TB"),  # Media with nonzero starting timestamps.
])
@pytest.mark.parametrize("fps", [30, 29.97])
def test_clip_fills_its_slot_with_saved_transition(
    caps, tmp_path: Path, rate: int, source_frames: int, duration: float | None,
    retime: str | None, fps: float,
) -> None:  # type: ignore[no-untyped-def]
    clip = tmp_path / "clip.mp4"
    timing = ["-vf", retime, "-fps_mode", "vfr"] if retime else []
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-f", "lavfi", "-i",
        f"testsrc2=size=160x90:rate={rate}", "-frames:v", str(source_frames),
        *timing, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(clip)])
    frames = round(2 * fps)
    audio = tmp_path / "voice.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-f", "lavfi", "-i",
        f"sine=frequency=330:duration={2 * frames / fps}", "-y", str(audio)])
    asset = PlanAsset(id="clip", kind=AssetKind.VIDEO, path=str(clip), width=160, height=90,
        duration=duration, license_name="CC0", license_author="Test", license_source="local")
    plan = ScenePlan(audio_path=str(audio), audio_sha256="a" * 64,
        audio_duration=2 * frames / fps, fps=fps, total_frames=2 * frames,
        scenes=(PlannedScene(index=0, start_frame=0, end_frame=frames, text="First scene"),
                PlannedScene(index=1, start_frame=frames, end_frame=2 * frames,
                             text="Second scene", asset=asset)))
    plan = set_transition(plan, 0, TransitionTreatment(kind="crossfade", seconds=.4))
    cache = tmp_path / "cache" / "segments"
    for name in ("first", "cached"):
        result = render_from_plan(plan, audio, get_template(), caps, tmp_path / f"{name}.mp4",
                                  height=120, cache_dir=cache)
        assert fixtures._frame_count(caps, result.video_path) == 2 * frames
        assert all(fixtures._frame_count(caps, path) == frames for path in cache.glob("seg_*.mp4"))
