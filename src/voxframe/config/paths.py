"""Where Voxframe keeps things on this computer (D-156).

A source checkout keeps working as it always has: the library, cache and
videos live in folders under the directory Voxframe is started from, which is
what development and the tests expect.

An **installed** Voxframe -- from the installer, or ``pip install`` -- has no
such directory to rely on: double-clicking an app starts it wherever the
operating system chooses. It keeps things in the user's own folders instead:

========  ================================  ==========================
            Windows                            macOS / Linux
========  ================================  ==========================
data      ``%LOCALAPPDATA%\\Voxframe``       ``~/Library/Application Support/Voxframe``
                                            (Linux: ``$XDG_DATA_HOME/voxframe``)
videos    ``Videos\\Voxframe``               ``~/Movies/Voxframe`` (Linux: ``~/Videos/Voxframe``)
========  ================================  ==========================

The library, cache and models are inside the data folder. The environment
variables (``VOXFRAME_LIBRARY_PATH`` and the rest) always win, and a library
moved in Settings is remembered in the user's preferences.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "configure_model_cache",
    "data_dir",
    "default_cache_path",
    "default_library_path",
    "default_output_path",
    "is_source_checkout",
    "models_dir",
    "videos_dir",
]

_PACKAGE = Path(__file__).resolve().parents[1]

#: Read into a plain string, so type checkers on one platform keep the other
#: platforms' branches.
_PLATFORM: str = sys.platform


def is_source_checkout(package: Path = _PACKAGE) -> bool:
    """Whether Voxframe is running from a source tree (``src/voxframe``).

    True for a clone, including an editable install; false for a wheel or the
    installer, which put the package in ``site-packages`` or an app bundle.
    """
    return package.parent.name == "src" and (package.parents[1] / "pyproject.toml").is_file()


def data_dir() -> Path:
    """The per-user folder for the library, cache and models."""
    if _PLATFORM == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "Voxframe"
    if _PLATFORM == "darwin":
        return Path.home() / "Library" / "Application Support" / "Voxframe"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "voxframe"


def videos_dir() -> Path:
    """Where finished videos go: the user's own videos folder."""
    if _PLATFORM == "darwin":
        return Path.home() / "Movies" / "Voxframe"
    return Path.home() / "Videos" / "Voxframe"


def models_dir() -> Path:
    return data_dir() / "models"


def default_library_path() -> Path:
    return Path("./library") if is_source_checkout() else data_dir() / "library"


def default_cache_path() -> Path:
    return Path("./.voxframe_cache") if is_source_checkout() else data_dir() / "cache"


def default_output_path() -> Path:
    return Path("./demo_output") if is_source_checkout() else videos_dir()


def configure_model_cache() -> Path | None:
    """Send model downloads to Voxframe's own models folder, when installed.

    Whisper and CLIP weights are fetched through the Hugging Face hub, which
    reads its cache location from the environment when it is first imported.
    Called at startup, before any model code is imported. A checkout, or a
    person who set ``HF_HOME`` or ``HF_HUB_CACHE`` themselves, is left alone.

    Returns:
        The folder used, or ``None`` if nothing was changed.
    """
    if is_source_checkout() or os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE"):
        return None
    folder = models_dir() / "huggingface"
    os.environ["HF_HUB_CACHE"] = str(folder)
    return folder
