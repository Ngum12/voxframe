"""Keep useful browser state when CI fails, without changing test outcomes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    directory = os.environ.get("VOXFRAME_BROWSER_DIAGNOSTICS")
    if not directory or report.when != "call" or not report.failed:
        return
    page = item.funcargs.get("page") or item.funcargs.get("made")
    if page is None:
        return
    root = Path(directory)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    name = hashlib.sha256(item.nodeid.encode()).hexdigest()[:16]
    details = {"test": item.nodeid}
    try:
        details["media"] = page.evaluate("""() => [...document.querySelectorAll('video,audio')].map(media => ({
            tag: media.tagName, label: media.getAttribute('aria-label'),
            paused: media.paused, ended: media.ended, muted: media.muted,
            currentTime: media.currentTime, duration: Number.isFinite(media.duration) ? media.duration : null,
            readyState: media.readyState, networkState: media.networkState,
            connected: media.isConnected, errorCode: media.error?.code ?? null
        }))""")
    except Exception as exc:
        details["state_capture_error"] = type(exc).__name__
    try:
        (root / f"{name}.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
    except OSError:
        pass
    try:
        page.screenshot(path=str(root / f"{name}.png"), full_page=True, timeout=5000)
    except Exception:
        pass  # Diagnostics must never replace the original failure.
