"""Find where the speaker is across a recording's frame (D-192).

A landscape recording made into a vertical video keeps a third of its width.
The middle third is where the speaker usually is, but not always: a person
filmed to one side, the "rule of thirds" framing most guides teach, would be
cropped out of their own video.

Method
------
**The speaker is what moves.** Across a talk the head turns, the mouth moves
and the hands gesture, while the room behind stays still. So pairs of frames a
fifth of a second apart are compared at many points through the recording, and
the difference is summed down each column of the picture. Where that sum peaks
is where the person is.

Chosen over a face detector for this first step because it needs no model and
no new dependency, and it fails safe: a recording where nothing moves (a slide
deck, a still), or where movement is spread everywhere (a moving camera),
gives no clear peak, and the crop stays in the centre, exactly as before.
Face tracking that follows a speaker who moves is the next step (ROADMAP:
"Framing that follows you").
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import structlog

from voxframe.render.encode.probe import FFmpegCapabilities, FootageInfo
from voxframe.render.ffpath import FFmpegError, run_ffmpeg

__all__ = ["SAMPLES", "find_speaker_x", "speaker_x_from_motion"]

log = structlog.get_logger(__name__)

#: Points through the recording where movement is measured.
SAMPLES = 16

#: Width the frames are compared at. Where a person is does not need detail.
_WORK_WIDTH = 160

#: Frames apart within each pair: about a fifth of a second at 30 fps.
_STEP = 6

#: How much of the movement must sit within a third of the width around the
#: peak for the peak to be trusted. Spread evenly, a third would hold a third.
_CONCENTRATION = 0.5


def speaker_x_from_motion(profile: np.ndarray) -> tuple[float, float]:
    """Where the movement is, from its strength in each column.

    Returns:
        ``(x, concentration)``: the centre of the movement from 0 to 1, and
        the share of it within a third of the width around that centre.
    """
    columns = profile.astype(np.float64)
    if columns.size == 0:
        return 0.5, 0.0
    # The still background is never perfectly still (noise, compression), so
    # only movement above the typical column counts.
    weights = np.clip(columns - np.median(columns), 0.0, None)
    total = float(weights.sum())
    if total <= 1e-9:
        return 0.5, 0.0
    # Smoothed before finding the peak, so a waving hand does not outweigh the
    # body it belongs to.
    window = max(3, columns.size // 8) | 1
    smooth = np.convolve(weights, np.ones(window) / window, mode="same")
    peak = int(np.argmax(smooth))
    third = columns.size // 3
    low, high = max(0, peak - third // 2), min(columns.size, peak + third // 2 + 1)
    near = weights[low:high]
    centre = float((near * np.arange(low, high)).sum() / max(near.sum(), 1e-9))
    return (centre + 0.5) / columns.size, float(near.sum()) / total


def find_speaker_x(
    path: Path, info: FootageInfo, caps: FFmpegCapabilities
) -> tuple[float, str]:
    """Where the speaker is across the frame, and how that was found.

    Returns:
        ``(x, source)``: ``x`` from 0 (left) to 1 (right); ``source`` is
        ``motion`` when the movement showed where they are, ``centre`` when it
        did not and the middle is used, as every crop did before.
    """
    height = max(2, round(_WORK_WIDTH * info.height / info.width / 2) * 2)
    frame_bytes = _WORK_WIDTH * height
    usable = max(0.0, info.duration - 0.5)
    times = [usable * (i + 0.5) / SAMPLES for i in range(SAMPLES)] if usable > 0 else [0.0]

    profile = np.zeros(_WORK_WIDTH, dtype=np.float64)
    pairs = 0
    with tempfile.TemporaryDirectory(prefix="vf-speaker-") as scratch:
        raw = Path(scratch) / "pair.gray"
        for at in times:
            try:
                run_ffmpeg(
                    caps.ffmpeg_path,
                    [
                        "-loglevel", "error",
                        "-ss", f"{at:.3f}",
                        "-i", str(path.resolve()),
                        "-an",
                        "-vf", f"framestep={_STEP},scale={_WORK_WIDTH}:{height},format=gray",
                        "-frames:v", "2",
                        "-f", "rawvideo",
                        "-y", str(raw),
                    ],
                    timeout=60,
                )
            except (FFmpegError, OSError) as exc:
                log.info("speaker.sample_failed", at=round(at, 2), error=str(exc)[:120])
                continue
            data = raw.read_bytes() if raw.is_file() else b""
            if len(data) < 2 * frame_bytes:
                continue
            frames = np.frombuffer(data[: 2 * frame_bytes], dtype=np.uint8).reshape(
                2, height, _WORK_WIDTH
            )
            profile += np.abs(frames[1].astype(np.int16) - frames[0].astype(np.int16)).sum(axis=0)
            pairs += 1

    if pairs == 0:
        return 0.5, "centre"
    x, concentration = speaker_x_from_motion(profile)
    log.info(
        "speaker.found", x=round(x, 3), concentration=round(concentration, 2), pairs=pairs
    )
    if concentration < _CONCENTRATION:
        return 0.5, "centre"
    return round(x, 4), "motion"
