"""Pexels: stock photographs and video clips. Optional, needs a free key.

Measured limits, from the response headers rather than the documentation
(D-079): 25,000 requests per month, reported per response in
``X-Ratelimit-Remaining``. The API also **requires a User-Agent** — a request
without one is refused with 403, which took a failed call to discover.

Attribution: Pexels asks that both the photographer and Pexels be credited
where the photo is used. Voxframe records the photographer's name and profile
URL per asset and writes both into the credits file (D-012).

The adapter stays off unless ``VOXFRAME_PEXELS_API_KEY`` is set, and the key is
sent in the ``Authorization`` header, which :mod:`voxframe.sourcing.http`
redacts before anything is logged.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

import structlog

from voxframe.models.asset import AssetKind, LicenseInfo
from voxframe.sourcing.base import Adapter, Candidate, SearchRequest
from voxframe.sourcing.http import RequestCache, request_json

__all__ = ["PEXELS_LICENSE", "PexelsAdapter"]

log = structlog.get_logger(__name__)

API_ROOT = "https://api.pexels.com"

#: Pexels publishes one license for all its content: free to use, modification
#: allowed, no attribution legally required but requested. Recorded by name so
#: the credits file states the actual terms rather than approximating them to a
#: Creative Commons code that does not apply.
PEXELS_LICENSE = "Pexels License"

#: Pexels caps a page at 80.
MAX_PER_PAGE = 80

#: Their orientation vocabulary matches ours for landscape/portrait but not
#: square.
_ORIENTATION = {
    "landscape": "landscape",
    "portrait": "portrait",
    "square": "square",
}

#: Locales Pexels accepts, mapped from our language codes. Anything else is
#: omitted rather than guessed, since an unknown locale is rejected outright.
_LOCALE = {"en": "en-US", "fr": "fr-FR"}


class PexelsAdapter(Adapter):
    """Search Pexels for photographs and video clips."""

    name = "pexels"
    requires_key = True
    terms = (
        "Free to use, modification allowed. Attribution is requested rather "
        "than required; Voxframe credits the photographer and Pexels."
    )

    def __init__(
        self,
        api_key: str | None = None,
        *,
        cache: RequestCache | None = None,
        timeout: float = 30.0,
        min_width: int = 1280,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.cache = cache
        self.timeout = timeout
        self.min_width = min_width

    def available(self) -> bool:
        return bool(self.api_key)

    def unavailable_reason(self) -> str:
        if self.available():
            return ""
        return (
            "pexels needs a free API key. Get one at https://www.pexels.com/api/ "
            "and set VOXFRAME_PEXELS_API_KEY in .env"
        )

    def search(self, request: SearchRequest) -> tuple[Candidate, ...]:
        """Find photographs or clips matching a request."""
        if not self.available():
            return ()

        if request.kind is AssetKind.VIDEO:
            return self._search_videos(request)
        return self._search_photos(request)

    # --- photographs ---

    def _search_photos(self, request: SearchRequest) -> tuple[Candidate, ...]:
        parameters: dict[str, str] = {
            "query": request.query,
            "per_page": str(min(MAX_PER_PAGE, max(request.limit, 5))),
        }

        if orientation := _ORIENTATION.get(request.orientation or ""):
            parameters["orientation"] = orientation
        if locale := _LOCALE.get(request.language):
            parameters["locale"] = locale

        payload, _ = self._get("/v1/search", parameters)

        candidates = [
            candidate
            for result in payload.get("photos") or []
            if (candidate := self._photo_to_candidate(result)) is not None
        ]
        return tuple(candidates[: request.limit])

    def _photo_to_candidate(self, result: dict[str, Any]) -> Candidate | None:
        """Translate one photo, or ``None`` if it is unusable."""
        sources = result.get("src") or {}
        if not isinstance(sources, dict):
            return None

        # "large2x" is about 1880px wide and is plenty for 1080p output;
        # "original" can be 6000px, which is a slow download for no visible
        # gain after the frame is cropped.
        url = sources.get("large2x") or sources.get("large") or sources.get("original")
        if not isinstance(url, str) or not url:
            return None

        width = int(result.get("width") or 0)
        height = int(result.get("height") or 0)
        if width and width < self.min_width:
            return None

        photographer = str(result.get("photographer") or "").strip() or "Unknown"

        return Candidate(
            url=url,
            license=LicenseInfo(
                name=PEXELS_LICENSE,
                author=photographer,
                source=self.name,
                # The photographer's profile, which is what the attribution
                # request asks to link.
                source_url=str(
                    result.get("photographer_url") or result.get("url") or ""
                ),
                requires_attribution=True,
                allows_commercial=True,
            ),
            width=width,
            height=height,
            kind=AssetKind.IMAGE,
            title=str(result.get("alt") or ""),
            preview_url=str(sources.get("medium") or ""),
        )

    # --- video clips ---

    def _search_videos(self, request: SearchRequest) -> tuple[Candidate, ...]:
        parameters: dict[str, str] = {
            "query": request.query,
            "per_page": str(min(MAX_PER_PAGE, max(request.limit, 5))),
        }
        if orientation := _ORIENTATION.get(request.orientation or ""):
            parameters["orientation"] = orientation
        if locale := _LOCALE.get(request.language):
            parameters["locale"] = locale

        payload, _ = self._get("/videos/search", parameters)

        candidates = [
            candidate
            for result in payload.get("videos") or []
            if (candidate := self._video_to_candidate(result)) is not None
        ]
        return tuple(candidates[: request.limit])

    def _video_to_candidate(self, result: dict[str, Any]) -> Candidate | None:
        """Pick the smallest rendition that still covers the render height.

        Deliberately not the largest: a 4K clip is hundreds of megabytes and
        looks identical once scaled to 1080p. Respecting the user's bandwidth
        matters more than a resolution nobody will see.
        """
        files = result.get("video_files") or []
        if not isinstance(files, list) or not files:
            return None

        usable = [
            entry
            for entry in files
            if isinstance(entry, dict)
            and isinstance(entry.get("link"), str)
            and int(entry.get("width") or 0) >= self.min_width
        ]
        # Nothing large enough: fall back to the biggest available rather than
        # dropping the clip, and let the resolution check downstream decide.
        pool = usable or [entry for entry in files if isinstance(entry, dict)]
        if not pool:
            return None

        chosen = min(
            pool,
            key=lambda entry: int(entry.get("width") or 0)
            if usable
            else -int(entry.get("width") or 0),
        )

        user = result.get("user") or {}
        author = str(user.get("name") or "").strip() or "Unknown"

        return Candidate(
            url=str(chosen.get("link")),
            license=LicenseInfo(
                name=PEXELS_LICENSE,
                author=author,
                source=self.name,
                source_url=str(user.get("url") or result.get("url") or ""),
                requires_attribution=True,
                allows_commercial=True,
            ),
            width=int(chosen.get("width") or 0),
            height=int(chosen.get("height") or 0),
            duration=float(result.get("duration") or 0.0) or None,
            kind=AssetKind.VIDEO,
            title=str(result.get("url") or "").rstrip("/").rsplit("/", 1)[-1],
            preview_url=str(result.get("image") or ""),
        )

    def _get(
        self, path: str, parameters: dict[str, str]
    ) -> tuple[dict[str, Any], Any]:
        url = f"{API_ROOT}{path}?{urllib.parse.urlencode(parameters)}"
        return request_json(
            url,
            headers={"Authorization": self.api_key},
            cache=self.cache,
            timeout=self.timeout,
            source=self.name,
        )
