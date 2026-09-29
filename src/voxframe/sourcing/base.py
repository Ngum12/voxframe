"""The sourcing adapter interface.

An adapter turns a search query into candidate assets with full provenance. The
point of Phase 4 is that a narration fills its own library, so a user does not
have to assemble one before getting a watchable video.

Two rules shape this interface.

**No key is ever required.** The brief forbids depending on paid services, so
adapters that need an API key are optional extras and the ones that do not
(Wikimedia Commons, Openverse) are the defaults. :meth:`Adapter.available`
reports whether an adapter can run, and the registry silently skips the ones
that cannot rather than failing a render.

**Provenance is not optional.** An adapter returns
:class:`~voxframe.models.asset.LicenseInfo` alongside every candidate, and a
candidate whose license cannot be determined is dropped rather than guessed at.
Credits are a projection of recorded facts (D-012), which only works if the
facts are recorded at the point of fetch — an adapter that returns a bare URL
has already lost the information.

Adapters do not write to the library. They return :class:`Candidate` objects;
downloading, hashing, deduplicating and embedding are the fetcher's job, so an
adapter stays a thin, testable translation of one API's shape into ours.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

import structlog

from voxframe.models.asset import AssetKind, LicenseInfo

__all__ = [
    "Adapter",
    "AdapterError",
    "Candidate",
    "SearchRequest",
]

log = structlog.get_logger(__name__)


class AdapterError(RuntimeError):
    """Raised when an adapter cannot complete a search.

    Distinct from returning no results: an empty list means "nothing matched",
    which is normal and not worth interrupting a render for. This means the
    adapter itself failed, and the caller decides whether to continue with the
    others.
    """


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """What to search for.

    Attributes:
        query: The text to search. Usually the scene's own sentence, since
            full-sentence search beat extracted keywords (D-051).
        orientation: Preferred shape, or ``None`` for any. A mismatched image
            is usable — Ken Burns can crop into it — so this is a preference
            the adapter passes to the API, not a filter applied to results.
        kind: Image or video.
        limit: Maximum candidates to return.
        language: Language of the query, for APIs that accept one.
        commercial_only: Drop anything not licensed for commercial use.
    """

    query: str
    orientation: str | None = None
    kind: AssetKind = AssetKind.IMAGE
    limit: int = 10
    language: str = "en"
    commercial_only: bool = False


@dataclass(frozen=True, slots=True)
class Candidate:
    """One result from an adapter, not yet downloaded.

    Attributes:
        url: Direct link to the media file itself, not a landing page.
        license: Provenance. Mandatory — see the module docstring.
        width: Pixel width, when the API reports it. Zero when unknown.
        height: Pixel height, when known.
        duration: Seconds, for video. ``None`` for stills.
        kind: Image or video.
        title: The source's own title, kept as a tag.
        tags: Labels from the source.
        preview_url: Smaller version, used for cheap ranking before a full
            download when one is offered.
    """

    url: str
    license: LicenseInfo
    width: int = 0
    height: int = 0
    duration: float | None = None
    kind: AssetKind = AssetKind.IMAGE
    title: str = ""
    tags: tuple[str, ...] = field(default=())
    preview_url: str = ""

    @property
    def pixels(self) -> int:
        """Total pixels, for preferring larger originals. Zero when unknown."""
        return self.width * self.height


class Adapter(abc.ABC):
    """Base class for a source of licensed media.

    Subclasses implement :meth:`search` and declare :attr:`name`. Everything
    else — rate limiting, retries, download, dedupe — belongs to the fetcher,
    so an adapter is a translation layer and nothing more.
    """

    #: Short identifier, recorded as ``LicenseInfo.source`` on every asset from
    #: this adapter. Lowercase, no spaces.
    name: str = ""

    #: Whether this adapter needs an API key. Keyless adapters are the
    #: defaults; the core pipeline must work with no account anywhere.
    requires_key: bool = False

    #: Human-readable note on the source's terms, shown by ``voxframe sources``
    #: so a user can see what they are agreeing to before enabling one.
    terms: str = ""

    #: Why this source is skipped for the rest of a run, or empty while it is
    #: in use: its limit is nearly used, it refused with a 429, or it could
    #: not be reached twice in a row. The other sources carry on (D-166).
    resting: str = ""

    #: Seconds until the source's limit resets, when it said.
    rest_seconds: float | None = None

    #: What the last response said about the remaining budget, when the source
    #: reports one (a :class:`~voxframe.sourcing.http.RateLimit`).
    rate_limit: Any = None

    #: Connection failures in a row, in this run.
    unreachable: int = 0

    def rest(self, reason: str, seconds: float | None = None) -> None:
        """Stop using this source for the rest of the run."""
        if not self.resting:
            log.info("sourcing.adapter.resting", adapter=self.name, reason=reason)
        self.resting = reason
        self.rest_seconds = seconds

    @abc.abstractmethod
    def search(self, request: SearchRequest) -> tuple[Candidate, ...]:
        """Find candidates matching a request.

        Returns:
            Candidates, best first where the source ranks them. An empty tuple
            means nothing matched, which is not an error.

        Raises:
            AdapterError: If the source could not be reached or returned
                something unusable.
        """

    def available(self) -> bool:
        """Whether this adapter can run right now.

        Keyless adapters are always available. A keyed adapter reports whether
        its key is configured, so the registry can skip it silently rather than
        failing a render over an optional feature.
        """
        return not self.requires_key

    def unavailable_reason(self) -> str:
        """Why :meth:`available` is false, for a diagnostic listing."""
        if self.available():
            return ""
        return f"{self.name} needs an API key; set it in .env to enable it"

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
