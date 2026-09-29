"""Tests for every filter builder that takes a filesystem path.

D-021 surfaced in the subtitles filter, but the cause is the filter-argument
tokenizer, which affects any path-bearing option. These tests cover the full
inventory so the fix cannot be applied at one call site and missed at the next.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.render.ffpath import (
    UnescapablePath,
    amovie_source,
    ass_filter,
    drawtext_filter,
    movie_source,
    needs_staging,
    stage_for_filters,
    subtitles_filter,
)


@pytest.fixture
def awkward_dir(tmp_path: Path) -> Path:
    """A directory whose name breaks naive filter-argument construction."""
    d = tmp_path / "Ngum's projet é"
    d.mkdir()
    return d


class TestAssFilter:
    def test_plain_path(self, tmp_path: Path) -> None:
        vf, cwd = ass_filter(tmp_path / "captions.ass")
        assert vf.startswith("ass=filename='")
        assert cwd is None

    def test_apostrophe_dir_uses_cwd(self, awkward_dir: Path) -> None:
        vf, cwd = ass_filter(awkward_dir / "captions.ass")
        assert cwd == awkward_dir
        assert vf == "ass=filename='captions.ass'"

    def test_fontsdir_included(self, tmp_path: Path) -> None:
        vf, _ = ass_filter(tmp_path / "c.ass", fontsdir=tmp_path / "fonts")
        assert "fontsdir='" in vf

    def test_unescapable_fontsdir_raises(self, tmp_path: Path, awkward_dir: Path) -> None:
        """Only one cwd exists; the subtitle has claimed it."""
        with pytest.raises(UnescapablePath):
            ass_filter(tmp_path / "c.ass", fontsdir=awkward_dir)


class TestSubtitlesFilter:
    def test_plain_path(self, tmp_path: Path) -> None:
        vf, _ = subtitles_filter(tmp_path / "captions.srt")
        assert vf.startswith("subtitles=filename='")

    def test_force_style_commas_escaped(self, tmp_path: Path) -> None:
        """Unescaped commas would be read as filter-graph separators."""
        vf, _ = subtitles_filter(tmp_path / "c.srt", force_style="FontSize=24,Bold=1")
        assert "\\," in vf

    def test_force_style_apostrophe_raises(self, tmp_path: Path) -> None:
        with pytest.raises(UnescapablePath):
            subtitles_filter(tmp_path / "c.srt", force_style="FontName=Ngum's Font")

    def test_charenc(self, tmp_path: Path) -> None:
        vf, _ = subtitles_filter(tmp_path / "c.srt", charenc="UTF-8")
        assert "charenc=UTF-8" in vf


class TestMovieSource:
    """Image and video overlays enter the graph through `movie`."""

    def test_plain_path(self, tmp_path: Path) -> None:
        vf, cwd = movie_source(tmp_path / "clip.mp4")
        assert vf.startswith("movie=filename='")
        assert cwd is None

    def test_apostrophe_dir_uses_cwd(self, awkward_dir: Path) -> None:
        vf, cwd = movie_source(awkward_dir / "photo.jpg")
        assert cwd == awkward_dir
        assert vf == "movie=filename='photo.jpg'"

    def test_options(self, tmp_path: Path) -> None:
        vf, _ = movie_source(tmp_path / "c.mp4", seek_point=2.5, loop=0)
        assert "seek_point=2.5" in vf
        assert "loop=0" in vf


class TestAmovieSource:
    """Background music enters the graph through `amovie` (pipeline step 10)."""

    def test_apostrophe_dir_uses_cwd(self, awkward_dir: Path) -> None:
        vf, cwd = amovie_source(awkward_dir / "music.mp3")
        assert cwd == awkward_dir
        assert vf == "amovie=filename='music.mp3'"


class TestDrawtextFilter:
    def test_literal_text(self) -> None:
        vf, cwd = drawtext_filter(text="Hello", x="10", y="20")
        assert "text='Hello'" in vf
        assert cwd is None

    def test_textfile_uses_cwd(self, awkward_dir: Path) -> None:
        vf, cwd = drawtext_filter(textfile=awkward_dir / "caption.txt")
        assert cwd == awkward_dir
        assert "textfile='caption.txt'" in vf

    def test_fontfile_escaped(self, tmp_path: Path) -> None:
        vf, _ = drawtext_filter(text="Hi", fontfile=tmp_path / "Inter.ttf")
        assert "fontfile='" in vf

    def test_colon_in_expression_escaped(self) -> None:
        """Position expressions may contain colons that must not split options."""
        vf, _ = drawtext_filter(text="Hi", x="if(gt(t\\,1)\\,10\\,20)")
        assert "text='Hi'" in vf

    def test_requires_exactly_one_text_source(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            drawtext_filter()
        with pytest.raises(ValueError, match="exactly one"):
            drawtext_filter(text="a", textfile=tmp_path / "b.txt")

    def test_apostrophe_in_literal_text_raises(self) -> None:
        """Captions with apostrophes must go through a textfile."""
        with pytest.raises(UnescapablePath):
            drawtext_filter(text="Ngum's lesson")


class TestStaging:
    def test_needs_staging_detects_multiple_awkward_paths(
        self, tmp_path: Path, awkward_dir: Path
    ) -> None:
        second = tmp_path / "another's dir"
        second.mkdir()

        assert not needs_staging(tmp_path / "a.ass")
        assert not needs_staging(awkward_dir / "a.ass", tmp_path / "b.ttf")
        assert needs_staging(awkward_dir / "a.ass", second / "b.ttf")

    def test_stage_produces_safe_paths(self, awkward_dir: Path, tmp_path: Path) -> None:
        from voxframe.render.ffpath import filter_path_context

        source = awkward_dir / "captions.ass"
        source.write_text("x", encoding="utf-8")

        staged = stage_for_filters({"subs": source}, tmp_path / "staging")

        assert staged["subs"].exists()
        assert filter_path_context(staged["subs"]).cwd is None
        assert staged["subs"].read_text(encoding="utf-8") == "x"

    def test_stage_rejects_unsafe_names(self, tmp_path: Path) -> None:
        source = tmp_path / "a.ass"
        source.write_text("x", encoding="utf-8")

        with pytest.raises(ValueError, match="alphanumeric"):
            stage_for_filters({"../escape": source}, tmp_path / "staging")
