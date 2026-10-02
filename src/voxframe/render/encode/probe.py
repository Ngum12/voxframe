"""Locate FFmpeg and discover what the installed build can actually do.

Capabilities are *probed*, never assumed (DECISIONS.md D-017). The development
machine illustrates why: it has ``libvpx-vp9`` and ``libaom-av1`` but lacks
``libopenh264`` and ``libsvtav1``, so a hardcoded fallback chain would break on
a build that is otherwise perfectly capable.
"""

from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from voxframe.processes import NO_WINDOW

__all__ = [
    "INSTALL_HINT",
    "FFmpegCapabilities",
    "FFmpegNotFound",
    "FootageInfo",
    "MediaInfo",
    "detect_silences",
    "find_ffmpeg",
    "probe_capabilities",
    "probe_footage",
    "probe_media",
]

# Ordered best-to-worst. Probed against the real build; missing entries are
# skipped with a log line rather than causing a failure.
ENCODER_PREFERENCE: tuple[str, ...] = (
    "h264_nvenc",
    "h264_qsv",
    "libx264",
    "libopenh264",
    "libvpx-vp9",
    "libsvtav1",
    "libaom-av1",
)

INSTALL_HINT = """FFmpeg was not found on your PATH.

Voxframe needs FFmpeg with libass support to render captions.

  Windows:  winget install Gyan.FFmpeg
            (or: choco install ffmpeg-full)
  macOS:    brew install ffmpeg
  Linux:    sudo apt install ffmpeg        # Debian/Ubuntu
            sudo dnf install ffmpeg        # Fedora

Then restart your terminal and run 'voxframe doctor' to verify.

If FFmpeg is installed somewhere unusual, set the path explicitly:
  VOXFRAME_FFMPEG_PATH=/path/to/ffmpeg"""


class FFmpegNotFound(RuntimeError):
    """Raised when no usable FFmpeg binary can be located."""

    def __init__(self, message: str = INSTALL_HINT) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class FFmpegCapabilities:
    """What the installed FFmpeg build supports."""

    ffmpeg_path: str
    ffprobe_path: str | None
    version: str
    encoders: frozenset[str] = field(default_factory=frozenset)
    filters: frozenset[str] = field(default_factory=frozenset)

    @property
    def has_libass(self) -> bool:
        """Whether subtitle rendering is available.

        Without this, Voxframe cannot draw captions at all — the single most
        important capability to check at startup.
        """
        return "ass" in self.filters or "subtitles" in self.filters

    @property
    def has_xfade(self) -> bool:
        """Whether crossfade transitions are available (D-014)."""
        return "xfade" in self.filters

    @property
    def has_zoompan(self) -> bool:
        """Whether Ken Burns motion via zoompan is available (D-015)."""
        return "zoompan" in self.filters

    def best_encoder(self, preference: tuple[str, ...] = ENCODER_PREFERENCE) -> str:
        """Return the best available encoder from the preference chain.

        Raises:
            FFmpegNotFound: If the build has no usable video encoder at all,
                which indicates a severely stripped-down installation.
        """
        for name in preference:
            if name in self.encoders:
                return name
        raise FFmpegNotFound(
            f"No usable video encoder found in {self.ffmpeg_path}.\n"
            f"Looked for: {', '.join(preference)}\n"
            f"{INSTALL_HINT}"
        )

    def missing_from_chain(
        self, preference: tuple[str, ...] = ENCODER_PREFERENCE
    ) -> tuple[str, ...]:
        """Encoders in the preference chain this build lacks. For diagnostics."""
        return tuple(n for n in preference if n not in self.encoders)


def find_ffmpeg(explicit_path: str | None = None) -> str:
    """Locate the FFmpeg binary.

    Args:
        explicit_path: An explicit path, typically from
            ``VOXFRAME_FFMPEG_PATH``. Checked first.

    Returns:
        Path to a usable ffmpeg binary.

    Raises:
        FFmpegNotFound: With platform-specific install instructions.
    """
    if explicit_path:
        found = shutil.which(explicit_path)
        if found:
            return found
        raise FFmpegNotFound(
            f"VOXFRAME_FFMPEG_PATH points to '{explicit_path}', "
            f"which is not an executable file.\n\n{INSTALL_HINT}"
        )

    found = shutil.which("ffmpeg")
    if not found:
        raise FFmpegNotFound()
    return found


