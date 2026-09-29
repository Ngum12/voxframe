"""Tests that provenance cannot be blank (D-012, D-035).

NOT NULL accepts '' and '   ', which would satisfy the schema while leaving the
credits file naming nobody. Both the model and the database must reject those,
because the commercial-use guarantee rests on every asset being attributable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from voxframe.library import AssetLibrary
from voxframe.models import Asset, AssetKind, LicenseInfo


def _asset(license_info: LicenseInfo, asset_id: str = "a1") -> Asset:
    return Asset(
        id=asset_id,
        path=Path(f"img/{asset_id}.jpg"),
        kind=AssetKind.IMAGE,
        sha256=asset_id.ljust(64, "0"),
        width=1920,
        height=1080,
        license=license_info,
    )


class TestModelValidation:
    @pytest.mark.parametrize("field", ["name", "author", "source"])
    def test_empty_string_rejected(self, field: str) -> None:
        kwargs = {"name": "CC0-1.0", "author": "Someone", "source": "local"}
        kwargs[field] = ""
        with pytest.raises(ValueError, match=r"at least 1 character|cannot be empty"):
            LicenseInfo(**kwargs)

    @pytest.mark.parametrize("field", ["name", "author", "source"])
    @pytest.mark.parametrize("blank", ["   ", "\t", "\n", " \t \n "])
    def test_whitespace_only_rejected(self, field: str, blank: str) -> None:
        """The case NOT NULL and min_length both miss."""
        kwargs = {"name": "CC0-1.0", "author": "Someone", "source": "local"}
        kwargs[field] = blank
        with pytest.raises(ValueError, match="cannot be empty or whitespace"):
            LicenseInfo(**kwargs)

    def test_error_names_the_field_and_the_remedy(self) -> None:
        with pytest.raises(ValueError, match=r"LicenseInfo\.author"):
            LicenseInfo(name="CC0-1.0", author="  ", source="local")

        with pytest.raises(ValueError, match="Unknown"):
            LicenseInfo(name="CC0-1.0", author="  ", source="local")

    def test_unknown_author_is_accepted(self) -> None:
        """'Unknown' is an honest record; a blank is not."""
        info = LicenseInfo(name="CC0-1.0", author="Unknown", source="local")
        assert "Unknown" in info.attribution()

    def test_source_url_may_be_empty(self) -> None:
        """A user's own photograph has no canonical URL.

        Requiring a fabricated one would make provenance less trustworthy.
        """
        info = LicenseInfo(name="CC0-1.0", author="Ngum", source="local")
        assert info.source_url == ""


class TestDatabaseConstraints:
    """The database is the last line of defence.

    Model validation can be bypassed by a future code path that writes SQL
    directly, so the constraints exist independently.
    """

    def test_check_constraints_exist(self, tmp_path: Path) -> None:
        library = AssetLibrary(tmp_path / "lib")
        with sqlite3.connect(library.db_path) as connection:
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'assets'"
            ).fetchone()[0]

        for field in ("license_name", "license_author", "license_source"):
            assert f"TRIM({field})" in schema, f"no CHECK constraint on {field}"

    def test_direct_insert_of_blank_is_rejected(self, tmp_path: Path) -> None:
        """Writing SQL directly must not bypass provenance."""
        library = AssetLibrary(tmp_path / "lib")

        with sqlite3.connect(library.db_path) as connection, pytest.raises(
            sqlite3.IntegrityError
        ):
            connection.execute(
                """
                INSERT INTO assets (
                    id, path, kind, sha256, width, height,
                    license_name, license_author, license_source, added_at
                ) VALUES ('x', 'p.jpg', 'image', 'x', 1, 1, 'CC0', '   ', 'local', '2026-01-01')
                """
            )

    def test_valid_asset_still_stores(self, tmp_path: Path) -> None:
        library = AssetLibrary(tmp_path / "lib")
        asset = _asset(
            LicenseInfo(
                name="CC0-1.0",
                author="Ngum",
                source="local",
                source_url="https://example.org/a",
            )
        )
        library.add(asset)

        stored = library.get("a1")
        assert stored is not None
        assert stored.license.author == "Ngum"
