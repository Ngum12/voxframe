"""Checking for a newer release, only when asked (D-155).

The owner's decision: a button, and no automatic network calls. Nothing in the
app calls this except the "Check for updates" button; it asks GitHub for the
latest published release once, and says what it found.

No identifying information is sent: the request carries only a generic user
agent, as every web page load does.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from voxframe import __version__

__all__ = ["RELEASES_URL", "UpdateCheck", "UpdateError", "check_for_update", "newer"]

#: Where releases are published. Read only when the person asks.
RELEASES_URL = "https://api.github.com/repos/Ngum12/voxframe/releases/latest"
RELEASES_PAGE = "https://github.com/Ngum12/voxframe/releases"

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class UpdateError(RuntimeError):
    """The check could not be done, in plain words."""


@dataclass(frozen=True)
class UpdateCheck:
    current: str
    latest: str | None
    newer: bool
    url: str


def _parts(version: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(version)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def newer(latest: str, current: str) -> bool:
    """Whether ``latest`` is a later version than ``current``."""
    latest_parts, current_parts = _parts(latest), _parts(current)
    return bool(latest_parts and current_parts and latest_parts > current_parts)


def check_for_update(current: str = __version__, timeout: float = 10.0) -> UpdateCheck:
    """Ask GitHub, once, for the latest release.

    Raises:
        UpdateError: The request failed; the message says so without detail
            the person cannot act on.
    """
    request = urllib.request.Request(
        RELEASES_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "voxframe-update-check"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            release = json.loads(response.read(1_000_000))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # No release has been published yet.
            return UpdateCheck(current=current, latest=None, newer=False, url=RELEASES_PAGE)
        raise UpdateError("GitHub did not answer the update check. Try again later.") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise UpdateError("Could not reach GitHub. Check your connection and try again.") from exc

    latest = str(release.get("tag_name") or "")
    url = str(release.get("html_url") or RELEASES_PAGE)
    if not url.startswith("https://github.com/"):
        url = RELEASES_PAGE
    return UpdateCheck(
        current=current, latest=latest or None, newer=newer(latest, current), url=url
    )
