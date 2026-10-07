"""Persistent, source-specific approvals for bookend signature exports."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from voxframe.config.bookend_signatures import BookendSignature
from voxframe.plan.bookend_audition import BookendChoice


class Approval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_job: str = Field(pattern=r"^[a-f0-9]{32}$")
    preview_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    signature_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    signature_name: str = Field(min_length=1, max_length=60)
    choice: BookendChoice


def _key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


def record(
    directory: Path,
    preview_id: str,
    source_job: str,
    signature: BookendSignature,
    choice: BookendChoice,
) -> str:
    approval = Approval(
        source_job=source_job,
        preview_id=preview_id,
        signature_id=signature.id,
        signature_name=signature.name,
        choice=choice,
    )
    payload = approval.model_dump(mode="json")
    key = _key(payload)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"{uuid4().hex}.partial"
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(directory / f"{key}.json")
    finally:
        temporary.unlink(missing_ok=True)
    return key


def metadata(directory: Path, key: str) -> Approval:
    if re.fullmatch(r"[a-f0-9]{24}", key) is None:
        raise ValueError("This collection preview is invalid.")
    payload = json.loads((directory / f"{key}.json").read_text(encoding="utf-8"))
    approval = Approval.model_validate(payload)
    if _key(approval.model_dump(mode="json")) != key:
        raise ValueError("This collection approval changed. Preview the recording again.")
    return approval
