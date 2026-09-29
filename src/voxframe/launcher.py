"""The Voxframe app: what runs when someone double-clicks it (D-158).

Starts the local server, opens the browser at it, and shows a small window
saying Voxframe is running, with **Open in browser** and **Quit**. Nothing to
type, no terminal. The same server as ``voxframe web``: loopback only, a fresh
session token, the same security (D-115).

- **The bundled FFmpeg** is put first on this process's ``PATH`` when the
  installer shipped one, so the app does not depend on the person having
  installed FFmpeg.
- **One at a time.** Two servers sharing one jobs folder would each mark the
  other's renders as interrupted (D-129), so a second launch says Voxframe is
  already running and points at its window instead of starting.
- **Quit** stops the server; a render still going is marked interrupted and can
  be resumed next time (D-129).
- **Logs** go to a file in the data folder, since there is no terminal to show
  them in.

Also installed for ``pip`` users as the ``voxframe-app`` command.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import IO, Any

__all__ = ["bundled_ffmpeg", "main"]

_PLATFORM: str = sys.platform


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
def _single_instance() -> Any:
    """Hold a lock for as long as this app runs; yield False if another has it."""
    from voxframe.config.paths import data_dir

    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    handle = (folder / "voxframe.lock").open("a+")
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


def _message(title: str, text: str) -> None:
    """Show a short message box, or print it if there is no window system."""
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showinfo(title, text, parent=root)
        root.destroy()
    except Exception:  # no window system: say it on the console
        print(f"{title}: {text}")


def _window(url: str, on_quit: Any) -> None:
    """The small "Voxframe is running" window. Blocks until it is closed."""
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
        text="It opens in your web browser. Keep this window open while you use it.",
        wraplength=320,
    ).grid(column=0, row=1, columnspan=2, sticky="w", pady=(6, 14))
    ttk.Button(frame, text="Open in browser", command=lambda: webbrowser.open(url)).grid(
        column=0, row=2, sticky="w"
    )

    def quit_app() -> None:
        on_quit()
        root.destroy()

    ttk.Button(frame, text="Quit", command=quit_app).grid(column=1, row=2, sticky="e")
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.mainloop()


def main() -> int:
    """Run the app until the person quits."""
    _use_bundled_ffmpeg()
    log_file = _log_to_file()

    with _single_instance() as first:
        if not first:
            _message(
                "Voxframe is already running",
                "Voxframe is already open. Use its window to open it in your browser.",
            )
            return 0

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
        import time
        import urllib.request

        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://{handle.host}:{handle.port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        webbrowser.open(handle.url)

        def stop() -> None:
            server.should_exit = True
            thread.join(timeout=15)
            handle.store.shutdown()

        try:
            _window(handle.url, stop)
        except Exception:  # no window system: run until interrupted
            print(f"Voxframe is running at {handle.url}\nPress Ctrl+C to stop. Log: {log_file}")
            try:
                thread.join()
            except KeyboardInterrupt:
                stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
