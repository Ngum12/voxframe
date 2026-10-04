"""Small, cached previews rendered by the same libass path as the export."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from voxframe.config.captions import CaptionTreatment
from voxframe.config.style import get_template
from voxframe.models.scene import Scene
from voxframe.plan.editing import EditError, _scene
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.captions.ass import build_ass


def caption_preview(
    plan: ScenePlan, index: int, treatment: CaptionTreatment,
    emphasis: tuple[int, ...], directory: Path,
) -> Path:
    from voxframe.assets.fonts import fonts_dir
    from voxframe.render.compose.from_plan import _scenes_for_captions
    from voxframe.render.encode.probe import probe_capabilities
    from voxframe.render.ffpath import run_ffmpeg
    from voxframe.render.ffpath.filters import relative_ass_filter

    planned = _scene(plan, index)
    original = next((s for s in _scenes_for_captions(plan) if s.index == index), None)
    if original is None or not original.words:
        raise EditError("This scene has no speech to preview.")
    start = planned.start_seconds(plan.fps)
    fps = min(60, plan.fps)
    duration = min(12, planned.duration_seconds(plan.fps))
    words = tuple(w.model_copy(update={"start": max(0, w.start - start),
                                      "end": max(0.001, w.end - start)})
                  for w in original.words if w.start - start < duration)
    # Emphasis indices refer to the same corrected word list, never the source transcript.
    scene = Scene(index=index, start_frame=0, end_frame=max(1, round(duration * fps)),
                  words=words, emphasis=tuple(i for i in emphasis if i < len(words)))
    if plan.aspect.value == "9:16":
        width, height = 270, 480
    elif plan.aspect.value == "1:1":
        width, height = 480, 480
    else:
        width, height = 480, 270
    ass = build_ass((scene,), get_template(plan.style).captions, width, height, fps,
                    scene_treatments={index: treatment})
    key = hashlib.sha256(json.dumps([ass, width, height, fps]).encode()).hexdigest()[:24]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{key}.mp4"
    if target.is_file() and target.stat().st_size:
        return target
    caps = probe_capabilities()
    # A simple relative path also works when Windows usernames contain apostrophes.
    with tempfile.TemporaryDirectory(prefix="voxframe-caption-preview-") as folder:
        scratch = Path(folder)
        subtitles = scratch / "captions.ass"
        subtitles.write_text(ass, encoding="utf-8", newline="\n")
        shutil.copytree(fonts_dir(), scratch / "fonts")
        filter_text = relative_ass_filter("captions.ass", "fonts")
        output = scratch / "preview.mp4"
        run_ffmpeg(caps.ffmpeg_path, [
            "-loglevel", "error", "-f", "lavfi", "-i",
            f"color=c=0x18212b:s={width}x{height}:r={fps}",
            "-vf", filter_text, "-frames:v", str(scene.end_frame), "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-threads", "2",
            "-y", str(output),
        ], cwd=scratch, timeout=45)
        shutil.copyfile(output, target)
    return target
