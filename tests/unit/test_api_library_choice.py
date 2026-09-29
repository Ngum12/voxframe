"""Which library a web render uses (D-126).

Owner's decision 6: a new user with an empty library and no keys must still get
a decent first video. The web app chooses the library implicitly, so it must
never hand the pipeline one the matcher will refuse.

The bug this pins: the API used a library whenever its directory *existed*.
Opening a library creates its database, so an empty one is easy to end up with,
and the matcher refuses an empty library outright -- a first-run user got a
failed render instead of a video.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")

from voxframe.api.app import _library_has_assets


def test_a_missing_library_is_not_used(tmp_path: Path) -> None:
    assert not _library_has_assets(tmp_path / "does-not-exist")


def test_an_empty_directory_is_not_used(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()

    assert not _library_has_assets(library)


def test_a_library_with_an_empty_database_is_not_used(tmp_path: Path) -> None:
    """The exact state that failed a first-run render."""
    from voxframe.library.db import AssetLibrary

    library = tmp_path / "library"
    AssetLibrary(library).all_assets()  # creates the database, holds nothing
    assert (library / "library.db").is_file()

    assert not _library_has_assets(library)


def test_a_library_with_assets_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voxframe.library.db import AssetLibrary

    library = tmp_path / "library"
    AssetLibrary(library).all_assets()
    monkeypatch.setattr(AssetLibrary, "all_assets", lambda self, kind=None: ("x",))

    assert _library_has_assets(library)


def test_checking_does_not_create_a_library(tmp_path: Path) -> None:
    """Deciding whether to use a library must not leave one behind.

    Creating one as a side effect is how the empty-database state arises in
    the first place.
    """
    library = tmp_path / "library"

    _library_has_assets(library)

    assert not library.exists()


def test_an_unreadable_library_is_treated_as_empty(tmp_path: Path) -> None:
    """A corrupt database must mean plain backgrounds, not a failed render."""
    library = tmp_path / "library"
    library.mkdir()
    (library / "library.db").write_bytes(b"this is not sqlite")

    assert not _library_has_assets(library)
