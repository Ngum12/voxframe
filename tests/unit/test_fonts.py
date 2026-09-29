"""Tests for bundled fonts (D-033).

Fonts ship with the package so caption rendering is identical everywhere.
Without them libass falls back to system fonts, and a golden frame records the
test machine rather than the code's behaviour.
"""

from __future__ import annotations

from voxframe.assets import DEFAULT_FONT_FAMILY, bundled_fonts, fonts_dir


class TestBundledFonts:
    def test_fonts_directory_exists(self) -> None:
        assert fonts_dir().is_dir()

    def test_regular_and_bold_present(self) -> None:
        """Both weights are needed: bold for captions, regular for body text."""
        names = {path.name for path in bundled_fonts()}
        assert "Inter-Regular.ttf" in names
        assert "Inter-Bold.ttf" in names

    def test_fonts_are_not_empty(self) -> None:
        for path in bundled_fonts():
            assert path.stat().st_size > 10_000, f"{path.name} looks truncated"

    def test_license_ships_alongside(self) -> None:
        """OFL requires the license to travel with the font."""
        license_file = fonts_dir() / "Inter-LICENSE.txt"
        assert license_file.is_file()
        text = license_file.read_text(encoding="utf-8")
        assert "SIL Open Font License" in text

    def test_default_family_matches_bundled(self) -> None:
        """The ASS Fontname must match the family recorded inside the font."""
        assert DEFAULT_FONT_FAMILY == "Inter"
        assert any(DEFAULT_FONT_FAMILY in p.name for p in bundled_fonts())

    def test_style_default_uses_bundled_family(self) -> None:
        from voxframe.config.style import CaptionStyle

        assert CaptionStyle().font_family == DEFAULT_FONT_FAMILY
