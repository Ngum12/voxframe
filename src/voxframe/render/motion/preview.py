"""Silent photo previews through the export's camera filter, at draft size."""
import hashlib
import json
import tempfile
from pathlib import Path

from voxframe.config.style import get_template
from voxframe.plan.scene_plan import MotionKind, ScenePlan
from voxframe.render.compose.captioned import _dimensions
from voxframe.render.compose.scenes import _scene_filter, _still_filter
from voxframe.render.encode.probe import probe_capabilities
from voxframe.render.ffpath import filter_path_context, run_ffmpeg
from voxframe.render.motion.saliency import find_subject_center
from voxframe.render.version import RENDERER_VERSION


def camera_preview(plan: ScenePlan, index: int, directory: Path) -> tuple[Path, float]:
    scene = plan.scenes[index]
    assert scene.asset is not None
    source = Path(scene.asset.path)
    stamp = source.stat()
    style = get_template(plan.style)
    width, height = _dimensions(plan.aspect, 480)
    frames = min(scene.duration_frames, max(1, round(12 * plan.fps)))
    key = hashlib.sha256(json.dumps([
        scene.model_dump(mode="json"), plan.fps, plan.aspect.value,
        style.motion.model_dump(mode="json"), stamp.st_size, stamp.st_mtime_ns, RENDERER_VERSION,
    ], sort_keys=True).encode()).hexdigest()[:24]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{key}.mp4"
    if target.is_file():
        return target, frames / plan.fps
    if scene.motion is MotionKind.NONE:
        chain = _still_filter(width, height)
    else:
        saliency = find_subject_center(source)
        chain = _scene_filter(scene, width, height, plan.fps, style.motion,
                              "#18212b",
                              saliency.center if saliency.is_confident else None)
    caps = probe_capabilities()
    fp = filter_path_context(source)
    with tempfile.TemporaryDirectory(prefix="camera-", dir=directory) as folder:
        output = Path(folder) / "preview.mp4"
        run_ffmpeg(caps.ffmpeg_path, [
            "-loglevel", "error", "-loop", "1", "-framerate", str(plan.fps),
            "-i", fp.name if fp.cwd else str(source.resolve()), "-vf", chain,
            "-frames:v", str(frames), "-r", str(plan.fps), "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-threads", "2",
            "-y", str(output.resolve()),
        ], cwd=fp.cwd, timeout=60)
        output.replace(target)
    return target, frames / plan.fps
