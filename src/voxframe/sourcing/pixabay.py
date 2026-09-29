"""Pixabay: stock photographs and video clips. Optional, needs a free key.

Pixabay's terms impose obligations the other adapters do not, and each is
implemented rather than noted (D-080):

**Cache responses for 24 hours.** Required by their terms. Handled by
:class:`~voxframe.sourcing.http.RequestCache`, keyed without the API key.

**No permanent hotlinking.** ``webformatURL`` expires after 24 hours, so it
must never become an asset's stored path. Selected images are downloaded into
the library before use, and the expiring URL is kept only as the fetch source.

**No systematic mass downloading.** Only candidates the matcher actually
selects are downloaded, not every search result.

**The key travels in the query string**, so every URL is redacted before it
reaches a log, an error or a cache key.

**Resolution is capped for standard accounts.** ``fullHDURL`` and ``imageURL``
come back ``None`` without approved full access — verified against the live API,
not assumed — so ``largeImageURL`` at 1280px is the ceiling. The real dimensions
are recorded on the asset so the matcher can prefer a larger candidate when
rendering at 1080p.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

import structlog

from voxframe.models.asset import AssetKind, LicenseInfo
from voxframe.sourcing.base import Adapter, Candidate, SearchRequest
from voxframe.sourcing.http import RequestCache, request_json

__all__ = ["PIXABAY_LICENSE", "PixabayAdapter"]

log = structlog.get_logger(__name__)

API_ROOT = "https://pixabay.com/api"

#: Pixabay publishes its own license, not a Creative Commons one. Named exactly
#: so the credits file states the real terms.
PIXABAY_LICENSE = "Pixabay Content License"

#: Documented and confirmed from the response headers: 100 requests per 60
#: seconds for a standard key.
REQUESTS_PER_WINDOW = 100

#: ``q`` may not exceed 100 characters. Truncated at a word boundary rather
#: than mid-word, which would turn a real term into a nonsense one.
MAX_QUERY_CHARS = 100

#: Pixabay caps a page at 200 and rejects anything below 3.
MAX_PER_PAGE = 200
MIN_PER_PAGE = 3

_ORIENTATION = {"landscape": "horizontal", "portrait": "vertical"}

#: Languages Pixabay accepts. Others are omitted rather than guessed.
_LANGUAGES = frozenset({"en", "fr", "de", "es", "it", "pt", "nl", "pl", "ru"})

#: Video renditions by output height, smallest that still covers it.
#:
#: The 4K "large" rendition is never chosen by default: it is tens of megabytes
#: and looks identical once scaled to 1080p.
_VIDEO_RENDITIONS = ("medium", "small", "tiny", "large")


def truncate_query(query: str, limit: int = MAX_QUERY_CHARS) -> str:
    """Shorten a query to Pixabay's limit, at a word boundary.

    Cutting mid-word would turn "mountain" into "moun", which matches nothing
    and looks like a bug in the results rather than in the request.
    """
    query = query.strip()
    if len(query) <= limit:
        return query

    clipped = query[:limit]
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped.strip()


class PixabayAdapter(Adapter):
    """Search Pixabay for photographs and video clips."""

    name = "pixabay"
    requires_key = True
    terms = (
        "Pixabay Content License: free for commercial use, no attribution "
        "required though Voxframe credits the contributor anyway. Responses "
        "are cached 24h and images are downloaded rather than hotlinked, as "
        "their terms require."
    )

    def __init__(
        self,
        api_key: str | None = None,
        *,
        cache: RequestCache | None = None,
        timeout: float = 30.0,
        min_width: int = 1280,
        output_height: int = 1080,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.cache = cache
        self.timeout = timeout
        self.min_width = min_width
        self.output_height = output_height

    def available(self) -> bool:
        return bool(self.api_key)

    def unavailable_reason(self) -> str:
        if self.available():
            return ""
        return (
            "pixabay needs a free API key. Get one at "
            "https://pixabay.com/api/docs/ and set VOXFRAME_PIXABAY_API_KEY "
            "in .env"
        )

    def search(self, request: SearchRequest) -> tuple[Candidate, ...]:
        if not self.available():
            return ()

        if request.kind is AssetKind.VIDEO:
            return self._search_videos(request)
        return self._search_images(request)

    # --- photographs ---

    def _search_images(self, request: SearchRequest) -> tuple[Candidate, ...]:
        parameters = self._common_parameters(request)
        parameters["image_type"] = "photo"
        parameters["min_width"] = str(self.min_width)

        if orientation := _ORIENTATION.get(request.orientation or ""):
            parameters["orientation"] = orientation

        payload, _ = self._get("/", parameters)

        candidates = [
            candidate
            for hit in payload.get("hits") or []
            if (candidate := self._image_to_candidate(hit)) is not None
        ]
        return tuple(candidates[: request.limit])

    def _image_to_candidate(self, hit: dict[str, Any]) -> Candidate | None:
        """Translate one image hit, preferring the largest available size.

        ``fullHDURL`` and ``imageURL`` are ``None`` on a standard account, so
        ``largeImageURL`` (1280px) is normally the best available. The asset
        records the *original* dimensions from ``imageWidth``/``imageHeight``
        rather than the download's, so the matcher can tell a genuinely
        high-resolution photograph from a small one.
        """
        url = (
            hit.get("fullHDURL")
            or hit.get("largeImageURL")
            or hit.get("webformatURL")
        )
        if not isinstance(url, str) or not url:
            return None

        # Pixabay flags its own low-quality and AI-generated material. Both are
        # dropped: an AI image in a documentary-style video is a claim the user
        # has not agreed to make.
        if hit.get("isLowQuality") or hit.get("isAiGenerated"):
            return None

        user = str(hit.get("user") or "").strip() or "Unknown"
        user_id = hit.get("user_id")

        return Candidate(
            url=url,
            license=LicenseInfo(
                name=PIXABAY_LICENSE,
                author=user,
                source=self.name,
                source_url=(
                    f"https://pixabay.com/users/{user}-{user_id}/"
                    if user_id
                    else str(hit.get("pageURL") or "")
                ),
                requires_attribution=False,
                allows_commercial=True,
            ),
            # The original's dimensions, not the download's: this is what the
            # resolution preference reasons about.
            width=int(hit.get("imageWidth") or 0),
            height=int(hit.get("imageHeight") or 0),
            kind=AssetKind.IMAGE,
            title=str(hit.get("pageURL") or ""),
            tags=tuple(
                tag.strip()
                for tag in str(hit.get("tags") or "").split(",")
                if tag.strip()
            )[:12],
            preview_url=str(hit.get("previewURL") or ""),
        )

    # --- video clips ---

    def _search_videos(self, request: SearchRequest) -> tuple[Candidate, ...]:
        payload, _ = self._get("/videos/", self._common_parameters(request))

        candidates = [
            candidate
            for hit in payload.get("hits") or []
            if (candidate := self._video_to_candidate(hit)) is not None
        ]
        return tuple(candidates[: request.limit])

    def _video_to_candidate(self, hit: dict[str, Any]) -> Candidate | None:
        """Pick the smallest rendition that covers the output height."""
        renditions = hit.get("videos") or {}
        if not isinstance(renditions, dict):
            return None

        chosen: dict[str, Any] | None = None
        for name in _VIDEO_RENDITIONS:
            entry = renditions.get(name)
            if not isinstance(entry, dict) or not entry.get("url"):
                continue
            chosen = entry
            if int(entry.get("height") or 0) >= self.output_height:
                break

        if chosen is None:
            return None

        user = str(hit.get("user") or "").strip() or "Unknown"
        user_id = hit.get("user_id")

        return Candidate(
            url=str(chosen.get("url")),
            license=LicenseInfo(
                name=PIXABAY_LICENSE,
                author=user,
                source=self.name,
                source_url=(
                    f"https://pixabay.com/users/{user}-{user_id}/"
                    if user_id
                    else str(hit.get("pageURL") or "")
                ),
                requires_attribution=False,
                allows_commercial=True,
            ),
            width=int(chosen.get("width") or 0),
            height=int(chosen.get("height") or 0),
            duration=float(hit.get("duration") or 0.0) or None,
            kind=AssetKind.VIDEO,
            title=str(hit.get("pageURL") or ""),
            tags=tuple(
                tag.strip()
                for tag in str(hit.get("tags") or "").split(",")
                if tag.strip()
            )[:12],
        )

    # --- shared ---

    def _common_parameters(self, request: SearchRequest) -> dict[str, str]:
        parameters = {
            "key": self.api_key,
            "q": truncate_query(request.query),
            "per_page": str(
                min(MAX_PER_PAGE, max(MIN_PER_PAGE, request.limit * 2))
            ),
            # Always on: a tool that drops images into a video automatically
            # should not surprise anyone.
            "safesearch": "true",
        }

        if request.language in _LANGUAGES:
            parameters["lang"] = request.language

        return parameters

    def _get(
        self, path: str, parameters: dict[str, str]
    ) -> tuple[dict[str, Any], Any]:
        url = f"{API_ROOT}{path}?{urllib.parse.urlencode(parameters)}"
        payload, limits = request_json(
            url, cache=self.cache, timeout=self.timeout, source=self.name
        )
        if limits.remaining is not None:
            self.rate_limit = limits
        return payload, limits
