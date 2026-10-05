"""Searching online for one scene, by hand (D-142).

The automatic path searches, downloads and matches for every scene that needs
imagery. This is the person doing it for one scene: they type what they want,
see what the sources offer, and choose. Video previews download a temporary
clip to make a muted MP4; images use small thumbnails. The chosen source keeps
its licence, author and source exactly as an automatic download does (D-035).

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
from tempfile import TemporaryDirectory
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
    kind: AssetKind = AssetKind.IMAGE,
) -> tuple[list[Candidate], list[str]]:
    """Search every configured source for the requested media type.

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
            kind=kind,
            limit=limit,
            language=plan.language,
            commercial_only=not policy.allow_non_commercial,
        ),
    )
    failed = sorted({_source_name(name) for name, _ in failures})
    log.info("search.manual", results=len(found), failed=failed)
    return found[:limit], failed


def video_info(path: Path) -> tuple[int, int, float]:
    """Probe a downloaded standalone clip without allowing playlist/network reads."""
    import json

    from voxframe.render.encode.probe import probe_capabilities
    from voxframe.render.ffpath import run_ffmpeg

    caps = probe_capabilities()
    if not caps.ffprobe_path:
        raise SearchError("FFprobe is needed to read video clips.")
    try:
        payload = json.loads(run_ffmpeg(caps.ffprobe_path, ["-v", "error",
            "-protocol_whitelist", "file,pipe", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration,format_name",
            "-of", "json", str(path.resolve())]).stdout)
        stream = payload["streams"][0]
        form = payload["format"]
        width, height = int(stream["width"]), int(stream["height"])
        seconds = float(form["duration"])
        if (not set(form["format_name"].split(",")) & {"mov", "mp4", "matroska", "webm"}
                or width <= 0 or height <= 0 or not 0 < seconds <= 7200):
            raise ValueError("invalid clip")
    except Exception as exc:
        raise SearchError("That download is not a readable standalone video clip.") from exc
    return width, height, seconds


def fetch_video_preview(
    candidate: Candidate, directory: Path, token: str, settings: Settings,
) -> Path:
    """A muted, browser-compatible MP4 preview of the first 15 seconds."""
    from voxframe.render.encode.probe import probe_capabilities
    from voxframe.render.ffpath import run_ffmpeg

    target = directory / f"{token}.mp4"
    if target.is_file():
        return target
    directory.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial.mp4")
    try:
        with TemporaryDirectory(prefix="clip-", dir=directory) as temporary:
            source, _, _ = fetch_choice(candidate, Path(temporary), "preview", settings)
            run_ffmpeg(probe_capabilities().ffmpeg_path, ["-v", "error",
                "-protocol_whitelist", "file,pipe", "-i", str(source.resolve()), "-t", "15",
                "-an", "-vf",
                "scale=480:480:force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", "-y", str(partial.resolve())])
        partial.replace(target)
    except Exception as exc:
        raise SearchError("This clip preview could not be made. Try another result.") from exc
    finally:
        partial.unlink(missing_ok=True)
    return target


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
        SearchError: If it cannot be downloaded or is not readable media.
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
            f"{_source_name(candidate.license.source)} did not send the requested media. "
            "Try another one."
        )
    path = result.downloaded[0]
    if candidate.kind is AssetKind.VIDEO:
        width, height, _ = video_info(path)
        return path, width, height
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
