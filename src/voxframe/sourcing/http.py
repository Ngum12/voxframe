"""Shared HTTP for sourcing adapters: redaction, caching, rate limits.

Three concerns that every adapter has and none should reimplement.

**Redaction.** Pixabay puts its key in the query string, so a URL reaching a log
line or an exception carries the secret. Every request here logs and raises
through :mod:`voxframe.sourcing.secrets`, which means an adapter cannot leak a
key by forgetting to.

**Caching.** Pixabay's terms require responses to be cached for 24 hours
(D-081). The cache is content-addressed on the URL *without* its secret
parameters, so two requests differing only by key share an entry and a rotation
does not invalidate the cache.

**Rate limits.** Both APIs publish their remaining budget in response headers.
Reading them and pausing before exhaustion is better than discovering the limit
through a 429 partway through a render.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from voxframe.sourcing.base import AdapterError
from voxframe.sourcing.secrets import cache_key_url, redact, redact_mapping

__all__ = ["CACHE_TTL_SECONDS", "RateLimit", "RequestCache", "request_json"]

log = structlog.get_logger(__name__)

USER_AGENT = "voxframe/0.1 (+https://github.com/Ngum12/voxframe)"

#: How long a cached API response stays valid.
#:
#: 24 hours because Pixabay's terms require it. Applied to every adapter
#: rather than only that one: re-running a render should not re-query anybody,
#: and a shorter TTL for the others would buy nothing.
CACHE_TTL_SECONDS = 24 * 60 * 60

#: Pause when fewer than this many requests remain in the window, rather than
#: running to zero and taking a 429 mid-render.
RATE_LIMIT_HEADROOM = 3


@dataclass
class RateLimit:
    """What an API's headers say about the remaining budget."""

    limit: int | None = None
    remaining: int | None = None
    reset_seconds: float | None = None

    @classmethod
    def from_headers(cls, headers: Any) -> RateLimit:
        """Read the limit headers both providers use.

        Pexels and Pixabay spell them identically apart from case, which
        ``email.message.Message`` already handles case-insensitively.
        """

        def integer(name: str) -> int | None:
            raw = headers.get(name)
            try:
                return int(raw) if raw is not None else None
            except (TypeError, ValueError):
                return None

        reset = integer("X-RateLimit-Reset")

        # Pexels reports an absolute epoch; Pixabay reports seconds remaining.
        # A value far in the future is an epoch, which needs converting.
        if reset is not None and reset > 10_000_000:
            reset = max(0, reset - int(time.time()))

        return cls(
            limit=integer("X-RateLimit-Limit"),
            remaining=integer("X-RateLimit-Remaining"),
            reset_seconds=float(reset) if reset is not None else None,
        )

    @property
    def is_nearly_exhausted(self) -> bool:
        return self.remaining is not None and self.remaining <= RATE_LIMIT_HEADROOM

    def describe(self) -> str:
        if self.remaining is None:
            return "unknown"
        total = f"/{self.limit}" if self.limit else ""
        return f"{self.remaining}{total} remaining"


class RequestCache:
    """A 24-hour on-disk cache of API responses.

    Keyed on the URL with secret parameters stripped, so the key never contains
    an API key and survives a rotation.
    """

    def __init__(self, directory: Path, ttl: float = CACHE_TTL_SECONDS) -> None:
        self.directory = directory
        self.ttl = ttl

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(cache_key_url(url).encode()).hexdigest()[:24]
        return self.directory / f"{digest}.json"

    def get(self, url: str) -> dict[str, Any] | None:
        """A cached response, or ``None`` when absent or stale."""
        path = self._path(url)
        if not path.is_file():
            return None

        if time.time() - path.stat().st_mtime > self.ttl:
            return None

        try:
            payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        return payload

    def put(self, url: str, payload: dict[str, Any]) -> None:
        """Store a response. Failures are ignored: a cache miss is survivable."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._path(url).write_text(
                json.dumps(payload), encoding="utf-8", newline="\n"
            )
        except OSError as exc:
            log.debug("sourcing.cache.write_failed", error=str(exc))


def request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    cache: RequestCache | None = None,
    timeout: float = 30.0,
    source: str = "",
) -> tuple[dict[str, Any], RateLimit]:
    """GET a JSON API response, cached and with secrets redacted.

    Args:
        url: Full request URL, which may contain a secret query parameter.
        headers: Extra headers. ``Authorization`` is redacted before logging.
        cache: Where to read and store the response.
        timeout: Seconds.
        source: Adapter name, for log lines.

    Returns:
        ``(payload, rate_limit)``. A cached hit returns an empty
        :class:`RateLimit`, since no request was made.

    Raises:
        AdapterError: On any failure, with the URL redacted. The message is
            safe to show a user and safe to log.
    """
    if cache is not None and (cached := cache.get(url)) is not None:
        log.debug("sourcing.cache.hit", source=source, url=redact(url))
        return cached, RateLimit()

    all_headers = {"User-Agent": USER_AGENT, **(headers or {})}

    log.debug(
        "sourcing.request",
        source=source,
        url=redact(url),
        headers=redact_mapping(all_headers),
    )

    request = urllib.request.Request(url, headers=all_headers)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            limits = RateLimit.from_headers(response.headers)
    except urllib.error.HTTPError as exc:
        limits = RateLimit.from_headers(exc.headers)
        raise _http_error(exc, source, limits) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AdapterError(
            f"Could not reach {source or 'the source'}: "
            f"{type(exc).__name__}: {redact(str(exc))}"
        ) from exc

    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"{source} returned invalid JSON: {exc}") from exc

    if cache is not None:
        cache.put(url, payload)

    if limits.is_nearly_exhausted:
        log.warning(
            "sourcing.rate_limit.low",
            source=source,
            remaining=limits.remaining,
            reset_seconds=limits.reset_seconds,
        )

    return payload, limits


def _http_error(
    exc: urllib.error.HTTPError, source: str, limits: RateLimit
) -> AdapterError:
    """Turn an HTTP failure into a message a user can act on."""
    if exc.code == 429:
        wait = limits.reset_seconds
        when = f" Try again in about {int(wait)}s." if wait else ""
        return AdapterError(
            f"{source} rate limit reached ({limits.describe()}).{when} "
            f"Source fewer scenes at a time, or wait for the window to reset."
        )

    if exc.code in {401, 403}:
        return AdapterError(
            f"{source} rejected the request (HTTP {exc.code}). The API key may "
            f"be missing, wrong, or revoked. Check it in .env — Voxframe never "
            f"prints key values."
        )

    return AdapterError(f"{source} returned HTTP {exc.code}: {exc.reason}")
