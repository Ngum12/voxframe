"""Named, local creative settings; never recordings or track paths."""
from __future__ import annotations

import json
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from voxframe.config.captions import CaptionTreatment
from voxframe.config.userprefs import config_path
from voxframe.plan.audio_mix import AudioMix

LOCK = RLock()


class CreativeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    caption_treatment: CaptionTreatment | None = None
    audio_mix: AudioMix = Field(default_factory=AudioMix)


class CreativePreset(CreativeSettings):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=60)


def load() -> list[CreativePreset]:
    path = config_path().with_name("creative-presets.json")
    if not path.exists():
        return []
    # A damaged file must be surfaced, never overwritten with defaults.
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [CreativePreset.model_validate(item) for item in payload]


def _write(presets: list[CreativePreset]) -> None:
    path = config_path().with_name("creative-presets.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps([p.model_dump(mode="json") for p in presets]), encoding="utf-8")
    temporary.replace(path)


def save(name: str, settings: CreativeSettings) -> CreativePreset:
    name = " ".join(name.split())
    if not name:
        raise ValueError("Give your preset a name.")
    with LOCK:
        presets = load()
        if len(presets) >= 30:
            raise ValueError("You have 30 presets. Delete one before saving another.")
        if any(p.name.casefold() == name.casefold() for p in presets):
            raise ValueError("That preset name is already used. Choose another name.")
        preset = CreativePreset(id=uuid4().hex, name=name, **settings.model_dump())
        _write([*presets, preset])
        return preset


def delete(preset_id: str) -> None:
    with LOCK:
        presets = load()
        remaining = [p for p in presets if p.id != preset_id]
        if len(remaining) == len(presets):
            raise KeyError(preset_id)
        _write(remaining)
