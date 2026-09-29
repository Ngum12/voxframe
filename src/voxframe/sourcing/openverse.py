"""Openverse: CC-licensed images and audio, no API key required.

Openverse aggregates openly-licensed media from Flickr, Wikimedia, museums and
others, and exposes a documented API that works anonymously. That makes it the
right default for a project whose core must run without an account anywhere.

An anonymous key raises the rate limit but is not needed. Voxframe does not ask
for one: the whole point is that the tool works when you have nothing.

License filtering happens at the API rather than after the fact, so a request
does not spend its results on material the policy will reject.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import structlog

from voxframe.models.asset import AssetKind, LicenseInfo
from voxframe.sourcing.base import Adapter, AdapterError, Candidate, SearchRequest
from voxframe.sourcing.licenses import DEFAULT_POLICY, LicensePolicy, parse_license

__all__ = ["OpenverseAdapter"]

log = structlog.get_logger(__name__)

API_ROOT = "https://api.openverse.org/v1"
USER_AGENT = "voxframe/0.1 (+https://github.com/Ngum12/voxframe)"

#: Openverse caps page_size; asking for more is rejected outright.
MAX_PAGE_SIZE = 20

#: Openverse names orientations differently from our Orientation enum, and
#: rejects the request outright rather than ignoring an unknown value.
_ASPECT = {"landscape": "wide", "portrait": "tall", "square": "square"}


class OpenverseAdapter(Adapter):
    """Search Openverse for openly-licensed images."""

    name = "openverse"
    requires_key = False
    terms = (
        "Aggregates CC-licensed and public-domain media. Each result carries "
        "its own license; Voxframe records it per asset and credits it."
    )

    def __init__(
        self,
        policy: LicensePolicy = DEFAULT_POLICY,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.policy = policy
        self.timeout = timeout

    def search(self, request: SearchRequest) -> tuple[Candidate, ...]:
        """Find images matching a request.

        Raises:
            AdapterError: If Openverse is unreachable or returns a body that
                is not the JSON we expect.
        """
        if request.kind is not AssetKind.IMAGE:
            # Openverse indexes audio too, but no video, and Voxframe's video
            # needs come from adapters that actually have clips.
            return ()

        payload = self._get("/images/", self._parameters(request))
        results = payload.get("results")
        if not isinstance(results, list):
            raise AdapterError(
                f"Openverse returned no result list for {request.query!r}; "
                f"the API shape may have changed"
            )

        candidates: list[Candidate] = []
        rejected = 0

        for result in results:
            candidate = self._to_candidate(result)
            if candidate is None:
                rejected += 1
                continue
            candidates.append(candidate)

        if rejected:
            # Worth logging rather than silently dropping: a sudden rise means
            # the API changed or the policy is too narrow for this query.
            log.debug(
                "sourcing.openverse.rejected",
                query=request.query,
                rejected=rejected,
                kept=len(candidates),
            )

        return tuple(candidates[: request.limit])

    def _parameters(self, request: SearchRequest) -> dict[str, str]:
        """Query parameters, filtering by license at the API."""
        parameters = {
            "q": request.query,
            # Over-fetch a little: some results are dropped for an
            # unrecognised license or a missing URL, and a short page would
            # otherwise return fewer than asked for.
            "page_size": str(min(MAX_PAGE_SIZE, max(request.limit * 2, 5))),
            "license": ",".join(self.policy.openverse_codes()),
            # Openverse's own filter for material flagged as sensitive. On by
            # default and not exposed: a tool that drops images into a video
            # automatically should not surprise anyone.
            "mature": "false",
        }

        if aspect := _ASPECT.get(request.orientation or ""):
            parameters["aspect_ratio"] = aspect

        return parameters

    def _get(self, path: str, parameters: dict[str, str]) -> dict[str, Any]:
        """One GET against the API, with errors the caller can act on."""
        url = f"{API_ROOT}{path}?{urllib.parse.urlencode(parameters)}"
        http_request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise AdapterError(
                    "Openverse rate limit reached. It allows a modest number "
                    "of anonymous requests per hour; wait, or source fewer "
                    "scenes at a time."
                ) from exc
            raise AdapterError(f"Openverse returned HTTP {exc.code}: {exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AdapterError(
                f"Could not reach Openverse: {type(exc).__name__}: {exc}"
            ) from exc

        try:
            payload: dict[str, Any] = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AdapterError(f"Openverse returned invalid JSON: {exc}") from exc

        return payload

    def _to_candidate(self, result: dict[str, Any]) -> Candidate | None:
        """Translate one API result, or ``None`` if it is unusable.

        Dropped rather than guessed at when the license is unrecognised or the
        media URL is missing: an asset Voxframe cannot describe truthfully is
        worse than one fewer candidate.
        """
        url = result.get("url")
        if not isinstance(url, str) or not url:
            return None

        terms = parse_license(
            str(result.get("license") or ""),
            str(result.get("license_version") or ""),
        )
        if terms is None or not self.policy.permits(terms):
            return None

        creator = str(result.get("creator") or "").strip() or "Unknown"

        tags: list[str] = []
        for tag in result.get("tags") or []:
            if isinstance(tag, dict) and (name := tag.get("name")):
                tags.append(str(name))

        return Candidate(
            url=url,
            license=LicenseInfo(
                name=terms.code,
                author=creator,
                source=self.name,
                source_url=str(result.get("foreign_landing_url") or ""),
                requires_attribution=terms.requires_attribution,
                allows_commercial=terms.allows_commercial,
            ),
            width=int(result.get("width") or 0),
            height=int(result.get("height") or 0),
            kind=AssetKind.IMAGE,
            title=str(result.get("title") or ""),
            tags=tuple(tags[:12]),
            preview_url=str(result.get("thumbnail") or ""),
        )
