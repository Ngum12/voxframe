"""Tests for FFmpeg filter-path handling (D-018, D-021).

Pins behaviour verified experimentally against FFmpeg 8.1 on Windows 11. The
apostrophe cases matter most: no escaping sequence survives FFmpeg's filter
tokenizer, so those paths must be handled with a working-directory change
instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from voxframe.render.ffpath import (
    UnescapablePath,
    ass_filter,
    escape_filter_path,
    filter_path_context,
    subtitles_filter,
)


class TestEscapeFilterPath:
    def test_colons_escaped(self, tmp_path: Path) -> None:
        """Unescaped colons are read as filter option separators."""
        result = escape_filter_path(tmp_path / "sub.ass")
        assert ":" not in result.replace("\\:", "")

    def test_relative_path_resolved(self) -> None:
        """FFmpeg resolves filter paths against its own cwd, so absolutise."""
        assert len(escape_filter_path("sub.ass")) > len("sub.ass")

    def test_spaces_preserved(self, tmp_path: Path) -> None:
        result = escape_filter_path(tmp_path / "my captions.ass")
        assert "my captions.ass" in result

    def test_non_ascii_preserved(self, tmp_path: Path) -> None:
        result = escape_filter_path(tmp_path / "légendes_日本語.ass")
        assert "légendes_日本語" in result

    def test_apostrophe_raises_rather_than_silently_breaking(self, tmp_path: Path) -> None:
        """Refuse loudly instead of emitting a command that fails obscurely.

        FFmpeg strips apostrophes at the tokenizer, so any escaped form would
        produce a "Could not create a libass track" error naming a path the
        user never typed.
        """
        with pytest.raises(UnescapablePath, match="filter parser strips"):
            escape_filter_path(tmp_path / "Ngum's captions.ass")

    def test_error_names_the_remedy(self, tmp_path: Path) -> None:
        with pytest.raises(UnescapablePath, match="filter_path_context"):
            escape_filter_path(tmp_path / "it's.ass")


class TestFilterPathContext:
    def test_safe_path_needs_no_cwd(self, tmp_path: Path) -> None:
        fp = filter_path_context(tmp_path / "captions.ass")
        assert fp.cwd is None
        assert "captions.ass" in fp.name

    def test_apostrophe_dir_handled_by_cwd(self, tmp_path: Path) -> None:
        """The case that motivated this design: C:\\Users\\Ngum's laptop\\..."""
        awkward = tmp_path / "Ngum's project"
        awkward.mkdir()
        target = awkward / "captions.ass"

        fp = filter_path_context(target)

        assert fp.cwd == awkward
        assert fp.name == "captions.ass"
        assert "'" not in fp.name

    def test_apostrophe_in_filename_raises(self, tmp_path: Path) -> None:
        """A cwd change cannot rescue an apostrophe in the filename itself."""
        with pytest.raises(UnescapablePath):
            filter_path_context(tmp_path / "Ngum's captions.ass")

    def test_quoted_wraps_in_single_quotes(self, tmp_path: Path) -> None:
        fp = filter_path_context(tmp_path / "c.ass")
        assert fp.quoted.startswith("'")
        assert fp.quoted.endswith("'")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-letter behaviour")
class TestWindowsPaths:
    """Verified against FFmpeg 8.1 on Windows 11.

    - ``ass=C:\\path\\sub.ass``             FAILS (colon parsed as separator)
    - ``ass=C\\:/path/sub.ass``             FAILS (escaping colon alone is not enough)
    - ``ass=filename='C\\:/path/sub.ass'``  WORKS
    """

    def test_drive_colon_escaped(self, tmp_path: Path) -> None:
        result = escape_filter_path(tmp_path / "sub.ass")
        assert "\\:" in result
        assert not result.lower().startswith("c:")

    def test_backslashes_converted_to_forward(self, tmp_path: Path) -> None:
        result = escape_filter_path(tmp_path / "sub.ass")
        assert "/" in result
        assert result.replace("\\:", "").count("\\") == 0

    def test_full_filter_has_all_three_elements(self, tmp_path: Path) -> None:
        """Quoted filename, escaped colon, forward slashes: all required."""
        vf, cwd = ass_filter(tmp_path / "sub.ass")
        assert vf.startswith("ass=filename='")
        assert vf.endswith("'")
        assert "\\:" in vf
        assert "/" in vf
        assert cwd is None


class TestAssFilter:
    def test_returns_filter_and_cwd(self, tmp_path: Path) -> None:
        vf, cwd = ass_filter(tmp_path / "captions.ass")
        assert vf.startswith("ass=filename='")
        assert cwd is None

    def test_apostrophe_dir_returns_cwd(self, tmp_path: Path) -> None:
        awkward = tmp_path / "Ngum's project"
        awkward.mkdir()
        vf, cwd = ass_filter(awkward / "captions.ass")
        assert cwd == awkward
        assert vf == "ass=filename='captions.ass'"

    def test_fontsdir_included(self, tmp_path: Path) -> None:
        vf, _ = ass_filter(tmp_path / "c.ass", fontsdir=tmp_path / "fonts")
        assert "fontsdir='" in vf
        assert vf.count("filename='") == 1

    def test_unescapable_fontsdir_raises(self, tmp_path: Path) -> None:
        """Only one cwd exists and the subtitle already claims it."""
        fonts = tmp_path / "Ngum's fonts"
        fonts.mkdir()
        with pytest.raises(UnescapablePath):
            ass_filter(tmp_path / "c.ass", fontsdir=fonts)


class TestSubtitlesFilter:
    def test_basic_form(self, tmp_path: Path) -> None:
        vf, _ = subtitles_filter(tmp_path / "captions.srt")
        assert vf.startswith("subtitles=filename='")

    def test_force_style_commas_escaped(self, tmp_path: Path) -> None:
        """Unescaped commas would be read as filter-graph separators."""
        vf, _ = subtitles_filter(tmp_path / "c.srt", force_style="FontSize=24,Bold=1")
        assert "\\," in vf

    def test_force_style_apostrophe_raises(self, tmp_path: Path) -> None:
        with pytest.raises(UnescapablePath):
            subtitles_filter(tmp_path / "c.srt", force_style="FontName=Ngum's Font")
