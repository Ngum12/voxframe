"""FFmpeg filter-argument path handling and invocation (D-018, D-021).

Every filesystem path entering an FFmpeg filter argument goes through this
package, and every FFmpeg invocation goes through :func:`run_ffmpeg`.
"""

from voxframe.render.ffpath.escape import (
    FilterPath,
    UnescapablePath,
    escape_filter_path,
    filter_path_context,
)
from voxframe.render.ffpath.filters import (
    amovie_source,
    ass_filter,
    drawtext_filter,
    movie_source,
    needs_staging,
    stage_for_filters,
    subtitles_filter,
)
from voxframe.render.ffpath.runner import FFmpegError, FFmpegResult, run_ffmpeg

__all__ = [
    "FFmpegError",
    "FFmpegResult",
    "FilterPath",
    "UnescapablePath",
    "amovie_source",
    "ass_filter",
    "drawtext_filter",
    "escape_filter_path",
    "filter_path_context",
    "movie_source",
    "needs_staging",
    "run_ffmpeg",
    "stage_for_filters",
    "subtitles_filter",
]
