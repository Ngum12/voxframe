"""Builders for every FFmpeg filter that takes a filesystem path.

D-021 was found in the subtitles filter, but the cause — FFmpeg's filter
argument tokenizer consuming apostrophes — is not specific to subtitles. Any
filter option holding a path has the same problem. This module is the complete
inventory, so the fix cannot be applied to one call site and forgotten at the
next.

Filters taking paths
--------------------
==================  =======================  =========================
Filter              Option                   Builder
==================  =======================  =========================
``ass``             ``filename``, ``fontsdir``  :func:`ass_filter`
``subtitles``       ``filename``, ``fontsdir``  :func:`subtitles_filter`
``movie``           ``filename``             :func:`movie_source`
``amovie``          ``filename``             :func:`amovie_source`
``drawtext``        ``fontfile``, ``textfile``  :func:`drawtext_filter`
==================  =======================  =========================

The one-directory constraint
----------------------------
A filter graph runs in a single process with a single working directory, so at
most one awkward path per invocation can be rescued by ``cwd``. When a graph
needs several, the caller must stage the files into a safe directory first;
:func:`stage_for_filters` does that.

This is not a limitation in practice. The path Voxframe cannot control is the
user's project directory, which is where subtitles and generated assets live.
Bundled fonts live inside the package, whose path is ours to keep clean.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from voxframe.render.ffpath.escape import (
    UnescapablePath,
    escape_filter_path,
    filter_path_context,
)

__all__ = [
    "amovie_source",
    "ass_filter",
    "drawtext_filter",
    "movie_source",
    "needs_staging",
    "stage_for_filters",
    "subtitles_filter",
]


def _escape_value(value: str) -> str:
    """Escape a non-path filter option value.

    Commas and colons separate filter options and graph elements; backslashes
    escape. Apostrophes cannot be escaped at all, so they are rejected.
    """
    if "'" in value:
        raise UnescapablePath(Path(value), "'")
    return value.replace("\\", "\\\\").replace(":", "\\:").replace(",", "\\,")


def ass_filter(
    subtitle_path: str | Path, *, fontsdir: str | Path | None = None
) -> tuple[str, Path | None]:
    """Build an ``ass`` filter for burning in ASS subtitles.

    Preferred over :func:`subtitles_filter` for Voxframe's captions: renders
    through libass directly, without the subtitle decoder, preserving styling
    overrides exactly as written.

    Args:
        subtitle_path: Path to the ``.ass`` file.
        fontsdir: Optional bundled-font directory, so rendering does not depend
            on the fonts a user happens to have installed.

    Returns:
        ``(filter_string, cwd)``. When ``cwd`` is not ``None``, FFmpeg **must**
        run from that directory — pass it to
        :func:`~voxframe.render.ffpath.runner.run_ffmpeg`.

    Raises:
        UnescapablePath: If ``fontsdir`` needs rescuing too; only one directory
            is available and the subtitle has claimed it. Use
            :func:`stage_for_filters`.
    """
    fp = filter_path_context(subtitle_path)
    parts = [f"filename={fp.quoted}"]

    if fontsdir is not None:
        parts.append(f"fontsdir='{escape_filter_path(fontsdir)}'")

    return "ass=" + ":".join(parts), fp.cwd


def subtitles_filter(
    subtitle_path: str | Path,
    *,
    fontsdir: str | Path | None = None,
    force_style: str | None = None,
    charenc: str | None = None,
) -> tuple[str, Path | None]:
    """Build a ``subtitles`` filter string.

    Use for SRT/VTT input or when ``force_style`` is needed. For Voxframe's
    generated ASS captions, prefer :func:`ass_filter`.

    Args:
        subtitle_path: Path to the subtitle file.
        fontsdir: Optional bundled-font directory.
        force_style: ASS style overrides, e.g. ``"FontSize=24,Bold=1"``.
        charenc: Input character encoding, e.g. ``"UTF-8"``.

    Returns:
        ``(filter_string, cwd)``.
    """
    fp = filter_path_context(subtitle_path)
    parts = [f"filename={fp.quoted}"]

    if fontsdir is not None:
        parts.append(f"fontsdir='{escape_filter_path(fontsdir)}'")
    if force_style is not None:
        parts.append(f"force_style='{_escape_value(force_style)}'")
    if charenc is not None:
        parts.append(f"charenc={_escape_value(charenc)}")

    return "subtitles=" + ":".join(parts), fp.cwd


def movie_source(
    path: str | Path,
    *,
    seek_point: float | None = None,
    streams: str | None = None,
    loop: int | None = None,
) -> tuple[str, Path | None]:
    """Build a ``movie`` source filter, for pulling video into a graph.

    Used for image and video overlays — a second input inside the filter graph
    rather than a separate ``-i``.

    Args:
        path: Video or image file.
        seek_point: Seconds to seek before reading.
        streams: Stream specifier, e.g. ``"dv+da"``.
        loop: Loop count; ``0`` loops forever. Useful for stills.

    Returns:
        ``(filter_string, cwd)``.
    """
    fp = filter_path_context(path)
    parts = [f"filename={fp.quoted}"]

    if seek_point is not None:
        parts.append(f"seek_point={seek_point}")
    if streams is not None:
        parts.append(f"streams={_escape_value(streams)}")
    if loop is not None:
        parts.append(f"loop={loop}")

    return "movie=" + ":".join(parts), fp.cwd


def amovie_source(path: str | Path, *, seek_point: float | None = None) -> tuple[str, Path | None]:
    """Build an ``amovie`` source filter, for pulling audio into a graph.

    Used for background music (pipeline step 10).

    Args:
        path: Audio file.
        seek_point: Seconds to seek before reading.

    Returns:
        ``(filter_string, cwd)``.
    """
    fp = filter_path_context(path)
    parts = [f"filename={fp.quoted}"]

    if seek_point is not None:
        parts.append(f"seek_point={seek_point}")

    return "amovie=" + ":".join(parts), fp.cwd


def drawtext_filter(
    *,
    text: str | None = None,
    textfile: str | Path | None = None,
    fontfile: str | Path | None = None,
    x: str = "0",
    y: str = "0",
    fontsize: int = 48,
    fontcolor: str = "white",
    extra: dict[str, str] | None = None,
) -> tuple[str, Path | None]:
    """Build a ``drawtext`` filter.

    Voxframe renders captions through libass rather than ``drawtext``, but this
    is used for simple overlays such as progress indicators and debug stamps.

    Args:
        text: Literal text. Mutually exclusive with ``textfile``.
        textfile: File containing the text. Preferred for anything long or
            containing characters awkward to escape.
        fontfile: Font file. Bundled fonts live inside the package.
        x: X expression, e.g. ``"(w-text_w)/2"``.
        y: Y expression.
        fontsize: Point size.
        fontcolor: Colour name or hex.
        extra: Additional options, escaped as values.

    Returns:
        ``(filter_string, cwd)``.

    Raises:
        ValueError: If neither or both of ``text`` and ``textfile`` are given.
        UnescapablePath: If more than one supplied path needs ``cwd`` rescuing.
    """
    if (text is None) == (textfile is None):
        raise ValueError("Provide exactly one of 'text' or 'textfile'")

    parts: list[str] = []
    cwd: Path | None = None

    if textfile is not None:
        fp = filter_path_context(textfile)
        parts.append(f"textfile={fp.quoted}")
        cwd = fp.cwd
    else:
        assert text is not None  # narrowed by the check above
        parts.append(f"text='{_escape_value(text)}'")

    if fontfile is not None:
        # cwd is already claimed if textfile needed it, so this must be
        # independently escapable.
        parts.append(f"fontfile='{escape_filter_path(fontfile)}'")

    parts.extend(
        [
            f"x={_escape_value(x)}",
            f"y={_escape_value(y)}",
            f"fontsize={fontsize}",
            f"fontcolor={_escape_value(fontcolor)}",
        ]
    )

    if extra:
        parts.extend(f"{k}={_escape_value(v)}" for k, v in sorted(extra.items()))

    return "drawtext=" + ":".join(parts), cwd


def needs_staging(*paths: str | Path | None) -> bool:
    """Whether these paths can be used together in one filter graph.

    Returns ``True`` when more than one requires a ``cwd`` change, since a
    single invocation has only one working directory.
    """
    rescued = 0
    for path in paths:
        if path is None:
            continue
        try:
            if filter_path_context(path).cwd is not None:
                rescued += 1
        except UnescapablePath:
            rescued += 1
    return rescued > 1


def stage_for_filters(paths: dict[str, Path], staging_dir: Path | None = None) -> dict[str, Path]:
    """Copy files into a directory guaranteed safe for filter arguments.

    The escape hatch for graphs needing several awkward paths at once. Copies
    are cheap relative to a render, and the staging directory is under
    Voxframe's control.

    Args:
        paths: Mapping of logical name to source path. Names become filenames,
            so they must be plain identifiers.
        staging_dir: Destination. A temporary directory is created if omitted;
            the caller owns its cleanup.

    Returns:
        Mapping of the same names to staged paths.

    Raises:
        ValueError: If a name is not filesystem-safe.
    """
    if staging_dir is None:
        staging_dir = Path(tempfile.mkdtemp(prefix="voxframe_stage_"))
    staging_dir.mkdir(parents=True, exist_ok=True)

    staged: dict[str, Path] = {}
    for name, source in paths.items():
        if not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                f"Staging name {name!r} must be alphanumeric with _ or -; "
                "it becomes a filename."
            )
        destination = staging_dir / f"{name}{source.suffix}"
        shutil.copy2(source, destination)
        staged[name] = destination

    return staged
