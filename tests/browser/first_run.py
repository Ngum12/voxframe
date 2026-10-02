"""Getting a browser test to the upload screen, as a first-time user does.

A fresh machine -- a CI runner, a new contributor's -- has no models yet, so
the app opens on "Getting ready" (D-157), and on the online-search question
(D-116). Tests written where the models were already downloaded never met
the first and timed out waiting for the second (D-195). Both are answered
"Not now", which is what a person who wants to look around first does.
"""

from __future__ import annotations

from typing import Any

__all__ = ["to_upload_screen"]


def to_upload_screen(page: Any, timeout_ms: int = 30_000) -> None:
    """From a freshly loaded page, past any first-run screens, to the upload screen."""
    upload = page.get_by_text("Turn a recording into a video")
    not_now = page.get_by_role("button", name="Not now", exact=True)
    waited = 0
    while waited < timeout_ms:
        if upload.count():
            return
        if not_now.count():
            not_now.first.click(timeout=5_000)
        page.wait_for_timeout(250)
        waited += 250
    upload.wait_for(timeout=1_000)
