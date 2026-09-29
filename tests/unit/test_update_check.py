"""Checking for updates, only when asked (D-155)."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from voxframe import updates


class _Response(io.BytesIO):
    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _answer(monkeypatch: pytest.MonkeyPatch, payload: object = None, error: Exception | None = None) -> list[str]:
    calls: list[str] = []

    def urlopen(request, timeout: float = 0):  # type: ignore[no-untyped-def]
        calls.append(request.full_url)
        if error is not None:
            raise error
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    return calls


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [("v0.2.0", "0.1.0", True), ("v0.1.0", "0.1.0", False), ("0.1.1", "0.2.0", False),
     ("v1.0.0", "0.9.9", True), ("nonsense", "0.1.0", False)],
)
def test_versions_compare_as_numbers(latest: str, current: str, expected: bool) -> None:
    assert updates.newer(latest, current) is expected


def test_a_newer_release_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _answer(monkeypatch, {"tag_name": "v0.2.0", "html_url": "https://github.com/Ngum12/voxframe/releases/tag/v0.2.0"})

    result = updates.check_for_update("0.1.0")

    assert result.newer and result.latest == "v0.2.0"
    assert calls == [updates.RELEASES_URL]


def test_no_release_yet_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(monkeypatch, error=urllib.error.HTTPError(updates.RELEASES_URL, 404, "Not Found", {}, None))  # type: ignore[arg-type]

    result = updates.check_for_update("0.1.0")

    assert result.latest is None and not result.newer


def test_a_link_elsewhere_is_not_passed_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(monkeypatch, {"tag_name": "v9.0.0", "html_url": "https://example.com/evil"})

    assert updates.check_for_update("0.1.0").url == updates.RELEASES_PAGE


def test_offline_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(monkeypatch, error=urllib.error.URLError("down"))

    with pytest.raises(updates.UpdateError, match="connection"):
        updates.check_for_update("0.1.0")


def test_nothing_checks_for_updates_on_its_own() -> None:
    """The owner's decision: a button only. The one caller is the route the
    button calls, and nothing in the web app calls that route but the button."""
    root = Path(__file__).resolve().parents[2]
    callers = [
        path.relative_to(root).as_posix()
        for path in (root / "src" / "voxframe").rglob("*.py")
        if "check_for_update" in path.read_text(encoding="utf-8") and path.name != "updates.py"
    ]
    assert callers == ["src/voxframe/api/app.py"]
    frontend = [
        path.relative_to(root).as_posix()
        for path in (root / "web" / "src").rglob("*.ts*")
        if "/api/updates/check" in path.read_text(encoding="utf-8")
    ]
    assert frontend == ["web/src/api.ts"]