def _run_ffmpeg(binary: str, *args: str) -> str:
    """Run FFmpeg and return stdout, tolerating its habit of using stderr."""
    result = subprocess.run(
        [binary, "-hide_banner", *args],
        capture_output=True,
        text=True,
        check=False,
        creationflags=NO_WINDOW,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout or result.stderr


def _parse_names(output: str) -> frozenset[str]:
    """Extract entry names from ``-encoders`` / ``-filters`` output.

    Both use a flags column followed by the name, but the flag alphabets and
    widths differ between the two and across FFmpeg versions, so the pattern
    stays deliberately loose.
    """
    names: set[str] = set()
    for line in output.splitlines():
        match = re.match(r"^\s*[A-Z.\-]{2,11}\s+([A-Za-z0-9_\-]+)\s", line)
        if match:
            names.add(match.group(1))
    return frozenset(names)


@functools.lru_cache(maxsize=4)
def probe_capabilities(explicit_path: str | None = None) -> FFmpegCapabilities:
    """Discover what the installed FFmpeg supports.

    Cached: probing spawns three subprocesses and the answer cannot change
    within a run.

    Raises:
        FFmpegNotFound: If FFmpeg is missing or not executable.
    """
    binary = find_ffmpeg(explicit_path)

    version_output = _run_ffmpeg(binary, "-version")
    first_line = version_output.splitlines()[0] if version_output else ""
    version_match = re.search(r"ffmpeg version (\S+)", first_line)
    version = version_match.group(1) if version_match else "unknown"

    encoders = _parse_names(_run_ffmpeg(binary, "-encoders"))
    filters = _parse_names(_run_ffmpeg(binary, "-filters"))

    ffprobe = shutil.which("ffprobe")

    return FFmpegCapabilities(
        ffmpeg_path=binary,
        ffprobe_path=ffprobe,
        version=version,
        encoders=encoders,
        filters=filters,
    )

@dataclass(frozen=True, slots=True)
class MediaInfo:
    """Dimensions and duration of a media file.

    Returned by :func:`probe_media` so callers outside this module do not need
    to run a subprocess themselves — subprocess use is deliberately confined
    here and to the FFmpeg runner (D-020).
    """

    width: int
    height: int
    duration: float

    @property
    def is_usable(self) -> bool:
        return self.width > 0 and self.height > 0


def probe_media(path: Path, caps: FFmpegCapabilities | None = None) -> MediaInfo:
    """Read a video file's dimensions and duration.

    Args:
        path: The media file.
        caps: Probed capabilities, re-probed when omitted.

    Returns:
        Its dimensions and duration in seconds.

    Raises:
        FFmpegNotFound: If ffprobe is unavailable.
        OSError: If the file cannot be read.
    """
    capabilities = caps or probe_capabilities()

    if not capabilities.ffprobe_path:
        raise FFmpegNotFound(
            "ffprobe is needed to read video clips but was not found. It "
            "normally ships alongside ffmpeg."
        )

    completed = subprocess.run(
        [
            capabilities.ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        creationflags=NO_WINDOW,
    )

    if completed.returncode != 0:
        raise OSError(f"ffprobe failed on {path.name}: {completed.stderr.strip()[:120]}")

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise OSError(f"ffprobe returned invalid JSON for {path.name}: {exc}") from exc

    streams = payload.get("streams") or [{}]
    return MediaInfo(
        width=int(streams[0].get("width") or 0),
        height=int(streams[0].get("height") or 0),
        duration=float((payload.get("format") or {}).get("duration") or 0.0),
    )


@dataclass(frozen=True, slots=True)
class FootageInfo:
    """What the renderer needs to show a recording's own picture, in sync.

    Attributes:
        width: Displayed width, after any rotation a phone recorded.
        height: Displayed height, after rotation.
        fps: The picture's average frame rate. Phones record a variable rate;
            the renderer resamples to the video's grid either way.
        duration: The file's length in seconds.
        audio_offset: Seconds from the file's start to its first sound.
            Transcription and the soundtrack count time from the first sound;
            FFmpeg seeks the picture from the file's start. Seeking to a
            word's time plus this offset shows the frame it was spoken over.
    """

    width: int
    height: int
    fps: float
    duration: float
    audio_offset: float


def _rate(text: str | None) -> float:
    """``30000/1001`` -> 29.97; anything unreadable -> 0.0."""
    if not text:
        return 0.0
    numerator, _, denominator = text.partition("/")
    try:
        value = float(numerator) / float(denominator or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return value if value > 0 else 0.0


def _rotation(stream: dict[str, object]) -> int:
    """Degrees a player rotates this stream by, from either place it is kept."""
    side_data = stream.get("side_data_list")
    for item in side_data if isinstance(side_data, list) else []:
        if isinstance(item, dict) and "rotation" in item:
            try:
                return int(float(item["rotation"]))
            except (TypeError, ValueError):
                return 0
    tags = stream.get("tags")
    if isinstance(tags, dict) and "rotate" in tags:
        try:
            return int(float(tags["rotate"]))
        except (TypeError, ValueError):
            return 0
    return 0


def _start(entry: dict[str, object] | None) -> float:
    if not entry:
        return 0.0
    try:
        return float(entry.get("start_time") or 0.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def probe_footage(path: Path, caps: FFmpegCapabilities | None = None) -> FootageInfo | None:
    """Read a recording's picture, or ``None`` when it has none worth showing.

    A file with no video stream, or only a cover image (an MP3's album art is
    a one-frame "video"), has nothing to show and returns ``None``.

    Raises:
        FFmpegNotFound: If ffprobe is unavailable.
        OSError: If the file cannot be read.
    """
    capabilities = caps or probe_capabilities()
    if not capabilities.ffprobe_path:
        raise FFmpegNotFound(
            "ffprobe is needed to read a video's picture but was not found. It "
            "normally ships alongside ffmpeg."
        )

    completed = subprocess.run(
        [
            capabilities.ffprobe_path,
            "-v", "error",
            "-show_entries",
            "stream=codec_type,width,height,avg_frame_rate,r_frame_rate,start_time"
            ":stream_disposition=attached_pic:stream_tags=rotate:stream_side_data=rotation"
            ":format=start_time,duration",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        creationflags=NO_WINDOW,
    )
    if completed.returncode != 0:
        raise OSError(f"ffprobe failed on {path.name}: {completed.stderr.strip()[:120]}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise OSError(f"ffprobe returned invalid JSON for {path.name}: {exc}") from exc

    streams = [s for s in payload.get("streams") or [] if isinstance(s, dict)]
    video = next(
        (
            s for s in streams
            if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        return None

    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
    if width <= 0 or height <= 0 or fps <= 0:
        return None
    if _rotation(video) % 180:
        width, height = height, width

    form = payload.get("format") or {}
    try:
        duration = float(form.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    # FFmpeg seeks relative to the file's own start: the earliest stream.
    file_start = _start(form) if form.get("start_time") is not None else min(
        _start(video), _start(audio)
    )
    offset = (_start(audio) - file_start) if audio is not None else 0.0

    return FootageInfo(
        width=width,
        height=height,
        fps=round(fps, 6),
        duration=duration,
        audio_offset=round(max(0.0, offset), 6),
    )


def detect_silences(
    path: Path,
    caps: FFmpegCapabilities | None = None,
    *,
    threshold_db: float = -32.0,
    min_seconds: float = 0.30,
) -> list[tuple[float, float]]:
    """Find silent stretches in an audio file.

    FFmpeg's ``silencedetect`` reports its findings on stderr rather than as
    data, so this parses them. Lives here because subprocess use is confined
    to this module and the FFmpeg runner (D-020).

    Returns:
        ``(start, end)`` pairs in seconds, in order. Empty when none are found
        or FFmpeg fails, which callers treat as "no safe split points".
    """
    capabilities = caps or probe_capabilities()

    completed = subprocess.run(
        [
            capabilities.ffmpeg_path,
            "-hide_banner",
            "-i", str(path),
            "-af", f"silencedetect=noise={threshold_db}dB:d={min_seconds}",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        check=False,
        creationflags=NO_WINDOW,
    )

    silences: list[tuple[float, float]] = []
    start: float | None = None

    for line in completed.stderr.splitlines():
        if "silence_start:" in line:
            try:
                start = float(line.split("silence_start:")[1].strip().split()[0])
            except (IndexError, ValueError):
                start = None
        elif "silence_end:" in line and start is not None:
            try:
                end = float(line.split("silence_end:")[1].strip().split()[0])
            except (IndexError, ValueError):
                continue
            if end > start:
                silences.append((start, end))
            start = None

    return silences
