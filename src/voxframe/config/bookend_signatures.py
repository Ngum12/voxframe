"""Local bookend recipes, without quotes, media paths or replacement permissions."""

from __future__ import annotations

import json
from threading import RLock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from voxframe.config.userprefs import config_path

LOCK = RLock()


class BookendStyle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    opening_look: Literal["authority", "energy", "cinema"]
    closing_look: Literal["authority", "energy", "cinema"]
    opening_shot: Literal["keep", "speaker"] = "keep"
    closing_shot: Literal["keep", "speaker"] = "keep"
    match_captions: bool = True
    opening_words: int = Field(ge=1, le=12)
    closing_words: int = Field(ge=1, le=12)


class BookendSignature(BookendStyle):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=60)


def load() -> list[BookendSignature]:
    path = config_path().with_name("bookend-signatures.json")
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Your saved bookend signature file is invalid. It was kept.")
    try:
        signatures = [BookendSignature.model_validate(item) for item in payload]
    except (ValueError, TypeError) as exc:
        raise ValueError("Your saved bookend signature file is invalid. It was kept.") from exc
    if (
        len(signatures) > 30
        or len({s.id for s in signatures}) != len(signatures)
        or len({s.name.casefold() for s in signatures}) != len(signatures)
    ):
        raise ValueError("The saved bookend signature list is invalid.")
    return signatures


def _write(signatures: list[BookendSignature]) -> None:
    path = config_path().with_name("bookend-signatures.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"bookend-signatures-{uuid4().hex}.partial")
    try:
        temporary.write_text(
            json.dumps([s.model_dump(mode="json") for s in signatures]), encoding="utf-8"
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save(name: str, style: BookendStyle) -> BookendSignature:
    name = " ".join(name.split())
    if not name:
        raise ValueError("Give your bookend signature a name.")
    with LOCK:
        signatures = load()
        if len(signatures) >= 30:
            raise ValueError("You have 30 bookend signatures. Remove one before saving another.")
        if any(s.name.casefold() == name.casefold() for s in signatures):
            raise ValueError("That signature name is already used. Choose another name.")
        signature = BookendSignature(id=uuid4().hex, name=name, **style.model_dump())
        _write([*signatures, signature])
        return signature


def delete(signature_id: str) -> None:
    with LOCK:
        signatures = load()
        remaining = [s for s in signatures if s.id != signature_id]
        if len(remaining) == len(signatures):
            raise KeyError(signature_id)
        _write(remaining)
