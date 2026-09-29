"""How Voxframe starts other programs (D-165).

The installed Windows app runs under ``pythonw``, which has no console. Windows
gives every console program started from such a process -- FFmpeg, FFprobe --
a new console window of its own, so a render flashed one window per FFmpeg
call. Every subprocess Voxframe starts passes :data:`NO_WINDOW`, and a test
fails if one does not.
"""

from __future__ import annotations

import subprocess
import sys

__all__ = ["NO_WINDOW"]

#: ``creationflags`` for every subprocess: no console window on Windows,
#: nothing anywhere else (the flag does not exist there).
NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
