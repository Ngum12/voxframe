"""The Voxframe app: what runs when someone double-clicks it (D-158, D-164).

Starts the local server, opens the browser at it, and shows a small window
saying Voxframe is running, with **Open in browser** and **Quit**. Nothing to
type, no terminal. The same server as ``voxframe web``: loopback only, a fresh
session token, the same security (D-115).

- **The bundled FFmpeg** is put first on this process's ``PATH`` when the
  installer shipped one, so the app does not depend on the person having
  installed FFmpeg.
- **Always visible.** Closing the browser tab does not stop Voxframe, so there
  must be a window to reopen it from or quit it. If Tk cannot open one, a
  Windows message box does the same job; running with nothing visible at all
  is what v0.1.0 did when its Tcl/Tk files were missing (D-164).
- **One at a time.** Two servers sharing one jobs folder would each mark the
  other's renders as interrupted (D-129). A second launch asks the running one
  to open the browser, through a small local "wake" channel, and exits.
- **A leftover marker is not a running app.** The lock is held by the
  operating system for as long as the process lives, so a marker file with a
  free lock is stale and is cleared. A running copy that does not answer is
  offered to be stopped.
- **Quit** stops the server; a render still going is marked interrupted and can
  be resumed next time (D-129).
- **Logs** go to a file in the data folder, since there is no terminal to show
  them in.

The wake channel carries no access to the app. The session token is never
written to disk (D-115): the marker holds only a port and a separate random
key, and the most that key can do is ask the running app to open a browser tab
at its own address.

Also installed for ``pip`` users as the ``voxframe-app`` command.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import queue
import secrets
import socket
import socketserver
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import structlog

__all__ = ["bundled_ffmpeg", "main", "prepare_tk"]

log = structlog.get_logger(__name__)

_PLATFORM: str = sys.platform

#: The marker a running app writes: its process, and how to wake it.
MARKER = "voxframe.running.json"
LOCK = "voxframe.lock"

#: How long a second launch waits for a starting app to write its marker, and
#: for a running one to answer, before calling it unresponsive.
STARTING_GRACE = 30.0
WAKE_TIMEOUT = 5.0


def bundled_ffmpeg(python: Path | None = None) -> Path | None:
    """The FFmpeg folder an installer ships, if there is one.

    - Windows installer: Python in ``<app>/Python``, FFmpeg in ``<app>/ffmpeg``.
    - macOS app: Python in ``Voxframe.app/Contents/Resources/python/bin``,
      FFmpeg in ``Contents/Resources/ffmpeg``.

    Anywhere else -- a checkout, ``pip install`` -- there is none, and FFmpeg is
    found on the ``PATH`` as usual.
    """
    executable = (python or Path(sys.executable)).resolve()
    name = "ffmpeg.exe" if _PLATFORM == "win32" else "ffmpeg"
    for folder in (executable.parent.parent / "ffmpeg", executable.parents[2] / "ffmpeg"):
        if (folder / name).is_file():
            return folder
    return None


def _use_bundled_ffmpeg() -> None:
    folder = bundled_ffmpeg()
    if folder is not None:
        os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")


def prepare_tk() -> None:
    """Point Tcl and Tk at the script libraries the Windows installer ships.

    The installer puts ``_tkinter`` and the Tcl/Tk DLLs in its package folder
    with the libraries beside them in ``lib/``, where Tcl does not look on its
    own; without these variables ``Tk()`` fails (D-164). A person's own
    settings, and every other install, are left alone.
    """
    spec = importlib.util.find_spec("_tkinter")
    if spec is None or not spec.origin:
        return
    lib = Path(spec.origin).parent / "lib"
    for variable, folder in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        if (lib / folder).is_dir() and not os.environ.get(variable):
            os.environ[variable] = str(lib / folder)


def _log_to_file() -> Path:
    """Send logs to a file: an app opened by double-click has no terminal."""
    from voxframe.config.paths import data_dir
    from voxframe.logging import configure_logging

    folder = data_dir() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "voxframe.log"
    stream: IO[str] = path.open("a", encoding="utf-8", errors="replace", buffering=1)
    sys.stderr = stream
    if sys.stdout is None:  # pythonw has no console
        sys.stdout = stream
    configure_logging("info")
    return path


@contextlib.contextmanager
def _single_instance(folder: Path) -> Iterator[bool]:
    """Hold the lock for as long as this app runs; yield False if another has it.

    The operating system releases the lock when the process ends, however it
    ends, so a held lock always means a live process.
    """
    folder.mkdir(parents=True, exist_ok=True)
    handle = (folder / LOCK).open("a+")
    try:
        if _PLATFORM == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined,unused-ignore]
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined,unused-ignore]
    except OSError:
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        handle.close()


# --- the marker and the wake channel -------------------------------------------


@dataclass(frozen=True)
class Marker:
    """What a running app records so a second launch can find it."""

    pid: int
    wake_port: int
    wake_key: str

    def save(self, folder: Path) -> None:
        path = folder / MARKER
        path.write_text(
            json.dumps({"pid": self.pid, "wake_port": self.wake_port, "wake_key": self.wake_key}),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, folder: Path) -> Marker | None:
        try:
            data = json.loads((folder / MARKER).read_text(encoding="utf-8"))
            return cls(int(data["pid"]), int(data["wake_port"]), str(data["wake_key"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None


def _clear_marker(folder: Path) -> None:
    with contextlib.suppress(OSError):
        (folder / MARKER).unlink()


class WakeListener:
    """Answers a second launch: on the right key, calls ``on_wake``."""

    def __init__(self, on_wake: Callable[[], None]) -> None:
        self.key = secrets.token_urlsafe(24)
        listener = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                self.connection.settimeout(WAKE_TIMEOUT)
                try:
                    line = self.rfile.readline(200).decode("ascii", "replace").strip()
                except OSError:
                    return
                if secrets.compare_digest(line, listener.key):
                    self.wfile.write(b"ok\n")
                    on_wake()
                else:
                    self.wfile.write(b"no\n")

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port: int = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="voxframe-wake", daemon=True
        )

    def start(self) -> WakeListener:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def wake(marker: Marker, timeout: float = WAKE_TIMEOUT) -> bool:
    """Ask a running app to open the browser. True if it said it would."""
    try:
        with socket.create_connection(("127.0.0.1", marker.wake_port), timeout=timeout) as conn:
            conn.sendall(marker.wake_key.encode("ascii") + b"\n")
            conn.settimeout(timeout)
            return conn.recv(8).startswith(b"ok")
    except OSError:
        return False


# --- processes -------------------------------------------------------------------


def _process_image(pid: int) -> str | None:
    """The executable a process runs, or ``None`` if there is no such process."""
    if _PLATFORM == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        query_limited = 0x1000
        handle = kernel32.OpenProcess(query_limited, False, pid)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    try:
        return str(Path(f"/proc/{pid}/exe").readlink())
    except OSError:
        return "python"  # alive; macOS has no /proc, and the lock says it is ours


def _is_voxframe_process(pid: int) -> bool:
    """Whether a pid is a Python process, as the running app always is."""
    image = _process_image(pid)
    return image is not None and "python" in Path(image).name.lower()


def _terminate(pid: int) -> None:
    if _PLATFORM == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        terminate = 0x0001
        handle = kernel32.OpenProcess(terminate, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
    else:
        import signal

        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)


# --- messages ---------------------------------------------------------------------


def _message(title: str, text: str) -> None:
    """Show a short message, in whatever window system there is."""
    try:
        prepare_tk()
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showinfo(title, text, parent=root)
        root.destroy()
        return
    except Exception:
        pass
    if _PLATFORM == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)  # type: ignore[attr-defined,unused-ignore]
    else:
        print(f"{title}: {text}")


def _ask(title: str, text: str) -> bool:
    """A yes/no question, in whatever window system there is."""
    try:
        prepare_tk()
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        answer = bool(messagebox.askyesno(title, text, parent=root))
        root.destroy()
        return answer
    except Exception:
        pass
    if _PLATFORM == "win32":
        import ctypes

        yes = 6
        return bool(
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x04 | 0x20) == yes  # type: ignore[attr-defined,unused-ignore]
        )
    return False


# --- a second launch --------------------------------------------------------------


def reach_running_app(
    folder: Path,
    *,
    ask: Callable[[str, str], bool] = _ask,
    tell: Callable[[str, str], None] = _message,
    grace: float = STARTING_GRACE,
) -> bool:
    """Handle a launch while another copy holds the lock.

    Returns:
        True when this launch should now start the app itself (the other copy
        was stopped); False when there is nothing more to do.
    """
    deadline = time.monotonic() + grace
    marker = Marker.load(folder)
    # A copy that has just started has the lock but not yet its marker.
    while marker is None and time.monotonic() < deadline:
        time.sleep(0.5)
        marker = Marker.load(folder)

    if marker is not None and wake(marker):
        log.info("launcher.woke_running_app", pid=marker.pid)
        return False

    log.warning("launcher.running_app_not_answering", pid=marker.pid if marker else None)
    if marker is None or not _is_voxframe_process(marker.pid):
        tell(
            "Voxframe",
            "Voxframe seems to be running already but is not answering. Wait a "
            "moment and open it again; if it still does not open, restart your "
            "computer.",
        )
        return False
    if not ask(
        "Voxframe is not responding",
        "Voxframe is already running but is not answering. Stop it and start "
        "again? A video being made will be marked interrupted, and you can "
        "resume it.",
    ):
        return False
    _terminate(marker.pid)
    return True


# --- the window -------------------------------------------------------------------


def _window(url: str, on_quit: Callable[[], None], wakes: queue.Queue[None]) -> None:
    """The small "Voxframe is running" window. Blocks until it is closed."""
    prepare_tk()
    import tkinter
    from tkinter import ttk

    root = tkinter.Tk()
    root.title("Voxframe")
    root.resizable(False, False)
    try:
        icon = Path(__file__).resolve().parent / "assets" / "icon" / "voxframe.ico"
        if _PLATFORM == "win32":
            root.iconbitmap(default=str(icon))  # type: ignore[no-untyped-call]
    except Exception:  # an icon is decoration
        pass

    frame = ttk.Frame(root, padding=20)
    frame.grid()
    ttk.Label(frame, text="Voxframe is running", font=("Segoe UI", 12, "bold")).grid(
        column=0, row=0, columnspan=2, sticky="w"
    )
    ttk.Label(
        frame,
        text=(
            "It opens in your web browser. Closing the browser does not stop "
            "it: open it again from here, and use Quit when you are done."
        ),
        wraplength=320,
    ).grid(column=0, row=1, columnspan=2, sticky="w", pady=(6, 14))
    ttk.Button(frame, text="Open in browser", command=lambda: webbrowser.open(url)).grid(
        column=0, row=2, sticky="w"
    )

    def quit_app() -> None:
        on_quit()
        root.destroy()

    def show() -> None:
        webbrowser.open(url)
        root.deiconify()
        root.lift()

    def poll() -> None:
        # Tk may only be touched from its own thread; the wake listener runs
        # on another, so it leaves a note here instead.
        with contextlib.suppress(queue.Empty):
            while True:
                wakes.get_nowait()
                show()
        root.after(250, poll)

    ttk.Button(frame, text="Quit", command=quit_app).grid(column=1, row=2, sticky="e")
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.after(250, poll)
    root.mainloop()


def _message_box_loop(url: str, on_quit: Callable[[], None]) -> None:
    """The Windows fallback when Tk cannot open a window: still visible."""
    import ctypes

    ok = 1
    while True:
        answer = ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined,unused-ignore]
            None,
            "Voxframe is running.\n\nOK: open it in your browser.\nCancel: quit Voxframe.",
            "Voxframe",
            0x01 | 0x40,
        )
        if answer != ok:
            on_quit()
            return
        webbrowser.open(url)


def main() -> int:
    """Run the app until the person quits."""
    from voxframe.config.paths import data_dir

    _use_bundled_ffmpeg()
    log_file = _log_to_file()
    folder = data_dir()

    for _attempt in range(2):
        with _single_instance(folder) as first:
            if not first:
                if reach_running_app(folder):
                    time.sleep(2)  # let the stopped copy release its lock
                    continue
                return 0
            if Marker.load(folder) is not None:
                # The lock was free, so no copy is running: a marker left by
                # one that ended without cleaning up (D-164).
                log.info("launcher.stale_marker_cleared")
                _clear_marker(folder)
            return _run(folder, log_file)
    _message("Voxframe", "Voxframe could not start: another copy is still running.")
    return 1


def _run(folder: Path, log_file: Path) -> int:
    import urllib.request

    import uvicorn

    from voxframe.api.serve import build_server

    handle = build_server()
    server = uvicorn.Server(
        uvicorn.Config(
            handle.app, host=handle.host, port=handle.port,
            log_level="warning", access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, name="voxframe-server", daemon=True)
    thread.start()

    # Open the browser once the server answers, not before.
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://{handle.host}:{handle.port}/api/health", timeout=1)
            break
        except OSError:
            time.sleep(0.1)

    wakes: queue.Queue[None] = queue.Queue()
    shown = {"window": False}

    def on_wake() -> None:
        if shown["window"]:
            wakes.put(None)
        else:
            webbrowser.open(handle.url)

    listener = WakeListener(on_wake).start()
    Marker(os.getpid(), listener.port, listener.key).save(folder)
    webbrowser.open(handle.url)

    def stop() -> None:
        _clear_marker(folder)
        listener.stop()
        server.should_exit = True
        thread.join(timeout=15)
        handle.store.shutdown()

    try:
        shown["window"] = True
        _window(handle.url, stop, wakes)
    except Exception as exc:  # no Tk: a message box on Windows, else the console
        shown["window"] = False
        log.warning("launcher.no_window", error=f"{type(exc).__name__}: {exc}")
        if _PLATFORM == "win32":
            _message_box_loop(handle.url, stop)
        else:
            print(f"Voxframe is running at {handle.url}\nPress Ctrl+C to stop. Log: {log_file}")
            try:
                thread.join()
            except KeyboardInterrupt:
                stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
