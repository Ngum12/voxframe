r"""Passing filesystem paths to FFmpeg filter arguments.

All FFmpeg filter-argument path handling in Voxframe goes through this module.
No other module builds filter paths by hand (DECISIONS.md D-018, D-021).

Why this needs its own module
-----------------------------
FFmpeg parses filter arguments through several nested layers, and ordinary
paths trip more than one of them:

- ``:`` separates options within a filter, so a Windows ``C:`` starts a bogus
  option.
- ``\`` is an escape character, so backslashes are consumed silently.
- ``'`` is consumed by the filter-argument tokenizer at a layer that no
  escaping sequence reaches.

The apostrophe case is the nasty one. Measured against FFmpeg 8.1, *every*
escaping strategy fails, including the ones that work in shells::

    Ngum's project  ->  filename='...Ngum'\''s project/s.ass'    FAILS
                    ->  filename='...Ngum\'s project/s.ass'      FAILS
                    ->  filename="...Ngum's project/s.ass"       FAILS
                    ->  filename=...Ngum\'s project/s.ass        FAILS

FFmpeg reports back ``Ngums`` — the apostrophe deleted — even for a *relative*
path with no drive letter, which proves the tokenizer, not the colon handling,
is responsible. This is not a hypothetical: ``C:\Users\Ngum's laptop\...`` is
an ordinary Windows home directory.

The approach
------------
Rather than escape the unescapable, :func:`filter_path_context` sidesteps it:
run FFmpeg with its working directory set to the file's parent and pass a bare
filename. Awkward characters then never reach the filter parser at all.

:func:`escape_filter_path` remains for paths that are known-safe and for
arguments where a relative path is not an option. It raises on characters it
cannot handle rather than producing a command that fails later with a
misleading error.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "FilterPath",
    "UnescapablePath",
    "escape_filter_path",
    "filter_path_context",
]

#: Characters the FFmpeg filter-argument tokenizer consumes regardless of
#: escaping. Paths containing these must use :func:`filter_path_context`.
UNESCAPABLE_CHARS = frozenset("'")


class UnescapablePath(ValueError):
    """Raised when a path cannot be safely placed in a filter argument.

    Signals that the caller should use :func:`filter_path_context` instead.
    """

    def __init__(self, path: Path, char: str) -> None:
        self.path = path
        self.char = char
        super().__init__(
            f"Path contains {char!r}, which FFmpeg's filter parser strips "
            f"regardless of escaping: {path}\n"
            f"Use filter_path_context() to run FFmpeg from the file's directory."
        )


@dataclass(frozen=True, slots=True)
class FilterPath:
    """A path prepared for use in an FFmpeg filter argument.

    Attributes:
        name: The value to place in the filter argument, already escaped.
        cwd: Working directory FFmpeg must run from, or ``None`` if the path is
            absolute and needs no particular directory.
    """

    name: str
    cwd: Path | None

    @property
    def quoted(self) -> str:
        """The name wrapped in the single quotes FFmpeg expects."""
        return f"'{self.name}'"


def filter_path_context(path: str | Path) -> FilterPath:
    """Prepare any path for an FFmpeg filter argument.

    The safe general-purpose entry point. If the path contains characters the
    filter parser would destroy, returns a bare filename plus the working
    directory FFmpeg must run from; otherwise returns an escaped absolute path.

    Args:
        path: The file to reference.

    Returns:
        A :class:`FilterPath`. Callers must pass ``cwd`` to
        :func:`subprocess.run` when it is not ``None``.

    Example:
        >>> fp = filter_path_context("/home/a/captions.ass")  # doctest: +SKIP
        >>> subprocess.run([ffmpeg, "-vf", f"ass={fp.quoted}"], cwd=fp.cwd)
    """
    resolved = Path(path).resolve()

    if any(c in UNESCAPABLE_CHARS for c in str(resolved)):
        # Only the filename need be safe; the directory is handled by cwd.
        bad = next((c for c in resolved.name if c in UNESCAPABLE_CHARS), None)
        if bad is not None:
            raise UnescapablePath(resolved, bad)
        return FilterPath(name=_escape(resolved.name), cwd=resolved.parent)

    return FilterPath(name=_escape(resolved.as_posix()), cwd=None)


def _escape(text: str) -> str:
    """Escape backslashes and colons for the filter-argument parser."""
    return text.replace("\\", "\\\\").replace(":", "\\:")


def escape_filter_path(path: str | Path) -> str:
    """Escape an absolute path for an FFmpeg filter argument.

    Prefer :func:`filter_path_context`, which handles every path. Use this only
    when a relative path is not viable.

    Args:
        path: Path to escape. Resolved to absolute, since FFmpeg resolves
            filter paths against its own working directory.

    Returns:
        An escaped path for placing inside single quotes.

    Raises:
        UnescapablePath: If the path contains a character FFmpeg would strip.
    """
    resolved = Path(path).resolve()

    for char in str(resolved):
        if char in UNESCAPABLE_CHARS:
            raise UnescapablePath(resolved, char)

    return _escape(resolved.as_posix())
