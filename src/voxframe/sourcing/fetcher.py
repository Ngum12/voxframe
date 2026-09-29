"""Download candidates into the library, with provenance intact.

The scene plan names what each scene is about; this turns that into a library
that can actually fill those scenes. The functional goal of Phase 4 is that a
typical narration produces a video with no empty gradient scenes (D-069).

Design choices worth stating:

**Per-asset provenance, written at fetch time.** Each downloaded file gets a
sidecar recording its license, author and source URL. Ingest reads it back, so
a library assembled from six sources credits each asset correctly rather than
tagging everything with one blanket license. `ingest_directory` takes a single
`LicenseInfo` for a folder the user organised themselves, which is right for
that case and wrong for this one.

**Download is separated from ingest.** Files land in a staging directory, then
`ingest_directory` hashes, deduplicates and embeds them. Reusing it rather than
reimplementing means near-duplicate detection and the embedding-model guard
apply to sourced assets exactly as they do to local ones.

**A failed source is not a failed render.** Network errors are collected and
reported, never raised: a user on a train should get the scenes that could be
filled, not a traceback.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from voxframe.models.provenance import (
    PROVENANCE_SUFFIX,
    read_provenance,
    write_provenance,
)
from voxframe.sourcing.base import Adapter, AdapterError, Candidate, SearchRequest

__all__ = [
    "PROVENANCE_SUFFIX",
    "FetchResult",
    "download_candidates",
    "read_provenance",
    "search_adapters",
]

log = structlog.get_logger(__name__)

USER_AGENT = "voxframe/0.1 (+https://github.com/Ngum12/voxframe)"

#: Refuse anything larger. A 4K photograph is a few MB; something far bigger is
#: either a mistake or a resource the user did not intend to download.
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024

#: Extensions accepted from a URL, mapped from the served content type where
#: the URL itself is uninformative.
_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


@dataclass
class FetchResult:
    """What a fetch run downloaded, skipped and failed to get."""

    downloaded: list[Path] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    total_bytes: int = 0

    @property
    def count(self) -> int:
        return len(self.downloaded)

    @property
    def megabytes(self) -> float:
        """Downloaded size, reported per render so bandwidth is visible."""
        return self.total_bytes / (1024 * 1024)

    def summary(self) -> str:
        parts = [f"{self.count} downloaded ({self.megabytes:.1f} MB)"]
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        if self.failures:
            parts.append(f"{len(self.failures)} failed")
        return ", ".join(parts)


def _extension_for(candidate: Candidate, content_type: str) -> str:
    """Pick a file extension from the URL, falling back to the content type."""
    suffix = Path(urllib.parse.urlparse(candidate.url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm"}:
        return ".jpg" if suffix == ".jpeg" else suffix

    return _CONTENT_TYPES.get(content_type.split(";")[0].strip().lower(), "")


def _download_one(
    candidate: Candidate,
    destination_dir: Path,
    stem: str,
    *,
    size_cap: int = MAX_DOWNLOAD_BYTES,
) -> Path:
    """Fetch one candidate to disk.

    Raises:
        OSError: If the download fails, is too large, or is not media.
    """
    request = urllib.request.Request(
        candidate.url, headers={"User-Agent": USER_AGENT}
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        content_type = response.headers.get("Content-Type", "")

        declared = response.headers.get("Content-Length")
        if declared and int(declared) > size_cap:
            raise OSError(
                f"{int(declared) // (1024 * 1024)} MB exceeds the "
                f"{size_cap // (1024 * 1024)} MB limit"
            )

        extension = _extension_for(candidate, content_type)
        if not extension:
            raise OSError(f"not a recognised media type: {content_type!r}")

        # Read with a cap rather than trusting Content-Length, which a server
        # may omit or understate.
        data = response.read(size_cap + 1)

    if len(data) > size_cap:
        raise OSError(f"larger than the {size_cap // (1024 * 1024)} MB limit")
    if not data:
        raise OSError("empty response")

    path = destination_dir / f"{stem}{extension}"
    path.write_bytes(data)
    return path


def download_candidates(
    candidates: list[Candidate],
    destination_dir: Path,
    *,
    limit: int | None = None,
    query: str = "",
    queries_by_url: dict[str, str] | None = None,
    max_file_mb: int | None = None,
    max_total_mb: int | None = None,
) -> FetchResult:
    """Download candidates and record each one's provenance.

    Args:
        candidates: What to fetch, best first.
        destination_dir: Staging directory. Created if absent.
        limit: Stop after this many successful downloads.
        query: Fallback search term, when the caller has only one.
        queries_by_url: The query that found each candidate, keyed by URL.
            Preferred over ``query``: neighbouring scenes search different
            terms, so one value for a whole batch would misattribute most of
            them (item 25).
        max_file_mb: Refuse any single file larger than this. Clips are an
            order of magnitude larger than stills, so this is what stops one
            4K download dominating a render.
        max_total_mb: Stop once this much has been downloaded. Reported per
            render so bandwidth is visible rather than surprising.

    Returns:
        Paths written, plus what was skipped and what failed. Never raises for
        a network problem: a user offline should get a clear report, not a
        traceback.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)
    result = FetchResult()

    seen_urls: set[str] = set()
    file_cap = (max_file_mb or 0) * 1024 * 1024 or MAX_DOWNLOAD_BYTES
    total_cap = (max_total_mb or 0) * 1024 * 1024

    for index, candidate in enumerate(candidates):
        if limit is not None and result.count >= limit:
            break

        if candidate.url in seen_urls:
            result.skipped.append((candidate.url, "already fetched in this run"))
            continue
        seen_urls.add(candidate.url)

        if total_cap and result.total_bytes >= total_cap:
            result.skipped.append(
                (candidate.url, f"render download cap of {max_total_mb} MB reached")
            )
            continue

        # Name by source and position so a staging directory is readable, and
        # so two adapters cannot collide on the same stem.
        stem = f"{candidate.license.source}_{index:04d}"

        try:
            path = _download_one(
                candidate, destination_dir, stem, size_cap=file_cap
            )
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            result.failures.append((candidate.url, f"{type(exc).__name__}: {exc}"))
            continue

        result.total_bytes += path.stat().st_size
        write_provenance(
            path,
            candidate.license,
            description=candidate.title,
            tags=candidate.tags,
            query=(queries_by_url or {}).get(candidate.url, query),
            original_width=candidate.width,
            original_height=candidate.height,
        )
        result.downloaded.append(path)

    log.info(
        "sourcing.download.done",
        downloaded=result.count,
        skipped=len(result.skipped),
        failed=len(result.failures),
        directory=str(destination_dir),
    )

    return result


def search_adapters(
    adapters: list[Adapter], request: SearchRequest
) -> tuple[list[Candidate], list[tuple[str, str]]]:
    """Search every available adapter, collecting failures rather than raising.

    Returns:
        ``(candidates, failures)``. Candidates are interleaved by adapter so
        one source cannot monopolise the results — with three adapters and a
        limit of six, each contributes roughly two rather than the first
        supplying all six.
    """
    per_adapter: list[list[Candidate]] = []
    failures: list[tuple[str, str]] = []

    for adapter in adapters:
        if not adapter.available():
            log.debug("sourcing.adapter.skipped", adapter=adapter.name)
            continue

        try:
            found = list(adapter.search(request))
        except AdapterError as exc:
            failures.append((adapter.name, str(exc)))
            log.warning("sourcing.adapter.failed", adapter=adapter.name, error=str(exc))
            continue

        if found:
            per_adapter.append(found)

    interleaved: list[Candidate] = []
    for position in range(max((len(group) for group in per_adapter), default=0)):
        for group in per_adapter:
            if position < len(group):
                interleaved.append(group[position])

    return interleaved, failures
