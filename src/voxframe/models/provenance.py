"""The per-asset provenance sidecar.

A sourcing adapter writes one of these beside every file it downloads, and
ingest reads it back, so a library assembled from several sources credits each
asset correctly rather than tagging everything with one blanket license
(D-072).

This lives in ``models`` rather than beside the fetcher because both the
library and the sourcing package need it, and putting it in either one would
make them import each other.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from voxframe.models.asset import LicenseInfo

__all__ = [
    "PROVENANCE_SUFFIX",
    "SourcedProvenance",
    "read_provenance",
    "read_sourced_provenance",
    "write_provenance",
]

log = structlog.get_logger(__name__)

#: Appended to the media filename, e.g. ``photo.jpg.provenance.json``.
#:
#: Appended rather than replacing the suffix so two files that differ only by
#: extension cannot share one sidecar.
PROVENANCE_SUFFIX = ".provenance.json"


class SourcedProvenance(BaseModel):
    """Everything an adapter knew about an asset when it fetched it.

    The license is mandatory (D-012). The rest exists so the library keeps what
    the source said rather than re-deriving a worse version of it: the
    description is the only signal for spotting a text-bearing image, and the
    query records *why* this asset is in the library at all (item 25, D-082).
    """

    model_config = {"frozen": True}

    license: LicenseInfo
    description: str = Field(default="", description="The source's own caption.")
    tags: tuple[str, ...] = Field(default=())
    query: str = Field(default="", description="The search that found it.")
    original_width: int = Field(default=0, ge=0)
    original_height: int = Field(default=0, ge=0)

    def search_text(self) -> str:
        """Description and tags together, for keyword screening."""
        return " ".join([self.description, *self.tags]).strip()


def sidecar_path(media_path: Path) -> Path:
    """Where a media file's provenance sidecar lives."""
    return media_path.with_suffix(media_path.suffix + PROVENANCE_SUFFIX)


def write_provenance(
    media_path: Path,
    license_info: LicenseInfo,
    *,
    description: str = "",
    tags: tuple[str, ...] = (),
    query: str = "",
    original_width: int = 0,
    original_height: int = 0,
) -> Path:
    """Record one asset's provenance beside it."""
    record = SourcedProvenance(
        license=license_info,
        description=description,
        tags=tags,
        query=query,
        original_width=original_width,
        original_height=original_height,
    )
    path = sidecar_path(media_path)
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    return path


def read_sourced_provenance(media_path: Path) -> SourcedProvenance | None:
    """The full record beside a downloaded file, or ``None``.

    Accepts the older bare-``LicenseInfo`` shape too, so a library written
    before this existed still ingests rather than being rejected.
    """
    path = sidecar_path(media_path)
    if not path.is_file():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("provenance.unreadable", path=str(path), error=str(exc))
        return None

    try:
        if "license" in raw:
            return SourcedProvenance.model_validate(raw)
        # Older sidecar: the license fields sat at the top level.
        return SourcedProvenance(license=LicenseInfo.model_validate(raw))
    except ValueError as exc:
        log.warning("provenance.invalid", path=str(path), error=str(exc))
        return None


def read_provenance(media_path: Path) -> LicenseInfo | None:
    """Read the sidecar written beside a downloaded file.

    Returns:
        The recorded provenance, or ``None`` when there is no sidecar or it is
        unreadable.

    Note:
        A missing sidecar means the file did not come from an adapter — a user
        dropped it in by hand — so the caller falls back to whatever license
        they supplied for the folder. An unreadable one is logged rather than
        raised: ingesting with the folder's license beats dropping an asset
        over malformed JSON.
    """
    record = read_sourced_provenance(media_path)
    return record.license if record is not None else None
