"""The library, as the web app's Library screen manages it (D-146).

Three things a person does with their library: add their own photographs and
clips, see what is there and where it came from, and take things out. Each
keeps provenance intact: an upload is credited to the name the person gives,
under the licence they choose (own work by default), and written beside the
file exactly as a download's is (D-072), so the ordinary ingest reads it back.

Nothing here decides where the library lives or who may reach it; the API's
path sandbox does that (D-115, D-143).
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from voxframe.library.db import AssetLibrary
from voxframe.library.ingest import VIDEO_SUFFIXES, IngestResult, ingest_directory
from voxframe.models.asset import Asset, LicenseInfo
from voxframe.models.provenance import sidecar_path, write_provenance

if TYPE_CHECKING:
    from voxframe.config.settings import Settings
    from voxframe.jobs.store import JobStore
    from voxframe.library.embeddings import Embedder

__all__ = [
    "MAX_CLIP_BYTES",
    "MAX_PHOTO_BYTES",
    "OWN_SOURCE",
    "PHOTO_SUFFIXES",
    "DeleteResult",
    "LibraryError",
    "UploadBatch",
    "add_batch",
    "asset_usage",
    "delete_asset",
    "shared_embedder",
]

log = structlog.get_logger(__name__)

#: Formats accepted from the browser. Narrower than ingest accepts from a
#: folder: every one of these is something a browser can also preview.
PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
CLIP_SUFFIXES = VIDEO_SUFFIXES

MAX_PHOTO_BYTES = 50 * 1024 * 1024
MAX_CLIP_BYTES = 400 * 1024 * 1024

#: The source recorded for a person's own uploads. The same as a folder they
#: ingest from the command line: credits then read "Jane Doe (Own work)",
#: with no "via" (see ``PlanAsset.attribution``).
OWN_SOURCE = "local"

#: Where uploads are kept, inside the library.
UPLOADS = "own"


class LibraryError(ValueError):
    """Something a person asked of the library that cannot be done, in plain words."""


@dataclass
class UploadBatch:
    """Files written for one upload, awaiting ingest."""

    directory: Path
    files: list[Path] = field(default_factory=list)


@dataclass
class DeleteResult:
    removed: bool
    #: Whether the file itself was deleted. Only files the Library screen added
    #: are; a folder the person ingested themselves is theirs, and only its
    #: entry in the library goes.
    file_deleted: bool


def new_batch(library_root: Path) -> UploadBatch:
    directory = library_root / UPLOADS / secrets.token_hex(6)
    directory.mkdir(parents=True, exist_ok=True)
    return UploadBatch(directory=directory)


def provenance_for(author: str, license_name: str) -> LicenseInfo:
    """Provenance for a person's own upload.

    Raises:
        LibraryError: No name to credit. A blank would credit nobody (D-012).
    """
    author = " ".join(author.split())
    if not author:
        raise LibraryError("Say whose work this is, for the credits.")
    return LicenseInfo(
        name=" ".join(license_name.split()) or "Own work",
        author=author[:120],
        source=OWN_SOURCE,
        requires_attribution=True,
        allows_commercial=True,
    )


def add_batch(
    batch: UploadBatch,
    library: AssetLibrary,
    embedder: Embedder,
    license_info: LicenseInfo,
) -> IngestResult:
    """Record provenance beside each file and add the batch to the library.

    Files the library already holds are skipped by the ingest and deleted
    here, so a repeated upload leaves nothing behind.
    """
    for path in batch.files:
        write_provenance(path, license_info)

    result = ingest_directory(batch.directory, library, embedder, license_info)

    for path in [*result.skipped_duplicates, *(path for path, _ in result.failed)]:
        path.unlink(missing_ok=True)
        sidecar_path(path).unlink(missing_ok=True)
    log.info("library.uploaded", summary=result.summary())
    return result


def delete_asset(library: AssetLibrary, library_root: Path, asset_id: str) -> DeleteResult:
    """Take an asset out of the library.

    Its file is deleted only if the Library screen put it there (under
    ``own/``). Anything else -- a download, a folder ingested from the command
    line -- keeps its file; downloads are cheap to fetch again, and a person's
    own folder is not the app's to empty.
    """
    asset = library.get(asset_id)
    if asset is None:
        return DeleteResult(removed=False, file_deleted=False)

    library.remove(asset_id)

    deleted = False
    uploads = (library_root / UPLOADS).resolve()
    path = Path(asset.path).resolve()
    if uploads in path.parents and path.is_file():
        path.unlink()
        sidecar_path(path).unlink(missing_ok=True)
        (path.parent / ".frames" / f"{asset.sha256[:16]}.jpg").unlink(missing_ok=True)
        deleted = True
    log.info("library.deleted", asset=asset_id, file_deleted=deleted)
    return DeleteResult(removed=True, file_deleted=deleted)


def asset_usage(store: JobStore) -> dict[str, list[dict[str, str]]]:
    """Which videos show each asset, by asset id.

    Read from each job's saved plan: the plan is the record of what a video
    shows (D-011). A plan that cannot be read is skipped, not fatal.
    """
    usage: dict[str, list[dict[str, str]]] = {}
    for job in store.all_jobs():
        plan_path = store.artifact_path(job.id, "plan")
        if plan_path is None or not plan_path.is_file():
            continue
        try:
            plan: dict[str, Any] = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        title = str(job.options.get("title") or "") or job.audio_name
        seen: set[str] = set()
        for scene in plan.get("scenes", []):
            asset = scene.get("asset") or {}
            asset_id = asset.get("id")
            if asset_id and asset_id not in seen:
                seen.add(asset_id)
                usage.setdefault(asset_id, []).append({"job_id": job.id, "title": title})
    return usage


_EMBEDDERS: dict[tuple[str, bool], Embedder] = {}
_EMBEDDER_LOCK = threading.Lock()


def shared_embedder(settings: Settings) -> Embedder:
    """One embedding model per server, loaded on first use.

    Loading takes about twenty seconds; an upload should pay that once, not
    every time.
    """
    from voxframe.library.embeddings import Embedder

    key = (settings.resolved_embed_model, settings.use_gpu)
    with _EMBEDDER_LOCK:
        if key not in _EMBEDDERS:
            _EMBEDDERS[key] = Embedder(use_gpu=settings.use_gpu, model_key=key[0])
        return _EMBEDDERS[key]


def describe(asset: Asset, library_root: Path) -> dict[str, Any]:
    """An asset as the Library screen shows it. Never its path."""
    uploads = (library_root / UPLOADS).resolve()
    return {
        "id": asset.id,
        "kind": asset.kind.value,
        "width": asset.width,
        "height": asset.height,
        "duration": asset.duration,
        "author": asset.license.author,
        "license": asset.license.name,
        "source": asset.license.source,
        "source_url": asset.license.source_url,
        "added_at": asset.added_at.isoformat() if asset.added_at else None,
        "uploaded_here": uploads in Path(asset.path).resolve().parents,
    }
