"""API keys must not reach logs, errors, caches or plans.

Pixabay puts its key in the query string, so any URL that escapes into a log
line carries the secret. These tests use fake keys with the same *shape* as the
real ones, never real values.
"""

from __future__ import annotations

import pytest

from voxframe.sourcing.secrets import (
    PLACEHOLDER,
    cache_key_url,
    mask,
    redact,
    redact_mapping,
)

# allow-key-shaped-fixtures: the constants below are invented values with the
# same shape as real keys, which is the point — redaction must catch a key it
# has never seen. The repository secret scanner skips this file on this marker.

#: Shaped like the providers' keys, but invented.
FAKE_PIXABAY = "12345678-0123456789abcdef0123456789"
FAKE_PEXELS = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGhIjKl"


class TestMask:
    def test_shows_only_the_tail(self) -> None:
        masked = mask(FAKE_PEXELS)
        assert masked == f"...{FAKE_PEXELS[-4:]}"
        assert FAKE_PEXELS not in masked

    def test_empty_is_reported_as_unset(self) -> None:
        assert mask("") == "(not set)"

    def test_short_secret_is_not_partially_revealed(self) -> None:
        """A short key would be mostly recoverable from its tail."""
        assert mask("abcd") == "(set)"


class TestRedact:
    def test_pixabay_key_in_a_query_string(self) -> None:
        url = f"https://pixabay.com/api/?key={FAKE_PIXABAY}&q=winter"
        redacted = redact(url)

        assert FAKE_PIXABAY not in redacted
        assert "q=winter" in redacted

    def test_bare_key_outside_a_url(self) -> None:
        """An Authorization header echoed into an exception message."""
        assert FAKE_PEXELS not in redact(f"request failed with {FAKE_PEXELS}")

    def test_pixabay_shaped_key_outside_a_url(self) -> None:
        assert FAKE_PIXABAY not in redact(f"key was {FAKE_PIXABAY} oops")

    def test_a_url_without_secrets_is_unchanged(self) -> None:
        url = "https://api.pexels.com/v1/search?query=winter&per_page=5"
        assert redact(url) == url

    def test_empty_text(self) -> None:
        assert redact("") == ""

    @pytest.mark.parametrize(
        "parameter", ["key", "api_key", "apikey", "token", "access_token"]
    )
    def test_every_secret_parameter_name(self, parameter: str) -> None:
        url = f"https://example.invalid/?{parameter}={FAKE_PEXELS}&q=x"
        assert FAKE_PEXELS not in redact(url)

    def test_secret_parameter_matching_is_case_insensitive(self) -> None:
        url = f"https://example.invalid/?KEY={FAKE_PEXELS}"
        assert FAKE_PEXELS not in redact(url)

    def test_redaction_is_visible(self) -> None:
        """A redacted value must look redacted, not merely absent."""
        url = f"https://pixabay.com/api/?key={FAKE_PIXABAY}"
        assert "REDACTED" in redact(url)

    def test_a_key_inside_a_longer_message_is_removed(self) -> None:
        message = (
            f"Could not reach pixabay: HTTPError for "
            f"https://pixabay.com/api/?key={FAKE_PIXABAY}&q=snow — timed out"
        )
        redacted = redact(message)

        assert FAKE_PIXABAY not in redacted
        assert "timed out" in redacted


class TestCacheKey:
    def test_secret_is_removed_entirely(self) -> None:
        """Not replaced with a placeholder: the key must not shape the cache."""
        url = f"https://pixabay.com/api/?key={FAKE_PIXABAY}&q=winter"
        key = cache_key_url(url)

        assert FAKE_PIXABAY not in key
        assert PLACEHOLDER not in key
        assert "q=winter" in key

    def test_two_keys_share_one_entry(self) -> None:
        """A rotation must not invalidate a 24-hour cache (D-080)."""
        first = cache_key_url(f"https://pixabay.com/api/?key={FAKE_PIXABAY}&q=x")
        second = cache_key_url("https://pixabay.com/api/?key=99999999-different&q=x")

        assert first == second

    def test_parameter_order_does_not_split_the_cache(self) -> None:
        first = cache_key_url("https://example.invalid/?a=1&b=2")
        second = cache_key_url("https://example.invalid/?b=2&a=1")

        assert first == second

    def test_different_queries_differ(self) -> None:
        first = cache_key_url(f"https://pixabay.com/api/?key={FAKE_PIXABAY}&q=snow")
        second = cache_key_url(f"https://pixabay.com/api/?key={FAKE_PIXABAY}&q=fire")

        assert first != second


class TestRedactMapping:
    def test_authorization_header_is_removed(self) -> None:
        """Pexels sends the key as the whole header value."""
        headers = redact_mapping({"Authorization": FAKE_PEXELS, "Accept": "json"})

        assert headers["Authorization"] == PLACEHOLDER
        assert headers["Accept"] == "json"

    def test_case_insensitive_header_name(self) -> None:
        assert redact_mapping({"authorization": FAKE_PEXELS})["authorization"] == (
            PLACEHOLDER
        )

    def test_harmless_headers_survive(self) -> None:
        headers = redact_mapping({"User-Agent": "voxframe/0.1"})
        assert headers["User-Agent"] == "voxframe/0.1"
