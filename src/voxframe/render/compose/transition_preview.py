"""Preview a real pair of scenes with the export's join effect."""
from __future__ import annotations

import shutil
from pathlib import Path

from voxframe.config.style import get_template
from voxframe.config.transitions import TransitionTreatment
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.transition_studio import set_transition
from voxframe.render.compose.joins import cached_path, concat_saved_transitions, digest, trim_piece
from voxframe.render.compose.scenes import SegmentResult, render_scene_segments
from voxframe.render.compose.segment_cache import SegmentCache
from voxframe.render.compose.transitions import resolved_transitions
from voxframe.render.encode.probe import probe_capabilities
from voxframe.render.ffpath import run_ffmpeg


def transition_preview(plan: ScenePlan, index: int, treatment: TransitionTreatment,
                       directory: Path, scene_cache: Path) -> Path:
    draft = set_transition(plan, index, treatment)
    effect = resolved_transitions(draft)[index]
    pair = draft.model_copy(update={"scenes": draft.scenes[index:index + 2]})
    caps = probe_capabilities()
    height = 270 if plan.aspect.value == "16:9" else 480
    base_width, base_height = plan.aspect.dimensions_1080
    width = round(height * base_width / base_height)
    width += width % 2
    segments = render_scene_segments(pair, caps, directory / "scenes", width=width,
        height=height, motion=get_template(plan.style).motion, cache=SegmentCache(scene_cache))
    # One second before the join and at least one second after it.
    before = min(segments[0].frames, round(plan.fps))
    after = min(segments[1].frames, max(round(plan.fps), effect.frames + round(plan.fps / 2)))
    a = trim_piece(segments[0], segments[0].frames - before, segments[0].frames,
                   plan.fps, caps, directory / "pieces")
    b = trim_piece(segments[1], 0, after, plan.fps, caps, directory / "pieces")
    target = cached_path(directory, [digest(a), digest(b), repr(effect), plan.fps], "preview")
    if target.is_file():
        return target
    trimmed = [SegmentResult(0, a, before, segments[0].had_asset),
               SegmentResult(1, b, after, segments[1].had_asset)]
    joined = directory / "joined.mp4"
    concat_saved_transitions(trimmed, [effect], plan.fps, caps, joined, directory / "joins")
    # Browser-safe playback; intermediate scenes use yuv444p for rendering.
    temporary = directory / "preview.tmp.mp4"
    run_ffmpeg(caps.ffmpeg_path, ["-loglevel", "error", "-i", str(joined.resolve()),
        "-an", "-frames:v", str(before + after), "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(temporary.resolve())])
    shutil.move(temporary, target)
    return target
