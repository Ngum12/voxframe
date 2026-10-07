"""Cached full drafts and immutable winner snapshots, including actual music."""
from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from pathlib import Path

from voxframe.config.settings import QualityPreset
from voxframe.config.style import get_template
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.audio.mixdown import Stems
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose.from_plan import render_from_plan, stems_path
from voxframe.render.encode.probe import probe_capabilities
from voxframe.render.version import RENDERER_VERSION


def sources(plan: ScenePlan) -> set[str]:
    paths = {plan.audio_path}
    if plan.footage and any(plan.shows_speaker(s) for s in plan.scenes):
        paths.add(plan.footage.path)
    paths.update(s.asset.path for s in plan.scenes if s.asset and not plan.shows_speaker(s))
    if plan.music_path:
        paths.add(plan.music_path)
    return paths


def stamps(plan: ScenePlan) -> list:
    paths = sources(plan)
    if plan.score:
        from voxframe.music.score import samples_dir

        root = samples_dir()
        if root.is_dir():
            paths.update(str(p) for p in root.rglob("*") if p.is_file())
    result = []
    for source in sorted(paths):
        path = Path(source).resolve()
        info = path.stat()
        result.append([str(path), info.st_size, info.st_mtime_ns])
    return result


def engine(plan: ScenePlan) -> dict:
    identity = {"renderer": RENDERER_VERSION, "preview": 1}
    if plan.score:
        from voxframe.music.score import SCORE_VERSION, styles

        style = styles().get(plan.score.style)
        identity.update(score=SCORE_VERSION, style=hashlib.sha256(
            (style.source if style else "").encode()).hexdigest())
    return identity


def complete_preview(plan: ScenePlan, revision: str, directory: Path) -> dict:
    material = [plan.model_dump(mode="json"), revision, stamps(plan), engine(plan)]
    key = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:24]
    directory.mkdir(parents=True, exist_ok=True)
    target, manifest = directory / f"{key}.mp4", directory / f"{key}.json"
    if target.is_file() and manifest.is_file():
        return json.loads(manifest.read_text(encoding="utf-8"))
    music = (MusicSettings(path=Path(plan.music_path), credit=plan.music_credit)
             if plan.music_path else None)
    with tempfile.TemporaryDirectory(prefix="render-", dir=directory) as folder:
        output = Path(folder) / "complete.mp4"
        result = render_from_plan(plan, Path(plan.audio_path), get_template(plan.style),
            probe_capabilities(), output, quality=QualityPreset.DRAFT, music=music, height=480,
            cache_dir=directory / "cache" / "segments", write_sidecars=False)
        has_music = Stems.load(stems_path(output)).music is not None
        if (plan.music_path or plan.score) and not has_music:
            raise ValueError("The selected music could not be rendered. "
                             "Choose a saved track or No added music.")
        if stamps(plan) != material[2]:
            raise ValueError("A source file changed while rendering. Render this audition again.")
        snapshot = {"key": key, "revision": revision, "plan": plan.model_dump(mode="json"),
            "stamps": material[2], "engine": material[3], "seconds": plan.total_frames / plan.fps,
            "has_music": has_music, "sound": result.sound, "music_note": result.music_note,
            "note": "Complete draft with voice, visuals and captions" + (
                " and added music." if has_music else ". No added music.")}
        temporary = Path(folder) / "snapshot.json"
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        output.replace(target)
        temporary.replace(manifest)
    return snapshot


def winner(directory: Path, key: str, revision: str, *,
           check_plan: Callable[[ScenePlan], None] | None = None) -> ScenePlan:
    manifest, video = directory / f"{key}.json", directory / f"{key}.mp4"
    if not manifest.is_file() or not video.is_file():
        raise ValueError("Render this complete audition before choosing it.")
    snapshot = json.loads(manifest.read_text(encoding="utf-8"))
    if snapshot["revision"] != revision or snapshot["key"] != key:
        raise ValueError("This audition belongs to an earlier edit. Render it again.")
    plan = ScenePlan.model_validate(snapshot["plan"])
    if check_plan is not None:
        check_plan(plan)
    if stamps(plan) != snapshot["stamps"] or engine(plan) != snapshot["engine"]:
        raise ValueError("A source file changed. Render this audition again before choosing it.")
    return plan
