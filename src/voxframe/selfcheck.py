"""Checks an installed copy of Voxframe runs on itself (D-163).

Run by the installers' tests on GitHub's machines, against the installed
files: each proves one part of the app works end to end, not that a module
imports. Import checks are what let an installer without ``transformers``
ship -- every module that *was* there imported fine.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

__all__ = ["picture_model", "window"]


def picture_model(model_key: str = "default") -> str:
    """Embed a sentence and an image with the real picture model.

    Downloads the model on first use, like the app does.

    Raises:
        EmbedderUnavailable: If the model cannot be loaded, with the reason.
    """
    from PIL import Image

    from voxframe.library.embeddings import EMBEDDING_DIM, Embedder

    embedder = Embedder(use_gpu=False, model_key=model_key)
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "check.png"
        Image.new("RGB", (64, 64), (200, 120, 40)).save(image)
        text_vector = embedder.embed_text("a candle glowing in the dark")
        image_vector = embedder.embed_images([image])[0]
    if not len(text_vector) == len(image_vector) == EMBEDDING_DIM:
        raise AssertionError(f"{len(text_vector)} and {len(image_vector)} dimensions")
    return f"picture model {model_key!r}: text and image embedded"


def window() -> str:
    """Open and close a Tk window, as the app's "Voxframe is running" one."""
    from voxframe.launcher import prepare_tk

    prepare_tk()
    import tkinter

    root = tkinter.Tk()
    root.withdraw()
    version = str(root.tk.call("info", "patchlevel"))
    root.destroy()
    return f"Tk {version}"
