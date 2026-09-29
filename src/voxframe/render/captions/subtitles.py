"""Export captions as SRT and WebVTT.

These accompany the video rather than being burned into it, so viewers can turn
them off and platforms can index the text. Both formats carry one caption per
scene without word-level highlighting, which neither supports in a way players
handle consistently.
"""

from __future__ import annotations

from pathlib import Path

from voxframe.models.scene import Scene

__all__ = ["build_srt", "build_vtt", "write_srt", "write_vtt"]


def _srt_timestamp(seconds: float) -> str:
    """``HH:MM:SS,mmm`` — SRT uses a comma before milliseconds."""
    seconds = max(0.0, seconds)
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    milliseconds = round((seconds - int(seconds)) * 1000)

    if milliseconds == 1000:
        milliseconds = 0
        secs += 1
        if secs == 60:
            secs, minutes = 0, minutes + 1
            if minutes == 60:
                minutes, hours = 0, hours + 1

    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _vtt_timestamp(seconds: float) -> str:
    """``HH:MM:SS.mmm`` — WebVTT uses a dot."""
    return _srt_timestamp(seconds).replace(",", ".")


def _caption_lines(scenes: tuple[Scene, ...], fps: float) -> list[tuple[float, float, str]]:
    """Timed text for the non-silent scenes."""
    return [
        (scene.start_seconds(fps), scene.end_seconds(fps), scene.text)
        for scene in scenes
        if not scene.is_silent and scene.text.strip()
    ]


def build_srt(scenes: tuple[Scene, ...], fps: float) -> str:
    """Build an SRT document."""
    blocks: list[str] = []

    for number, (start, end, text) in enumerate(_caption_lines(scenes, fps), start=1):
        blocks.append(
            f"{number}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n"
        )

    return "\n".join(blocks)


def build_vtt(scenes: tuple[Scene, ...], fps: float) -> str:
    """Build a WebVTT document."""
    blocks = ["WEBVTT\n"]

    for start, end, text in _caption_lines(scenes, fps):
        blocks.append(f"{_vtt_timestamp(start)} --> {_vtt_timestamp(end)}\n{text}\n")

    return "\n".join(blocks)


def write_srt(path: Path, scenes: tuple[Scene, ...], fps: float) -> Path:
    """Write an SRT file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_srt(scenes, fps), encoding="utf-8", newline="\n")
    return path


def write_vtt(path: Path, scenes: tuple[Scene, ...], fps: float) -> Path:
    """Write a WebVTT file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_vtt(scenes, fps), encoding="utf-8", newline="\n")
    return path
