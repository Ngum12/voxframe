"""The single entry point for running FFmpeg.

Every FFmpeg invocation in Voxframe goes through :func:`run_ffmpeg`. It exists
to make one specific mistake impossible: changing the *process* working
directory to satisfy a filter path.

Why process-wide cwd is unacceptable
------------------------------------
``filter_path_context`` handles awkward paths by running FFmpeg from the file's
directory (DECISIONS.md D-021). The obvious implementation is::

    os.chdir(fp.cwd)          # NEVER DO THIS
    subprocess.run([...])

That is wrong even in single-threaded code, and actively dangerous from Phase 8
onward, when the API runs concurrent render jobs in one process:

- ``os.chdir`` is process-global. A second job changing directory mid-render
  silently breaks the first job's relative paths.
- It is not thread-safe, and the corruption is timing-dependent, so it would
  appear as rare, unreproducible render failures rather than an obvious bug.
- Any relative path resolved elsewhere in the process changes meaning
  underneath code that never asked for it.

``subprocess.run(cwd=...)`` sets the working directory of the *child* process
only. The parent is untouched, so concurrent jobs cannot interfere.

This is enforced, not merely documented: ``test_no_chdir_in_render_code`` parses
the render package's AST and fails if ``os.chdir`` appears anywhere.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = ["FFmpegError", "FFmpegResult", "run_ffmpeg"]


class FFmpegError(RuntimeError):
    """Raised when an FFmpeg invocation fails.

    Carries the command and stderr, because FFmpeg's diagnostics are often the
    only way to tell what went wrong and are easily lost otherwise.
    """

    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr

        detail = stderr.strip() or "(no stderr output)"
        super().__init__(
            f"FFmpeg failed with exit code {returncode}.\n"
            f"Command: {' '.join(command)}\n"
            f"Error:\n{detail}"
        )


@dataclass(frozen=True, slots=True)
class FFmpegResult:
    """Outcome of an FFmpeg invocation."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_ffmpeg(
    binary: str,
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: float | None = None,
) -> FFmpegResult:
    """Run FFmpeg with the given arguments.

    Args:
        binary: Path to the ffmpeg executable, from
            :func:`~voxframe.render.encode.probe.find_ffmpeg`.
        args: Arguments after the binary name. ``-hide_banner`` is added.
        cwd: Working directory **for the child process only**, as returned by
            the ``ffpath`` helpers. Never applied to this process.
        check: Raise :class:`FFmpegError` on non-zero exit.
        timeout: Seconds before the child is killed. ``None`` means no limit,
            which is appropriate for long renders.

    Returns:
        The invocation's result.

    Raises:
        FFmpegError: If ``check`` is set and FFmpeg exits non-zero.
        subprocess.TimeoutExpired: If ``timeout`` elapses.
    """
    command = [binary, "-hide_banner", *args]

    # The binary path comes from find_ffmpeg, never from user input.
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd) if cwd is not None else None,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )

    result = FFmpegResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )

    if check and not result.ok:
        raise FFmpegError(command, result.returncode, result.stderr)

    return result
