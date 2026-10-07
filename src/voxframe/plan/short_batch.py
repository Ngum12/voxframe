"""Persistent approvals for separate, distinct short exports."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from voxframe.plan.editing import EditError
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import hook_details, tokens
from voxframe.render.compose.complete_preview import winner


def record_preview(directory: Path, snapshot: dict, first: int, last: int) -> str:
    payload = {"preview_id": snapshot["key"], "revision": snapshot["revision"],
               "first_word": first, "last_word": last}
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"{uuid4().hex}.partial"
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(directory / f"{key}.json")
    finally:
        temporary.unlink(missing_ok=True)
    return key


def approved(directory: Path, previews: Path, ids: list[str], revision: str,
             *, check_plan=None) -> list[tuple[str, ScenePlan, dict]]:  # type: ignore[no-untyped-def]
    if len(set(ids)) != len(ids):
        raise EditError("Choose each reviewed clip only once.")
    result = []
    for key in ids:
        metadata = json.loads((directory / f"{key}.json").read_text(encoding="utf-8"))
        if metadata["revision"] != revision:
            raise ValueError("This shortlist belongs to an earlier edit. Preview it again.")
        first, last = metadata["first_word"], metadata["last_word"]
        if not isinstance(first, int) or not isinstance(last, int) or first < 0 or last < first:
            raise ValueError("This clip's word range is no longer available.")
        if any(first <= other[2]["last_word"] and last >= other[2]["first_word"]
               for other in result):
            raise EditError("These clips share spoken words. Trim their ranges before exporting.")
        if re.fullmatch(r"[a-f0-9]{24}", str(metadata["preview_id"])) is None:
            raise ValueError("This clip preview is no longer available.")
        plan = winner(previews, metadata["preview_id"], revision, check_plan=check_plan)
        result.append((key, plan, metadata))
    return result


def passage_details(plan: ScenePlan, first: int, last: int) -> dict:
    timed = tokens(plan)
    return {"text": " ".join(t.value.text for t in timed[first:last + 1]),
            **hook_details(timed, first, last)}


def clean_quotes(plan: ScenePlan) -> ScenePlan:
    """Do not carry automatic quotes from outside the chosen passage."""
    scenes = []
    for scene in plan.scenes:
        beat = scene.visual_beat
        if beat and beat.source == "director" and beat.text:
            quote = " ".join(re.findall(r"\w+", scene.display_text.casefold()))
            text = " ".join(re.findall(r"\w+", beat.text.casefold()))
            if f" {text} " not in f" {quote} ":
                beat = beat.model_copy(update={"text": ""})
        scenes.append(scene.model_copy(update={"visual_beat": beat}))
    return plan.model_copy(update={"scenes": tuple(scenes)})
