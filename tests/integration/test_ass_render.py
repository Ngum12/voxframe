"""End-to-end checks that our filter paths work with the real FFmpeg binary.

Unit tests assert the *shape* of the produced arguments. These assert the
arguments are accepted by FFmpeg on this machine and render visible text — the
only evidence that counts, since the failure modes here are misleading enough
to have caused two wrong implementations already (DECISIONS.md D-021).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
from voxframe.render.ffpath import ass_filter

pytestmark = pytest.mark.needs_ffmpeg

MINIMAL_ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Alignment, MarginV
Style: Default,Arial,48,&H00FFFFFF,1,2,40

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,VOXFRAME
"""


@pytest.fixture(scope="module")
def caps():  # type: ignore[no-untyped-def]
    try:
        return probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")


@pytest.fixture
def subtitle(tmp_path: Path) -> Path:
    path = tmp_path / "sub.ass"
    path.write_text(MINIMAL_ASS, encoding="utf-8")
    return path


def _render(
    ffmpeg: str, vf: str, out: Path, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=navy:s=640x360:d=1",
            "-vf", vf, "-frames:v", "1", "-y", str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd) if cwd else None,
    )


def _require_libass(caps) -> None:  # type: ignore[no-untyped-def]
    if not caps.has_libass:
        pytest.skip("build lacks libass")


def test_plain_path_renders(caps, subtitle: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _require_libass(caps)
    out = tmp_path / "frame.png"

    vf, cwd = ass_filter(subtitle)
    result = _render(caps.ffmpeg_path, vf, out, cwd)

    assert result.returncode == 0, f"ffmpeg failed: {result.stderr}"
    assert out.stat().st_size > 0


def test_path_with_spaces(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _require_libass(caps)
    folder = tmp_path / "my video project"
    folder.mkdir()
    sub = folder / "my captions.ass"
    sub.write_text(MINIMAL_ASS, encoding="utf-8")
    out = tmp_path / "frame_spaces.png"

    vf, cwd = ass_filter(sub)
    result = _render(caps.ffmpeg_path, vf, out, cwd)

    assert result.returncode == 0, f"ffmpeg failed: {result.stderr}"


def test_path_with_apostrophe(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The real-world case: C:\\Users\\Ngum's laptop\\project\\captions.ass

    FFmpeg strips apostrophes at the filter tokenizer no matter how they are
    escaped, so ass_filter returns a cwd and a bare filename instead.
    """
    _require_libass(caps)
    awkward = tmp_path / "Ngum's video project"
    awkward.mkdir()
    sub = awkward / "captions.ass"
    sub.write_text(MINIMAL_ASS, encoding="utf-8")
    out = tmp_path / "frame_apos.png"

    vf, cwd = ass_filter(sub)
    assert cwd == awkward, "apostrophe path must be handled via cwd"

    result = _render(caps.ffmpeg_path, vf, out, cwd)

    assert result.returncode == 0, f"ffmpeg failed: {result.stderr}"
    assert out.stat().st_size > 0


def test_non_ascii_path(caps, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _require_libass(caps)
    folder = tmp_path / "vidéos_日本語"
    folder.mkdir()
    sub = folder / "captions.ass"
    sub.write_text(MINIMAL_ASS, encoding="utf-8")
    out = tmp_path / "frame_unicode.png"

    vf, cwd = ass_filter(sub)
    result = _render(caps.ffmpeg_path, vf, out, cwd)

    assert result.returncode == 0, f"ffmpeg failed: {result.stderr}"


@pytest.mark.skipif(sys.platform != "win32", reason="drive-letter parsing is Windows-specific")
def test_naive_path_still_fails(caps, subtitle: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The unescaped form must fail, or ffpath has no reason to exist.

    If a future FFmpeg accepts raw Windows paths, this fails and tells us the
    escaping can be simplified. That is a useful signal, not a problem.
    """
    _require_libass(caps)
    out = tmp_path / "frame_naive.png"

    result = _render(caps.ffmpeg_path, f"ass={subtitle}", out)

    assert result.returncode != 0, (
        "Raw Windows path unexpectedly succeeded. If FFmpeg now handles this, "
        "revisit DECISIONS.md D-018."
    )
