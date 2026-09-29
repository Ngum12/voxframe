"""The Voxframe app launcher (D-158)."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("uvicorn", reason="web extra not installed")

from voxframe import launcher
from voxframe.config import paths


def test_the_installers_ffmpeg_is_found(tmp_path: Path) -> None:
    python = tmp_path / "Voxframe" / "Python" / "pythonw.exe"
    ffmpeg = tmp_path / "Voxframe" / "ffmpeg"
    python.parent.mkdir(parents=True)
    ffmpeg.mkdir()
    python.write_bytes(b"")
    (ffmpeg / ("ffmpeg.exe" if launcher._PLATFORM == "win32" else "ffmpeg")).write_bytes(b"")

    assert launcher.bundled_ffmpeg(python) == ffmpeg.resolve()


def test_the_mac_apps_ffmpeg_is_found(tmp_path: Path) -> None:
    resources = tmp_path / "Voxframe.app" / "Contents" / "Resources"
    python = resources / "python" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (resources / "ffmpeg").mkdir()
    (resources / "ffmpeg" / ("ffmpeg.exe" if launcher._PLATFORM == "win32" else "ffmpeg")).write_bytes(b"")

    assert launcher.bundled_ffmpeg(python) == (resources / "ffmpeg").resolve()


def test_no_installer_no_bundled_ffmpeg(tmp_path: Path) -> None:
    assert launcher.bundled_ffmpeg(tmp_path / "bin" / "python") is None


def test_only_one_app_at_a_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two servers sharing a jobs folder would mark each other's renders
    interrupted (D-129)."""
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)

    with launcher._single_instance() as first:
        with launcher._single_instance() as second:
            assert first is True
            assert second is False
    with launcher._single_instance() as again:
        assert again is True


def test_it_starts_serves_and_quits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(launcher, "_log_to_file", lambda: tmp_path / "log")
    monkeypatch.setenv("VOXFRAME_OUTPUT_PATH", str(tmp_path / "out"))
    monkeypatch.setenv("VOXFRAME_LIBRARY_PATH", str(tmp_path / "library"))
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    seen: dict[str, object] = {}

    def window(url: str, on_quit) -> None:  # type: ignore[no-untyped-def]
        base = url.split("/?", 1)[0]
        seen["health"] = json.loads(urllib.request.urlopen(f"{base}/api/health").read())
        on_quit()

    monkeypatch.setattr(launcher, "_window", window)

    assert launcher.main() == 0
    assert seen["health"] == {"status": "ok", "app": "voxframe"}
    assert opened and "token=" in opened[0], "the browser opens at the app, with its session"
