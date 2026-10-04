"""Real join effects: exact frame counts, unchanged captions and reusable scenes."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.integration import test_render_from_plan as fixtures
from voxframe.config.style import TransitionKind, get_template
from voxframe.config.transitions import TransitionTreatment
from voxframe.plan.scene_plan import MotionKind, PlanAsset, PlannedScene, PlanWord, ScenePlan
from voxframe.plan.transition_studio import set_transition
from voxframe.render.compose import render_from_plan
from voxframe.render.ffpath import run_ffmpeg

caps = fixtures.caps
pytestmark = pytest.mark.needs_ffmpeg


@pytest.fixture
def plan(caps, tmp_path: Path) -> ScenePlan:  # type: ignore[no-untyped-def]
    source = tmp_path / "speech.wav"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=6", "-y", str(source)])
    scenes = []
    for i, color in enumerate(((220, 40, 30), (20, 80, 220), (20, 210, 70))):
        photo = tmp_path / f"{i}.png"
        Image.new("RGB", (640, 360), color).save(photo)
        asset = PlanAsset(id=str(i), path=str(photo), width=640, height=360,
                         license_name="CC0", license_author="test", license_source="local")
        scenes.append(PlannedScene(index=i, start_frame=i * 60, end_frame=(i + 1) * 60,
            text="A voice", asset=asset, motion=MotionKind.KEN_BURNS,
            words=(PlanWord(text="A", start=i * 2 + .2, end=i * 2 + .5),
                   PlanWord(text="voice", start=i * 2 + .7, end=i * 2 + 1.5))))
    return ScenePlan(audio_path=str(source), audio_sha256="0" * 64, audio_duration=6,
                     fps=30, total_frames=180, scenes=tuple(scenes))


def render(plan: ScenePlan, caps, path: Path, cache: Path):  # type: ignore[no-untyped-def]
    return render_from_plan(plan, Path(plan.audio_path), get_template(plan.style), caps,
                            path, height=120, cache_dir=cache)


@pytest.mark.parametrize("kind", list(TransitionKind))
def test_all_effects_export_exact_original_frames_and_captions(plan: ScenePlan, caps, tmp_path: Path, kind: TransitionKind) -> None:  # type: ignore[no-untyped-def]
    updated = set_transition(plan, 0, TransitionTreatment(kind=kind, seconds=.4), all_joins=True)
    result = render(updated, caps, tmp_path / "video.mp4", tmp_path / "cache")
    assert fixtures._frame_count(caps, result.video_path) == 180
    assert result.srt_path.read_text().count("voice") == 3
    assert updated.scenes[2].words[1].start == 4.7
    # At 5.5s the third image is fully visible; effects never consume its timing.
    frame = tmp_path / "last.png"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-ss", "5.5", "-i",
        str(result.video_path), "-frames:v", "1", "-y", str(frame)])
    rgb = np.asarray(Image.open(frame).convert("RGB"))[:40].mean(axis=(0, 1))
    assert rgb[1] > rgb[0] + 100 and rgb[1] > rgb[2] + 100


def test_changing_one_kind_reuses_all_scenes_and_the_other_join(plan: ScenePlan, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cache = tmp_path / "cache" / "segments"
    join_cache = cache.parent / "joins"
    chosen = set_transition(plan, 0, TransitionTreatment(seconds=.4), all_joins=True)
    first = render(chosen, caps, tmp_path / "first.mp4", cache)
    scene_files = {p.name: p.read_bytes() for p in cache.glob("seg_*.mp4")}
    joins = {p.name: p.read_bytes() for p in join_cache.glob("join_*.mp4")}
    assert len(scene_files) == 3 and len(joins) == 2
    # Cached scenes still render even when their source images are unavailable.
    for scene in plan.scenes:
        Path(scene.asset.path).unlink()
    changed = set_transition(chosen, 0, TransitionTreatment(kind="dip_to_black", seconds=.4))
    second = render(changed, caps, tmp_path / "second.mp4", cache)
    assert {p.name: p.read_bytes() for p in cache.glob("seg_*.mp4")} == scene_files
    new_joins = list(join_cache.glob("join_*.mp4"))
    assert len(new_joins) == 3
    assert all((join_cache / name).read_bytes() == data for name, data in joins.items())
    assert first.ass_path.read_bytes() == second.ass_path.read_bytes()
    assert first.srt_path.read_bytes() == second.srt_path.read_bytes()
    assert fixtures._frame_count(caps, second.video_path) == 180


@pytest.mark.parametrize("fps", [24, 29.97, 60])
def test_saved_blends_preserve_fractional_and_high_frame_grids(plan: ScenePlan, caps, tmp_path: Path, fps: float) -> None:  # type: ignore[no-untyped-def]
    scenes = tuple(s.model_copy(update={"start_frame": round(i * 2 * fps),
        "end_frame": round((i + 1) * 2 * fps)}) for i, s in enumerate(plan.scenes))
    total = round(6 * fps)
    adjusted = plan.model_copy(update={"fps": fps, "scenes": scenes, "total_frames": total,
                                       "audio_duration": total / fps})
    chosen = set_transition(adjusted, 0, TransitionTreatment(kind="push"), all_joins=True)
    result = render(chosen, caps, tmp_path / "fps.mp4", tmp_path / "cache")
    assert fixtures._frame_count(caps, result.video_path) == total


@pytest.mark.parametrize("kind", ["crossfade", "slide", "push", "dip_to_black"])
def test_speaker_flashes_stay_on_their_tones_after_saved_joins(caps, tmp_path: Path, kind: str) -> None:  # type: ignore[no-untyped-def]
    from tests.integration import test_footage_sync as sync

    footage = sync._footage(caps, sync._recording(caps, tmp_path / "recording.mp4", rate=25))
    source = sync._plan(footage, sync._speaker(0, 0, 2.5, 0), sync._speaker(1, 2.5, 7, 2.5))
    chosen = set_transition(source, 0, TransitionTreatment(kind=kind, seconds=.6))
    result = sync._render(caps, chosen, tmp_path)
    sync._assert_in_sync(caps, result.video_path, list(sync.CLAPS))


def test_dip_to_black_has_a_dark_midpoint_that_crossfade_does_not(plan: ScenePlan, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    means = {}
    for kind in ("dip_to_black", "crossfade"):
        chosen = set_transition(plan, 0, TransitionTreatment(kind=kind, seconds=.6))
        result = render(chosen, caps, tmp_path / f"{kind}.mp4", tmp_path / "cache")
        target = tmp_path / f"{kind}.png"
        run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-i", str(result.video_path),
            "-vf", "select=eq(n\\,67)", "-frames:v", "1", "-y", str(target)])
        pixels = np.asarray(Image.open(target).convert("RGB"))[:40]
        means[kind] = pixels.mean()
        if kind == "crossfade":
            rgb = pixels.mean(axis=(0, 1))
            assert rgb[0] > 50 and rgb[2] > 50  # both pictures are still in the blend
    assert means["dip_to_black"] < 20
    assert means["crossfade"] > 70
