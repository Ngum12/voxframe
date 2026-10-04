"""Render a bounded draft through the real export renderer without saving edits."""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from voxframe.config.settings import QualityPreset
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.compose.from_plan import render_from_plan
from voxframe.render.encode.probe import probe_capabilities


def short_preview(plan: ScenePlan, directory: Path) -> Path:
    # Music preview is separate: avoid fitting a full recording's track to a
    # draft. Choosing this cut retains the person's music/score for final export.
    draft = plan.model_copy(update={"score": None, "music_path": "", "music_credit": ""})
    sources = {draft.audio_path}
    if draft.footage and any(draft.shows_speaker(s) for s in draft.scenes):
        sources.add(draft.footage.path)
    sources.update(s.asset.path for s in draft.scenes if s.asset and not draft.shows_speaker(s))
    stamps = []
    for source in sorted(sources):
        info = Path(source).stat()
        stamps.append((source, info.st_size, info.st_mtime_ns))
    key = hashlib.sha256(json.dumps([draft.model_dump(mode="json"), stamps, 1],
                                    sort_keys=True).encode()).hexdigest()[:24]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{key}.mp4"
    if target.is_file():
        return target
    caps = probe_capabilities()
    with tempfile.TemporaryDirectory(prefix="render-", dir=directory) as folder:
        output = Path(folder) / "short.mp4"
        render_from_plan(draft, Path(draft.audio_path), get_template(draft.style), caps,
                         output, quality=QualityPreset.DRAFT, height=480,
                         cache_dir=directory / "cache" / "segments", write_sidecars=False)
        output.replace(target)
    return target
