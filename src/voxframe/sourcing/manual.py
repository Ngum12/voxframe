"""Searching online for one scene, by hand (D-142).

The automatic path searches, downloads and matches for every scene that needs
imagery. This is the person doing it for one scene: they type what they want,
see what the sources offer, and choose. Nothing is downloaded in full until
they choose, and what they choose keeps its licence, author and source exactly
as an automatic download does (D-035).

Results are remembered per job under random tokens, so the browser names a
result it was shown and never a URL: a page cannot make the server fetch an
address of its choosing.
"""

from __future__ import annotations

import secrets
import urllib.error
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

import structlog

from voxframe.config.settings import Settings
from voxframe.models.asset import AssetKind
from voxframe.plan.scene_plan import ScenePlan
from voxframe.sourcing.base import Candidate, SearchRequest
from voxframe.sourcing.fetcher import _download_one, download_candidates, search_adapters
from voxframe.sourcing.licenses import DEFAULT_POLICY, LicensePolicy
from voxframe.sourcing.registry import _orientation_for, build_adapters

__all__ = ["SearchError", "SearchResults", "fetch_choice", "fetch_preview", "search"]

log = structlog.get_logger(__name__)

#: Results shown for one search: enough to choose from, few enough to preview
#: quickly on a slow connection.
RESULTS_PER_SEARCH = 12

#: Results remembered per job. Older ones expire first; a person who searches
#: forty times is not going back to the first page.
REMEMBERED_PER_JOB = 120

#: Longest query accepted. Search APIs cap far lower; this only stops abuse.
MAX_QUERY_CHARACTERS = 200

#: A preview is a small image. Anything larger is not a preview.
PREVIEW_MAX_BYTES = 8 * 1024 * 1024

#: Pixel size previews are stored at, on the longer side.
PREVIEW_SIZE = 480

_SOURCE_NAMES = {"pexels": "Pexels", "pixabay": "Pixabay", "openverse": "Openverse"}


class SearchError(RuntimeError):
    """A search or download that could not be done, said in plain words.

    Never carries an adapter's own error text: a request URL can hold an API
    key (Pixabay's does), and these messages reach the browser.
    """


@dataclass
class SearchResults:
    """Results a person has been shown, by token, per job."""

    _by_job: dict[str, OrderedDict[str, Candidate]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def remember(self, job_id: str, candidates: list[Candidate]) -> list[str]:
        with self._lock:
            store = self._by_job.setdefault(job_id, OrderedDict())
            tokens = []
            for candidate in candidates:
                token = secrets.token_urlsafe(12)
                store[token] = candidate
                tokens.append(token)
            while len(store) > REMEMBERED_PER_JOB:
                store.popitem(last=False)
            return tokens

    def get(self, job_id: str, token: str) -> Candidate | None:
        with self._lock:
            return self._by_job.get(job_id, OrderedDict()).get(token)


def _source_name(source: str) -> str:
    return _SOURCE_NAMES.get(source, source.capitalize())


def search(
    query: str,
    plan: ScenePlan,
    settings: Settings,
    *,
    policy: LicensePolicy = DEFAULT_POLICY,
    limit: int = RESULTS_PER_SEARCH,
) -> tuple[list[Candidate], list[str]]:
    """Search every configured source for images.

    Returns:
        The candidates, interleaved by source, and the names of any sources
        that failed -- names only, never their error text.

    Raises:
        SearchError: If the query is empty or too long.
    """
    query = " ".join(query.split())
    if not query:
        raise SearchError("Type what you would like to see.")
    if len(query) > MAX_QUERY_CHARACTERS:
        raise SearchError(f"Keep the search under {MAX_QUERY_CHARACTERS} characters.")

    adapters = build_adapters(settings, policy)
    found, failures = search_adapters(
        adapters,
        SearchRequest(
            query=query,
            orientation=_orientation_for(plan),
            kind=AssetKind.IMAGE,
            limit=limit,
            language=plan.language,
            commercial_only=not policy.allow_non_commercial,
        ),
    )
    failed = sorted({_source_name(name) for name, _ in failures})
    log.info("search.manual", results=len(found), failed=failed)
    return found[:limit], failed


def fetch_preview(candidate: Candidate, directory: Path, token: str) -> Path:
    """A small JPEG of one result, cached by token.

    Fetched by the server rather than linked, because the page's content
    policy admits images from the app alone, and re-encoded so that what
    reaches the browser is an image the server itself wrote.

    Raises:
        SearchError: If the preview cannot be fetched or is not an image.
    """
    target = directory / f"{token}.jpg"
    if target.is_file():
        return target

    directory.mkdir(parents=True, exist_ok=True)
    source = Candidate(
        url=candidate.preview_url or candidate.url, license=candidate.license
    )
    try:
        raw = _download_one(source, directory, f"{token}.raw", size_cap=PREVIEW_MAX_BYTES)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise SearchError("That preview could not be fetched.") from exc

    try:
        from PIL import Image

        with Image.open(raw) as image:
            image = image.convert("RGB")
            image.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE))
            image.save(target, "JPEG", quality=82)
    except Exception as exc:
        raise SearchError("That preview is not an image.") from exc
    finally:
        raw.unlink(missing_ok=True)
    return target


def fetch_choice(
    candidate: Candidate, directory: Path, query: str, settings: Settings
) -> tuple[Path, int, int]:
    """Download the chosen result in full, with its provenance beside it.

    Returns:
        The file, and its width and height.

    Raises:
        SearchError: If it cannot be downloaded or is not a readable image.
    """
    stem_directory = directory / secrets.token_hex(8)
    result = download_candidates(
        [candidate],
        stem_directory,
        query=query,
        max_file_mb=settings.resolved_max_clip_mb,
    )
    if not result.downloaded:
        raise SearchError(
            f"{_source_name(candidate.license.source)} did not send the image. "
            "Try another one."
        )
    path = result.downloaded[0]
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise SearchError("That download is not a readable image.") from exc
    return path, width, height
