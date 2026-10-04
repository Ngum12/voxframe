"""Cached join windows over unpadded scenes, with no changes to the frame grid.

A visual blend starts at the boundary: the outgoing last frame is held while
incoming frames play at their original timestamps. Only that small window is
effected. Canonical scenes are independent of every transition choice.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import structlog

from voxframe.render.compose.captioned import RenderError
from voxframe.render.compose.scenes import _INTERMEDIATE_ARGS, SegmentResult, _concat_listed
from voxframe.render.compose.transitions import TransitionPlan, xfade_options
from voxframe.render.encode.probe import FFmpegCapabilities
from voxframe.render.ffpath import run_ffmpeg

log = structlog.get_logger(__name__)
JOIN_VERSION = 4


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cached_path(directory: Path, values: list[object], prefix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps([JOIN_VERSION, *values]).encode()).hexdigest()[:24]
    return directory / f"{prefix}_{key}.mp4"


def encode_cached(caps: FFmpegCapabilities, args: list[str], target: Path, frames: int) -> None:
    """Publish only a complete clip, even if a render fails or runs concurrently."""
    staged = target.with_name(f".{target.stem}-{uuid4().hex}.mp4")
    try:
        run_ffmpeg(caps.ffmpeg_path, [*args, "-y", str(staged.resolve())])
        if caps.ffprobe_path:
            result = run_ffmpeg(caps.ffprobe_path, ["-v", "error", "-count_frames",
                "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
                "-of", "default=nw=1:nk=1", str(staged.resolve())])
            if result.stdout.strip() != str(frames):
                raise RenderError("A transition clip did not match its planned frame count.")
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)


def trim_piece(segment: SegmentResult, start: int, end: int, fps: float,
               caps: FFmpegCapabilities, directory: Path) -> Path:
    if start == 0 and end == segment.frames:
        return segment.path
    target = cached_path(directory, [digest(segment.path), start, end, fps], "body")
    if target.is_file():
        return target
    encode_cached(caps, ["-loglevel", "error", "-i", str(segment.path.resolve()),
        "-vf", f"trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,setsar=1",
        "-an", "-frames:v", str(end - start), "-r", str(fps),
        *_INTERMEDIATE_ARGS], target, end - start)
    return target


def render_join(outgoing: SegmentResult, incoming: SegmentResult, transition: TransitionPlan,
                fps: float, caps: FFmpegCapabilities, directory: Path) -> Path:
    target = cached_path(directory, [digest(outgoing.path), digest(incoming.path),
        transition.kind.value, transition.frames, transition.direction.value, fps], "join")
    if target.is_file():
        log.info("render.join.reused", after=transition.after_scene, key=target.stem)
        return target
    frames = transition.frames
    if not transition.is_blend or frames > incoming.frames:
        raise ValueError("A join needs a nonzero transition within the incoming scene.")
    # Reach the incoming frame fully on the window's final displayed frame.
    duration = max(1, frames - 1) / fps
    # FFmpeg 7 loses frame-rate metadata after trimming. Restore it before
    # tpad (or it clones zero frames), then again before xfade.
    chain = (
        f"[0:v]trim=start_frame={outgoing.frames - 1}:end_frame={outgoing.frames},"
        f"setpts=PTS-STARTPTS,fps={fps},"
        f"tpad=stop_mode=clone:stop_duration={(frames + 1) / fps:.9f},"
        f"trim=end_frame={frames},fps={fps},setsar=1[a];"
        f"[1:v]trim=end_frame={frames},setpts=PTS-STARTPTS,fps={fps},"
        f"setsar=1[b];"
        f"[a][b]xfade={xfade_options(transition)}:duration={duration:.9f}:offset=0[v]"
    )
    encode_cached(caps, ["-loglevel", "error", "-i", str(outgoing.path.resolve()),
        "-i", str(incoming.path.resolve()), "-filter_complex", chain, "-map", "[v]",
        "-an", "-frames:v", str(frames), "-r", str(fps), *_INTERMEDIATE_ARGS,
        ], target, frames)
    log.info("render.join.created", after=transition.after_scene, frames=frames,
             kind=transition.kind.value)
    return target


def concat_saved_transitions(segments: list[SegmentResult], transitions: list[TransitionPlan],
                             fps: float, caps: FFmpegCapabilities, output: Path,
                             directory: Path) -> Path:
    pieces: list[Path] = []
    for index, segment in enumerate(segments):
        incoming = transitions[index - 1] if index else None
        skip = incoming.frames if incoming and incoming.is_blend else 0
        if segment.frames > skip:
            pieces.append(trim_piece(segment, skip, segment.frames, fps, caps, directory))
        if index < len(transitions) and transitions[index].is_blend:
            pieces.append(render_join(segment, segments[index + 1], transitions[index],
                                      fps, caps, directory))
    _concat_listed(pieces, caps, output, output.parent / "saved-joins.txt")
    return output
