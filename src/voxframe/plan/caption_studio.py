"""Caption edits and acoustic suggestions, without changing the transcript."""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from voxframe.config.captions import CaptionTreatment
from voxframe.plan.editing import EditError, _replace, _scene
from voxframe.plan.scene_plan import ScenePlan


def set_captions(
    plan: ScenePlan, index: int, treatment: CaptionTreatment | None,
    emphasis: tuple[int, ...], *, all_scenes: bool = False,
) -> ScenePlan:
    scene = _scene(plan, index)
    words = scene.caption_words()
    count = len(words) or len(scene.display_text.split())
    if not count:
        raise EditError("This scene has no speech to caption.")
    if any(i < 0 or i >= count for i in emphasis) or len(emphasis) != len(set(emphasis)):
        raise EditError("Choose emphasis words from this scene's caption.")
    edited = scene.model_copy(update={
        "caption_treatment": None if all_scenes else treatment,
        "caption_emphasis": tuple(sorted(emphasis)),
    })
    result = _replace(plan, edited)
    if all_scenes:
        # Applying a look to the video clears per-scene looks but preserves each
        # scene's deliberate emphasis choices. The whole change is one undo step.
        result = result.model_copy(update={
            "caption_treatment": treatment,
            "scenes": tuple(s.model_copy(update={"caption_treatment": None})
                            for s in result.scenes),
        })
    return result


def suggest_emphasis(plan: ScenePlan, index: int) -> tuple[int, ...]:
    """Rank words by measured voice energy and delivery duration, locally."""
    from voxframe.render.encode.probe import probe_capabilities
    from voxframe.render.ffpath import run_ffmpeg

    scene = _scene(plan, index)
    words = scene.caption_words()
    if not words:
        raise EditError("This scene has no word timings to measure.")
    source = Path(plan.audio_path)
    if not source.is_file():
        raise EditError("The original recording is missing. Choose emphasis words yourself.")
    offset = plan.card_seconds_before(index)
    start = max(0, words[0].start - offset)
    duration = words[-1].end - offset - start
    if duration <= 0 or duration > 90:
        raise EditError("Choose emphasis words yourself for this scene.")
    caps = probe_capabilities()
    with tempfile.TemporaryDirectory(prefix="voxframe-emphasis-") as folder:
        audio = Path(folder) / "speech.wav"
        run_ffmpeg(caps.ffmpeg_path, [
            "-loglevel", "error", "-ss", str(start), "-i", str(source.resolve()),
            "-t", str(duration), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_f32le", "-y", str(audio),
        ], timeout=30)
        samples, rate = sf.read(audio, dtype="float32")
    scores = []
    for i, word in enumerate(words):
        token = "".join(c for c in word.text if c.isalpha())
        if len(token) < 3:
            continue
        a = max(0, int((word.start - offset - start) * rate))
        b = min(len(samples), int((word.end - offset - start) * rate))
        if b <= a:
            continue
        energy = math.sqrt(float(np.mean(samples[a:b].astype(np.float64) ** 2)))
        if energy < 1e-5:
            continue
        score = energy * math.sqrt(max(0.02, word.end - word.start) / len(token))
        scores.append((score, i))
    take = min(5, max(1, math.ceil(len(scores) / 4)))
    return tuple(sorted(i for _, i in sorted(scores, reverse=True)[:take]))
