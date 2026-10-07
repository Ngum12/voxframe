"""Immutable destination auditions of a finished clip's saved edit."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from voxframe.config.short_export import PRESETS
from voxframe.plan.audio_mix import LOUDNESS_TARGETS, Destination


def profiles() -> dict:
    result = {}
    for platform, preset in PRESETS.items():
        target = LOUDNESS_TARGETS[Destination.WHATSAPP if platform == "whatsapp"
            else Destination.YOUTUBE if platform == "youtube" else Destination.SOCIAL]
        result[platform] = {"label": preset["label"],
                           "settings": preset["export"].model_dump(mode="json"),
                           "target_lufs": target.lufs, "true_peak": target.true_peak}
    return result


def record(directory: Path, snapshot: dict, source_job: str) -> str:
    payload = {"source_job": source_job, "revision": snapshot["revision"],
               "preview_id": snapshot["key"],
               "platform": snapshot["plan"]["short_export"]["platform"]}
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"{uuid4().hex}.partial"
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(directory / f"{key}.json")
    finally:
        temporary.unlink(missing_ok=True)
    return key


def metadata(directory: Path, key: str) -> dict:
    payload = json.loads((directory / f"{key}.json").read_text(encoding="utf-8"))
    for field, length in (("source_job", 32), ("revision", 24), ("preview_id", 24)):
        if re.fullmatch(rf"[a-f0-9]{{{length}}}", str(payload[field])) is None:
            raise ValueError("This variant is no longer available. Preview it again.")
    if payload["platform"] not in PRESETS:
        raise ValueError("This destination is no longer available.")
    return payload
