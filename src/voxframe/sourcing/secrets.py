"""Keep API keys out of logs, errors, caches and plans.

Pixabay puts its key in the URL query string, so any URL that reaches a log
line, an exception message, a cache key or a scene plan carries the secret with
it. That is the failure this module exists to prevent, and it has to be applied
at every boundary rather than remembered at each call site.

Two rules:

**Redact by pattern, not by lookup.** :func:`redact` strips known secret
parameters from a URL whether or not the value matches a configured key, so a
key from a different environment, a rotated key, or one typed into a test is
caught the same way. Matching against the loaded settings would miss exactly
the cases where a mistake is most likely.

**Strip the secret before caching, not after.** A cache key built from a URL
containing the key would store the secret on disk and split the cache whenever
it rotated. :func:`cache_key_url` removes it, so two requests that differ only
by key share an entry — which is also what makes the 24-hour Pixabay cache
(D-081) behave sensibly across a rotation.
"""

from __future__ import annotations

import re
import urllib.parse

__all__ = [
    "SECRET_PARAMETERS",
    "cache_key_url",
    "mask",
    "redact",
    "redact_mapping",
]

#: Query parameters whose values are secrets. Lowercase; matching is
#: case-insensitive because APIs are inconsistent about it.
SECRET_PARAMETERS = frozenset(
    {
        "key",
        "api_key",
        "apikey",
        "access_key",
        "client_id",
        "client_secret",
        "token",
        "access_token",
        "auth",
        "authorization",
    }
)

#: What a redacted value is replaced with. Distinctive so it is obvious in a
#: log that redaction happened, rather than looking like a missing value.
PLACEHOLDER = "***REDACTED***"

#: Bare secrets that may appear outside a URL — an Authorization header echoed
#: into an exception, for instance. Long alphanumeric runs with the shape of an
#: API key.
_BARE_SECRET = re.compile(r"\b[A-Za-z0-9]{28,}\b")

#: Pixabay keys look like "12345678-abc123..." which the rule above misses
#: because of the hyphen.
_PIXABAY_SHAPED = re.compile(r"\b\d{6,10}-[0-9a-f]{20,}\b")


def mask(secret: str, visible: int = 4) -> str:
    """A safe rendering of a secret, for confirming configuration.

    Shows only the last few characters, so a user can tell *which* key is
    loaded without the value being recoverable from the output.

    Returns:
        ``"...wXyZ"`` style. An empty or very short secret renders as
        ``"(not set)"`` rather than exposing the whole of it.
    """
    if not secret:
        return "(not set)"
    if len(secret) <= visible * 2:
        return "(set)"
    return f"...{secret[-visible:]}"


def redact(text: str) -> str:
    """Remove secrets from arbitrary text before it is logged or stored.

    Handles URLs with secret query parameters, and bare key-shaped tokens
    appearing anywhere in the string.

    Returns:
        The text with every recognised secret replaced by
        :data:`PLACEHOLDER`.
    """
    if not text:
        return text

    redacted = _redact_urls(text)

    # Catch a bare key outside a URL, e.g. echoed from a header.
    redacted = _PIXABAY_SHAPED.sub(PLACEHOLDER, redacted)
    return _BARE_SECRET.sub(PLACEHOLDER, redacted)


def _redact_urls(text: str) -> str:
    """Replace secret query parameters inside any URLs found in ``text``."""

    def replace(match: re.Match[str]) -> str:
        return _redact_one_url(match.group(0))

    return re.sub(r"https?://\S+", replace, text)


def _redact_one_url(url: str) -> str:
    """Strip secret query parameters from one URL, keeping it readable."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return PLACEHOLDER

    if not parts.query:
        return url

    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    cleaned = [
        (name, PLACEHOLDER if name.lower() in SECRET_PARAMETERS else value)
        for name, value in pairs
    ]

    return urllib.parse.urlunsplit(
        parts._replace(query=urllib.parse.urlencode(cleaned))
    )


def cache_key_url(url: str) -> str:
    """A cache key for a URL, with secret parameters removed entirely.

    Removed rather than replaced with a placeholder: two requests differing
    only by API key are the same request, and should share a cache entry across
    a key rotation.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url

    pairs = [
        (name, value)
        for name, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if name.lower() not in SECRET_PARAMETERS
    ]
    # Sorted so parameter order cannot split the cache.
    pairs.sort()

    return urllib.parse.urlunsplit(
        parts._replace(query=urllib.parse.urlencode(pairs), fragment="")
    )


def redact_mapping(mapping: dict[str, str]) -> dict[str, str]:
    """Redact secret values in a header or parameter mapping.

    Used before logging request metadata. ``Authorization`` carries the Pexels
    key directly, so the whole value goes rather than being pattern-matched.
    """
    return {
        name: (
            PLACEHOLDER
            if name.lower() in SECRET_PARAMETERS or name.lower() == "authorization"
            else redact(value)
        )
        for name, value in mapping.items()
    }
