"""Small things the app does on the person's own desktop (D-156).

Opening a folder in the file manager, and asking for a folder with the
operating system's own dialog. Both happen on the machine the server runs on,
which for Voxframe is always the person's own: the server is local-only.

The folder dialog is how the library is moved without the browser ever sending
a path (D-115): the person picks a folder in a native window, and the server
reads the answer from that window, not from a request.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from voxframe.processes import NO_WINDOW

__all__ = ["DesktopUnavailable", "choose_folder", "open_folder"]


class DesktopUnavailable(RuntimeError):
    """This machine cannot do that from here, in plain words."""


def open_folder(path: Path) -> None:
    """Show a folder in the file manager, creating it if it does not exist yet."""
    path.mkdir(parents=True, exist_ok=True)
    platform: str = sys.platform
    try:
        if platform == "win32":
            # Windows only; other platforms' type stubs lack it.
            os.startfile(path)  # type: ignore[attr-defined,unused-ignore]
        elif platform == "darwin":
            subprocess.run(["open", str(path)], check=True, creationflags=NO_WINDOW)
        else:
            subprocess.run(["xdg-open", str(path)], check=True, creationflags=NO_WINDOW)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DesktopUnavailable(f"The folder could not be opened: {path}") from exc


def choose_folder(title: str, start: Path | None = None) -> Path | None:
    """Ask for a folder with the system's dialog. ``None`` if cancelled.

    Run on its own thread with its own hidden Tk root, so it neither blocks
    the server's event loop nor needs the main thread.

    Raises:
        DesktopUnavailable: No dialog can be shown here (no Tk, or a platform
            where Tk must own the main thread).
    """
    result: dict[str, str] = {}
    failure: list[BaseException] = []

    def ask() -> None:
        try:
            import tkinter
            from tkinter import filedialog

            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            chosen = filedialog.askdirectory(
                parent=root, title=title, initialdir=str(start or Path.home()), mustexist=False
            )
            root.destroy()
            result["path"] = chosen
        except Exception as exc:
            failure.append(exc)

    worker = threading.Thread(target=ask, name="voxframe-folder-dialog", daemon=True)
    worker.start()
    worker.join()
    if failure:
        raise DesktopUnavailable(
            "A folder window cannot be opened here. Set VOXFRAME_LIBRARY_PATH instead."
        ) from failure[0]
    chosen = result.get("path")
    return Path(str(chosen)) if chosen else None
