"""The Voxframe app launcher (D-158)."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from types import SimpleNamespace

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


def test_only_one_app_at_a_time(tmp_path: Path) -> None:
    """Two servers sharing a jobs folder would mark each other's renders
    interrupted (D-129)."""
    with launcher._single_instance(tmp_path) as first:
        with launcher._single_instance(tmp_path) as second:
            assert first is True
            assert second is False
    with launcher._single_instance(tmp_path) as again:
        assert again is True


def test_it_starts_serves_and_quits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(launcher, "_log_to_file", lambda: tmp_path / "log")
    monkeypatch.setenv("VOXFRAME_OUTPUT_PATH", str(tmp_path / "out"))
    monkeypatch.setenv("VOXFRAME_LIBRARY_PATH", str(tmp_path / "library"))
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    seen: dict[str, object] = {}

    def window(url: str, on_quit, wakes) -> None:  # type: ignore[no-untyped-def]
        base = url.split("/?", 1)[0]
        seen["health"] = json.loads(urllib.request.urlopen(f"{base}/api/health").read())
        seen["marker"] = launcher.Marker.load(tmp_path / "data")
        on_quit()

    monkeypatch.setattr(launcher, "_window", window)

    assert launcher.main() == 0
    assert seen["health"] == {"status": "ok", "app": "voxframe"}
    assert opened and "token=" in opened[0], "the browser opens at the app, with its session"
    marker = seen["marker"]
    assert isinstance(marker, launcher.Marker) and marker.pid == os.getpid()
    assert launcher.Marker.load(tmp_path / "data") is None, "Quit clears the marker"


# --- a second launch, and leftovers (D-164) -----------------------------------------


def _running(folder: Path) -> tuple[launcher.WakeListener, list[str]]:
    """A running app's side of the wake channel."""
    woken: list[str] = []
    listener = launcher.WakeListener(lambda: woken.append("browser")).start()
    launcher.Marker(os.getpid(), listener.port, listener.key).save(folder)
    return listener, woken


def test_a_second_launch_opens_the_running_app(tmp_path: Path) -> None:
    """v0.1.0 said "already open, use its window" -- of a window that was not there."""
    listener, woken = _running(tmp_path)
    try:
        start_again = launcher.reach_running_app(tmp_path, grace=0)
    finally:
        listener.stop()

    assert start_again is False
    assert woken == ["browser"]


def test_the_wake_channel_wants_its_key(tmp_path: Path) -> None:
    listener, woken = _running(tmp_path)
    try:
        answered = launcher.wake(launcher.Marker(os.getpid(), listener.port, "not-the-key"))
    finally:
        listener.stop()

    assert answered is False
    assert woken == []


def test_the_marker_holds_no_session_token(tmp_path: Path) -> None:
    """The token is never written to disk (D-115); the wake key opens nothing."""
    listener, _ = _running(tmp_path)
    listener.stop()

    saved = json.loads((tmp_path / launcher.MARKER).read_text(encoding="utf-8"))

    assert set(saved) == {"pid", "wake_port", "wake_key"}


def test_a_second_launch_through_main_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_log_to_file", lambda: tmp_path / "log")

    def run(*args: object) -> int:
        pytest.fail("started a second server")

    monkeypatch.setattr(launcher, "_run", run)
    listener, woken = _running(tmp_path)
    try:
        with launcher._single_instance(tmp_path) as held:
            assert held
            assert launcher.main() == 0
    finally:
        listener.stop()

    assert woken == ["browser"]


def test_a_leftover_marker_is_not_a_running_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock is free, so whatever wrote the marker has ended: start normally."""
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_log_to_file", lambda: tmp_path / "log")
    launcher.Marker(999_999, 1, "old").save(tmp_path)
    started: list[bool] = []

    def run(folder: Path, log: Path) -> int:
        started.append(launcher.Marker.load(folder) is None)
        return 0

    monkeypatch.setattr(launcher, "_run", run)

    assert launcher.main() == 0
    assert started == [True], "the stale marker should be cleared, then the app started"


def test_a_copy_that_does_not_answer_can_be_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher.Marker(os.getpid(), _closed_port(), "key").save(tmp_path)
    stopped: list[int] = []
    monkeypatch.setattr(launcher, "_terminate", stopped.append)

    declined = launcher.reach_running_app(tmp_path, ask=lambda *a: False, grace=0)
    accepted = launcher.reach_running_app(tmp_path, ask=lambda *a: True, grace=0)

    assert declined is False and accepted is True
    assert stopped == [os.getpid()]


def test_nothing_to_stop_is_said_plainly(tmp_path: Path) -> None:
    told: list[str] = []

    def ask(*args: object) -> bool:
        pytest.fail("offered to stop nothing")

    start_again = launcher.reach_running_app(
        tmp_path, ask=ask, tell=lambda title, text: told.append(text), grace=0
    )

    assert start_again is False
    assert told and "not answering" in told[0]


def _closed_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_tk_is_pointed_at_the_installers_libraries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v0.1.0's installer had no Tcl/Tk files, so its window never opened."""
    pkgs = tmp_path / "pkgs"
    for folder in ("tcl8.6", "tk8.6"):
        (pkgs / "lib" / folder).mkdir(parents=True)
    spec = SimpleNamespace(origin=str(pkgs / "_tkinter.pyd"))
    monkeypatch.setattr(launcher.importlib.util, "find_spec", lambda name: spec)
    # Set, empty, rather than deleted: pytest restores only what it recorded.
    monkeypatch.setenv("TCL_LIBRARY", "")
    monkeypatch.setenv("TK_LIBRARY", "set by the person")

    launcher.prepare_tk()

    assert os.environ["TCL_LIBRARY"] == str(pkgs / "lib" / "tcl8.6")
    assert os.environ["TK_LIBRARY"] == "set by the person"


def test_this_python_can_open_the_window() -> None:
    """Where there is a display: the same check the installers run on GitHub."""
    from voxframe.selfcheck import window

    try:
        import tkinter

        tkinter.Tk().destroy()
    except Exception as exc:  # a machine without a display
        pytest.skip(f"no display: {exc}")

    assert window().startswith("Tk ")
