"""Every path-bearing filter, exercised against real FFmpeg in a hostile directory.

The directory name ``Ngum's projet é`` combines the three things that break
filter-argument construction:

- an apostrophe, which the tokenizer strips regardless of escaping (D-021)
- a space, which needs quoting
- a non-ASCII character, which needs correct encoding end to end

Unit tests assert the shape of the arguments we build. Only running FFmpeg
proves they work: the Phase 1 bug passed every unit test and failed here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import (
    amovie_source,
    ass_filter,
    drawtext_filter,
    movie_source,
    run_ffmpeg,
    stage_for_filters,
    subtitles_filter,
)

pytestmark = pytest.mark.needs_ffmpeg

#: The directory name that breaks naive implementations.
AWKWARD = "Ngum's projet é"

MINIMAL_ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Bold, Outline, Alignment, MarginV
Style: Default,Arial,48,&H00FFFFFF,&H00000000,1,2,2,40

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,VOXFRAME
"""

MINIMAL_SRT = """1
00:00:00,000 --> 00:00:02,000
VOXFRAME
"""


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


@pytest.fixture
def awkward_dir(tmp_path: Path) -> Path:
    d = tmp_path / AWKWARD
    d.mkdir()
    return d


def _colour_input() -> list[str]:
    return ["-f", "lavfi", "-i", "color=c=navy:s=640x360:d=1"]


def _render(caps, vf: str, out: Path, cwd: Path | None) -> None:  # type: ignore[no-untyped-def]
    """Render one frame, raising with FFmpeg's own diagnostics on failure."""
    run_ffmpeg(
        caps.ffmpeg_path,
        ["-loglevel", "error", *_colour_input(), "-vf", vf, "-frames:v", "1", "-y", str(out)],
        cwd=cwd,
    )
    assert out.exists() and out.stat().st_size > 0, "ffmpeg reported success but wrote nothing"


class TestSubtitleFilters:
    def test_ass_filter(self, caps, awkward_dir: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        if not caps.has_libass:
            pytest.skip("build lacks libass")

        sub = awkward_dir / "captions.ass"
        sub.write_text(MINIMAL_ASS, encoding="utf-8")

        vf, cwd = ass_filter(sub)
        _render(caps, vf, tmp_path / "ass.png", cwd)

    def test_subtitles_filter(self, caps, awkward_dir: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        if not caps.has_libass:
            pytest.skip("build lacks libass")

        sub = awkward_dir / "captions.srt"
        sub.write_text(MINIMAL_SRT, encoding="utf-8")

        vf, cwd = subtitles_filter(sub)
        _render(caps, vf, tmp_path / "srt.png", cwd)

    def test_subtitles_with_force_style(self, caps, awkward_dir: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """force_style commas must not be read as filter separators."""
        if not caps.has_libass:
            pytest.skip("build lacks libass")

        sub = awkward_dir / "captions.srt"
        sub.write_text(MINIMAL_SRT, encoding="utf-8")

        vf, cwd = subtitles_filter(sub, force_style="FontSize=32,Bold=1")
        _render(caps, vf, tmp_path / "srt_styled.png", cwd)


class TestMediaSources:
    """Image and video overlays (pipeline steps 4, 8) and music (step 10)."""

    def test_movie_source_with_image(self, caps, awkward_dir: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        image = awkward_dir / "photo.png"
        run_ffmpeg(
            caps.ffmpeg_path,
            ["-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=100x100:d=1",
             "-frames:v", "1", "-y", str(image)],
        )

        # `movie` is a source filter: it has no inputs, so it starts its own
        # chain rather than consuming [in].
        vf, cwd = movie_source(image)
        _render(caps, f"{vf}[img];[in][img]overlay=10:10", tmp_path / "overlay.png", cwd)

    def test_amovie_source(self, caps, awkward_dir: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        music = awkward_dir / "music.wav"
        run_ffmpeg(
            caps.ffmpeg_path,
            ["-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             "-y", str(music)],
        )

        af, cwd = amovie_source(music)
        out = tmp_path / "mixed.wav"
        run_ffmpeg(
            caps.ffmpeg_path,
            ["-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
             "-filter_complex", f"{af}[bg];[0:a][bg]amix=inputs=2", "-y", str(out)],
            cwd=cwd,
        )
        assert out.stat().st_size > 0


class TestDrawtext:
    def test_textfile_from_awkward_dir(self, caps, awkward_dir: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """Text containing apostrophes belongs in a file, not an argument."""
        textfile = awkward_dir / "caption.txt"
        textfile.write_text("Ngum's leçon", encoding="utf-8")

        vf, cwd = drawtext_filter(textfile=textfile, x="20", y="20", fontsize=32)
        _render(caps, vf, tmp_path / "drawtext.png", cwd)


class TestStaging:
    def test_two_awkward_paths_via_staging(self, caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """One invocation has one cwd, so several awkward paths need staging."""
        if not caps.has_libass:
            pytest.skip("build lacks libass")

        first = tmp_path / AWKWARD
        first.mkdir()
        sub = first / "captions.ass"
        sub.write_text(MINIMAL_ASS, encoding="utf-8")

        second = tmp_path / "autre's dossier"
        second.mkdir()
        fontdir = second / "fonts"
        fontdir.mkdir()

        staged = stage_for_filters({"subs": sub}, tmp_path / "staging")

        vf, cwd = ass_filter(staged["subs"])
        assert cwd is None, "staged paths should need no cwd rescue"
        _render(caps, vf, tmp_path / "staged.png", cwd)


class TestRegressionGuard:
    def test_naive_construction_still_fails(self, caps, awkward_dir: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        """If this ever passes, FFmpeg changed and ffpath can be simplified."""
        if not caps.has_libass:
            pytest.skip("build lacks libass")

        sub = awkward_dir / "captions.ass"
        sub.write_text(MINIMAL_ASS, encoding="utf-8")

        from voxframe.render.ffpath.runner import FFmpegError

        with pytest.raises(FFmpegError):
            run_ffmpeg(
                caps.ffmpeg_path,
                ["-loglevel", "error", *_colour_input(),
                 "-vf", f"ass={sub}", "-frames:v", "1", "-y", str(tmp_path / "naive.png")],
            )
