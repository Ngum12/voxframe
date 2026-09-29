"""A new user's library is readable once their first render creates it (D-143).

The sandbox allowed the library only if it existed when the app started. A new
user's library does not: their first render's download creates it. Every
image that render found was then refused, and the filmstrip showed none of
them until the app was restarted. Found in the browser verification of search.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from voxframe.api.security import PathOutsideSandbox, resolve_within
from voxframe.api.serve import build_context
from voxframe.config.settings import Settings


def _context(tmp_path: Path, library: Path):  # type: ignore[no-untyped-def]
    return build_context(
        Settings(
            library_path=library,
            cache_path=tmp_path / "cache",
            output_path=tmp_path / "out",
        )
    )


def test_a_library_created_after_startup_is_readable(tmp_path: Path) -> None:
    library = tmp_path / "library"
    context = _context(tmp_path, library)

    # The first render downloads into it.
    image = library / "sourced" / "pexels_0000.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"jpeg")

    assert resolve_within(image, context.allowed_paths) == image.resolve()


def test_the_rest_of_the_disk_stays_out(tmp_path: Path) -> None:
    """Allowing a library that does not exist yet must not widen anything."""
    context = _context(tmp_path, tmp_path / "library")
    elsewhere = tmp_path / "private" / "notes.txt"
    elsewhere.parent.mkdir()
    elsewhere.write_text("secret")

    with pytest.raises(PathOutsideSandbox):
        resolve_within(elsewhere, context.allowed_paths)
