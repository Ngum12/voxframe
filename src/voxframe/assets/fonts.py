"""Bundled fonts.

Fonts ship inside the package so caption rendering is identical on every
machine. Without this, libass falls back to whatever the system provides:
captions shift position, line breaks land differently, and golden-frame tests
compare against output that varies by machine rather than by code.

Bundled in Phase 2 rather than Phase 5 (project owner's instruction), so that
Phase 3's golden-frame tests have something stable to compare against.

Why static weights, not the variable font
-----------------------------------------
Google Fonts ships Inter only as a variable font. Measured against libass in
FFmpeg 8.1, a variable font renders at its **default instance** and ignores the
weight requested by the ASS ``Bold`` field — so ``Bold: -1`` produced regular
weight with no visible outline.

The static ``Inter-Regular.ttf`` and ``Inter-Bold.ttf`` from the upstream Inter
release render at the correct weights. They cost ~800 KB together, which is
worth it: bold with a strong outline is what keeps captions legible over
imagery, and that arrives in Phase 3.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

__all__ = ["DEFAULT_FONT_FAMILY", "FontsMissing", "bundled_fonts", "fonts_dir"]

#: The family name to request in ASS. Must match the fonts' internal name, not
#: the filename: libass matches on the family recorded in the font itself.
DEFAULT_FONT_FAMILY = "Inter"


class FontsMissing(RuntimeError):
    """Raised when bundled fonts are absent from an installation."""


@lru_cache(maxsize=1)
def fonts_dir() -> Path:
    """Directory holding the bundled fonts.

    Returns:
        Path to the font directory.

    Raises:
        FontsMissing: If the directory is absent or empty, which indicates a
            packaging fault rather than a user error.
    """
    directory = Path(__file__).resolve().parent / "fonts"

    if not directory.is_dir():
        raise FontsMissing(
            f"Bundled fonts directory is missing: {directory}\n"
            "This indicates a broken installation; reinstall Voxframe."
        )

    if not any(directory.glob("*.ttf")):
        raise FontsMissing(
            f"No fonts found in {directory}\n"
            "This indicates a broken installation; reinstall Voxframe."
        )

    return directory


def bundled_fonts() -> tuple[Path, ...]:
    """Every bundled font file, sorted by name."""
    return tuple(sorted(fonts_dir().glob("*.ttf")))
