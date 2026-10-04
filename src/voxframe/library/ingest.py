"""Ingest images into the library.

Every asset gains an embedding, dominant colours, a perceptual hash and
provenance. Provenance is required at the call site rather than defaulted,
because a default would quietly attribute someone else's photograph to nobody
(D-012, D-035).

Duplicate handling
------------------
Two kinds, detected differently:

- **Exact duplicates** share a SHA-256. Skipped: the same file re-ingested is
  not new information.
- **Near-duplicates** share a perceptual hash within a small Hamming distance:
  the same photograph resized, re-compressed or lightly cropped. Reported but
  kept by default, since a user may legitimately hold several crops of one
  image and deleting their files is not this tool's decision.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import structlog
from PIL import Image

from voxframe.library.db import AssetLibrary
from voxframe.library.embeddings import Embedder, EmbedderUnavailable
from voxframe.models.asset import Asset, AssetKind, LicenseInfo
from voxframe.models.provenance import read_sourced_provenance

__all__ = [
    "IMAGE_SUFFIXES",
    "VIDEO_SUFFIXES",
    "IngestError",
    "IngestResult",
    "ingest_directory",
]

log = structlog.get_logger(__name__)

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"})

#: Video containers the renderer can consume.
VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mov", ".m4v"})

#: Where in a clip to take the frame that represents it.
#:
#: A third of the way in rather than the first frame: clips often open on
#: a fade, a title card or an establishing beat that does not describe what
#: the clip is actually of (D-085).
CLIP_SAMPLE_POSITION = 0.33

#: Perceptual hashes within this Hamming distance are treated as near-duplicates.
#: 5 of 64 bits tolerates re-compression and mild crops without merging images
#: that merely share a colour scheme.
PHASH_THRESHOLD = 5


class IngestError(RuntimeError):
    """Raised when ingest cannot proceed."""


@dataclass(slots=True)
class IngestResult:
    """What an ingest run did."""

    added: list[Asset] = field(default_factory=list)
    skipped_duplicates: list[Path] = field(default_factory=list)
    near_duplicates: list[tuple[Path, str]] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def total_seen(self) -> int:
        return (
            len(self.added)
            + len(self.skipped_duplicates)
            + len(self.failed)
        )

    def summary(self) -> str:
        parts = [f"{len(self.added)} added"]
        if self.skipped_duplicates:
            parts.append(f"{len(self.skipped_duplicates)} duplicates skipped")
        if self.near_duplicates:
            parts.append(f"{len(self.near_duplicates)} near-duplicates")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return ", ".join(parts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _perceptual_hash(image: Image.Image, size: int = 8) -> str:
    """A 64-bit difference hash, as hex.

    Compares each pixel with its right neighbour in a tiny greyscale version,
    so the hash reflects structure rather than exact pixel values. That makes
    it stable across re-compression and resizing, which is what identifies a
    near-duplicate.
    """
    small = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = np.asarray(small, dtype=np.int16)

    differences = pixels[:, 1:] > pixels[:, :-1]
    bits = differences.flatten()

    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _hamming_distance(first: str, second: str) -> int:
    """Differing bits between two hex hashes."""
    if not first or not second or len(first) != len(second):
        return 64
    return bin(int(first, 16) ^ int(second, 16)).count("1")


def _dominant_colors(image: Image.Image, count: int = 5) -> tuple[str, ...]:
    """The most common colours, as hex.

    Used in Phase 5 to keep neighbouring scenes visually consistent. Quantising
    to a small palette first groups near-identical shades, which a raw
    histogram would report as separate colours.
    """
    small = image.convert("RGB").resize((64, 64), Image.Resampling.LANCZOS)
    quantised = small.quantize(colors=count * 2, method=Image.Quantize.MEDIANCUT)
    palette = quantised.getpalette() or []

    counts = sorted(quantised.getcolors() or [], key=lambda pair: -pair[0])

    colors: list[str] = []
    for _, index in counts[:count]:
        base = index * 3
        if base + 2 < len(palette):
            r, g, b = palette[base], palette[base + 1], palette[base + 2]
            colors.append(f"#{r:02x}{g:02x}{b:02x}")

    return tuple(colors)


def _source_tags(sourced: object) -> tuple[str, ...]:
    """Tags from what the source said about the asset.

    The description is split into words and merged with the source's own tags.
    Keeping the words matters: a Pexels result has no tags at all, only an
    ``alt`` sentence, and that sentence is the only clue that an image is a
    photograph of printed text (D-082).
    """
    record = sourced  # typed loosely to avoid a circular import
    words: set[str] = set()

    description = getattr(record, "description", "") or ""
    for word in description.replace(",", " ").split():
        cleaned = word.strip(".,!?;:\"'()").lower()
        if len(cleaned) > 2 and cleaned.isalpha():
            words.add(cleaned)

    words.update(
        tag.strip().lower()
        for tag in getattr(record, "tags", ())
        if tag.strip()
    )

    return tuple(sorted(words))


def _tags_from_path(path: Path, root: Path) -> tuple[str, ...]:
    """Derive tags from the filename and enclosing folders.

    A library organised as ``nature/coastline/cliff-sunset.jpg`` already
    encodes what its images depict; ignoring that would waste the user's own
    organisation.
    """
    tags: set[str] = set()

    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path(path.name)

    for part in relative.parent.parts:
        if part not in (".", ".."):
            tags.update(_split_words(part))

    tags.update(_split_words(path.stem))

    return tuple(sorted(tag for tag in tags if len(tag) > 2))


def _split_words(text: str) -> set[str]:
    """Split a filename or folder into lowercase words."""
    cleaned = text.replace("-", " ").replace("_", " ").replace(".", " ")
    return {word.lower() for word in cleaned.split() if word.isalpha()}


def iter_images(directory: Path) -> Iterator[Path]:
    """Every media file under ``directory``, recursively and in a stable order.

    Includes video: a clip is a library asset like any other, and the matcher
    ranks it against the same embeddings.
    """
    for path in sorted(directory.rglob("*")):
        # Skip our own extracted clip frames: they describe a clip that is
        # already an asset, and ingesting them would duplicate every clip as a
        # still on the next run.
        if any(part.startswith(".") for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in (
            IMAGE_SUFFIXES | VIDEO_SUFFIXES
        ):
            yield path


def _probe_clip(path: Path) -> tuple[int, int, float]:
    """Dimensions and duration of a video clip.

    Delegates to :func:`~voxframe.render.encode.probe.probe_media`, because
    subprocess use is confined to the probe and runner modules (D-020) and an
    architecture test enforces it.
    """
    from voxframe.render.encode.probe import probe_media

    info = probe_media(path)
    return info.width, info.height, info.duration


def _extract_clip_frame(path: Path, destination: Path, duration: float) -> Path:
    """Write one representative frame from a clip, for embedding and hashing.

    CLIP embeds images, so a clip is represented by a frame. Taken a third of
    the way in rather than at the start, where a fade or title card would
    describe the clip badly.
    """
    from voxframe.render.encode.probe import probe_capabilities
    from voxframe.render.ffpath import run_ffmpeg

    caps = probe_capabilities()
    position = max(0.0, duration * CLIP_SAMPLE_POSITION)

    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-ss", f"{position:.3f}",
            "-i", str(path.resolve()),
            "-frames:v", "1",
            "-y", str(destination.resolve()),
        ],
    )
    return destination


def ingest_directory(
    directory: Path,
    library: AssetLibrary,
    embedder: Embedder,
    license_info: LicenseInfo | None = None,
    *,
    batch_size: int = 8,
    detect_near_duplicates: bool = True,
) -> IngestResult:
    """Ingest every image in a directory.

    Args:
        directory: Folder to scan, recursively.
        library: Destination library.
        embedder: Produces the CLIP embeddings.
        license_info: Provenance for files that have no sidecar. A file
            written by a sourcing adapter carries its own
            ``.provenance.json`` and uses that instead, so a library built
            from several sources credits each asset correctly rather than
            tagging everything with one blanket license (D-072).

            Required unless every file has a sidecar: a default would
            attribute someone's work to nobody (D-012).
        batch_size: Images per embedding batch.
        detect_near_duplicates: Report visually similar images.

    Returns:
        What was added, skipped and failed.

    Raises:
        IngestError: If the directory does not exist, or a file has neither a
            sidecar nor a supplied ``license_info``.
        EmbedderUnavailable: If the model cannot be loaded at all (D-163).
    """
    if not directory.is_dir():
        raise IngestError(f"Not a directory: {directory}")

    result = IngestResult()
    paths = list(iter_images(directory))

    # Extracted clip frames live beside the clips, not in a temp directory:
    # re-embedding later (voxframe reembed) needs them again.
    frames_dir = directory / ".frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    if not paths:
        log.warning("ingest.empty", directory=str(directory))
        return result

    log.info("ingest.start", directory=str(directory), files=len(paths))

    existing_hashes = (
        {asset.phash: asset.id for asset in library.all_assets() if asset.phash}
        if detect_near_duplicates
        else {}
    )

    pending: list[tuple[Path, Asset]] = []

    for path in paths:
        try:
            content_hash = _sha256(path)

            if library.by_hash(content_hash) is not None:
                result.skipped_duplicates.append(path)
                continue

            is_video = path.suffix.lower() in VIDEO_SUFFIXES
            duration: float | None = None
            # For a clip this is the extracted frame, which is what gets
            # embedded; for a still it is the file itself.
            embed_source = path

            if is_video:
                width, height, seconds = _probe_clip(path)
                duration = seconds or None

                frame = frames_dir / f"{content_hash[:16]}.jpg"
                _extract_clip_frame(path, frame, seconds)
                embed_source = frame

                with Image.open(frame) as image:
                    phash = _perceptual_hash(image)
                    colors = _dominant_colors(image)
            else:
                with Image.open(path) as image:
                    width, height = image.size
                    phash = _perceptual_hash(image)
                    colors = _dominant_colors(image)

            if detect_near_duplicates:
                for known_hash, known_id in existing_hashes.items():
                    if _hamming_distance(phash, known_hash) <= PHASH_THRESHOLD:
                        result.near_duplicates.append((path, known_id))
                        break

            # A sidecar written by an adapter wins: it describes this exact
            # file, where license_info describes the folder as a whole.
            sourced = read_sourced_provenance(path)
            provenance = sourced.license if sourced is not None else license_info
            if provenance is None:
                raise IngestError(
                    f"{path.name} has no provenance sidecar and no license was "
                    f"supplied. Every asset must be attributable (D-012); pass "
                    f"--license, --author and --source, or ingest a directory "
                    f"written by a sourcing adapter."
                )

            asset = Asset(
                id=content_hash[:16],
                path=path,
                kind=AssetKind.VIDEO if is_video else AssetKind.IMAGE,
                sha256=content_hash,
                width=width,
                height=height,
                duration=duration,
                license=provenance,
                # The source's own description and tags beat anything derived
                # from a generated filename like "pexels_0015.jpg".
                tags=(
                    _source_tags(sourced)
                    if sourced is not None and sourced.search_text()
                    else _tags_from_path(path, directory)
                ),
                dominant_colors=colors,
                phash=phash,
            )

            pending.append((embed_source, asset))
            existing_hashes[phash] = asset.id

            if len(pending) >= batch_size:
                _flush(pending, library, embedder, result)
                pending = []

        except IngestError:
            # Missing provenance is a caller mistake, not a broken file, and
            # continuing would silently build an unattributable library.
            raise
        except (OSError, ValueError) as exc:
            # One unreadable file must not abandon the whole ingest: a library
            # of thousands will contain the occasional broken download.
            log.warning("ingest.failed", path=str(path), error=str(exc))
            result.failed.append((path, str(exc)))

    if pending:
        _flush(pending, library, embedder, result)

    log.info("ingest.done", summary=result.summary())
    return result


def _flush(
    pending: Sequence[tuple[Path, Asset]],
    library: AssetLibrary,
    embedder: Embedder,
    result: IngestResult,
) -> None:
    """Embed and store a batch."""
    paths = [path for path, _ in pending]

    try:
        embeddings = embedder.embed_images(paths)
    except EmbedderUnavailable:
        # Not these files' fault: nothing can be embedded. Recording each as
        # failed hid exactly that (D-163), so it goes to the caller.
        raise
    except Exception as exc:
        log.warning("ingest.embed_failed", files=len(paths), error=str(exc))
        for path, _ in pending:
            result.failed.append((path, f"embedding failed: {exc}"))
        return

    for (path, asset), embedding in zip(pending, embeddings, strict=True):
        try:
            library.add(asset, embedding=embedding, embed_model=embedder.model_id)
            result.added.append(asset)
        except Exception as exc:
            result.failed.append((path, str(exc)))


def reembed_library(
    library: AssetLibrary,
    embedder: Embedder,
    *,
    batch_size: int = 8,
    progress: Callable[[int, int], None] | None = None,
) -> IngestResult:
    """Re-embed every asset whose vectors came from a different model.

    The remedy offered by :class:`~voxframe.library.db.EmbeddingModelMismatch`.
    Switching embedding models invalidates existing vectors, because different
    models occupy unrelated coordinate systems; this recomputes them.

    Args:
        library: The library to update.
        embedder: The new embedder. Its ``model_id`` becomes the library's.
        batch_size: Images per embedding batch.
        progress: Items processed (including failures) and the total to update.

    Returns:
        What was re-embedded and what failed. Assets whose files have since
        been moved or deleted are reported as failures rather than removed:
        deleting a user's records because a path broke is not this tool's
        decision.
    """
    result = IngestResult()
    stale = library.assets_needing_reembed(embedder.model_id)

    if not stale:
        log.info("reembed.nothing_to_do", model=embedder.model_id)
        return result

    log.info("reembed.start", assets=len(stale), model=embedder.model_id)

    def report() -> None:
        if progress is not None:
            progress(len(result.added) + len(result.failed), len(stale))

    report()
    batch: list[Asset] = []
    for asset in stale:
        if not asset.path.is_file():
            result.failed.append((asset.path, "file no longer exists"))
            report()
            continue

        batch.append(asset)
        if len(batch) >= batch_size:
            _reembed_batch(batch, library, embedder, result)
            report()
            batch = []

    if batch:
        _reembed_batch(batch, library, embedder, result)
        report()

    log.info("reembed.done", summary=result.summary())
    return result


def _reembed_batch(
    batch: list[Asset],
    library: AssetLibrary,
    embedder: Embedder,
    result: IngestResult,
) -> None:
    """Recompute and store embeddings for one batch."""
    paths: list[Path] = []
    ready: list[Asset] = []
    for asset in batch:
        try:
            source = asset.path
            if asset.kind == AssetKind.VIDEO:
                source = asset.path.parent / ".frames" / f"{asset.sha256[:16]}.jpg"
                if not source.is_file():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    _extract_clip_frame(asset.path, source, asset.duration or 0)
            paths.append(source)
            ready.append(asset)
        except Exception as exc:
            result.failed.append((asset.path, f"could not read clip frame: {exc}"))
    if not ready:
        return

    try:
        embeddings = embedder.embed_images(paths)
    except EmbedderUnavailable:
        raise
    except Exception as exc:
        log.warning("ingest.embed_failed", files=len(paths), error=str(exc))
        for asset in ready:
            result.failed.append((asset.path, f"embedding failed: {exc}"))
        return

    for asset, embedding in zip(ready, embeddings, strict=True):
        try:
            library.add(asset, embedding=embedding, embed_model=embedder.model_id)
            result.added.append(asset)
        except Exception as exc:
            result.failed.append((asset.path, str(exc)))
