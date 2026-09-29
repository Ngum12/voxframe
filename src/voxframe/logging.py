"""Structured logging configuration.

Voxframe logs structurally so that long jobs can be diagnosed after the fact,
but the CLI is a user-facing tool: a person running ``voxframe make`` wants
progress, not a log stream. Logs are therefore quiet by default and turned up
with ``--verbose`` or ``VOXFRAME_LOG_LEVEL``.

Library users get whatever they configure themselves; calling
:func:`configure_logging` is the application's choice, not the library's.
"""

from __future__ import annotations

import logging
import os
import sys

import structlog

__all__ = ["LOG_LEVELS", "configure_logging"]

LOG_LEVELS = ("debug", "info", "warning", "error", "critical")


def configure_logging(level: str | None = None, *, json_output: bool = False) -> None:
    """Configure structlog for application use.

    Args:
        level: One of :data:`LOG_LEVELS`. Falls back to ``VOXFRAME_LOG_LEVEL``,
            then to ``warning`` — quiet enough that normal CLI output is not
            drowned, loud enough that real problems still surface.
        json_output: Emit JSON lines instead of formatted console output.
            Appropriate for the Phase 8 server, where logs are collected.
    """
    resolved = (level or os.environ.get("VOXFRAME_LOG_LEVEL") or "warning").lower()
    if resolved not in LOG_LEVELS:
        resolved = "warning"

    numeric = getattr(logging, resolved.upper())

    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="%H:%M:%S"),
    ]

    # A log line must never be able to raise. On Windows, stderr redirected to a
    # file or pipe is cp1252, and a traceback or a contributor's name outside
    # it raised UnicodeEncodeError from inside an exception handler -- which is
    # how a failed render once sat at "running" forever (D-094, D-124).
    # Unencodable characters are replaced instead.
    _make_tolerant(sys.stderr)

    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        # Logs go to stderr so they never interleave with the CLI's own output
        # on stdout, which keeps `voxframe ... | something` usable.
        #
        # Plain tracebacks, not rich ones: the rich formatter draws boxes in
        # characters most Windows code pages cannot represent, and a log is
        # read in whatever terminal the user happens to have.
        processors.append(
            structlog.dev.ConsoleRenderer(
                colors=sys.stderr.isatty(),
                exception_formatter=structlog.dev.plain_traceback,
            )
        )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def _make_tolerant(stream: object) -> None:
    """Replace unencodable characters on ``stream`` rather than raising.

    ``reconfigure`` exists on real text streams; a test harness may substitute
    something without it, which is fine to leave alone.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(errors="replace")
    except (ValueError, OSError):
        # Already detached or closed; nothing useful to do.
        pass
