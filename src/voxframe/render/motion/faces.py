"""Follow the speaker's face with a calm virtual camera (D-193).

Step 1 (D-192) placed a crop once per video, where the picture moves. A
speaker who leans, steps or turns then drifts towards the edge of a vertical
frame. This finds their face through the recording and plans a camera path
that keeps them framed.

What the camera does
--------------------
It behaves like a camera operator, not like a tracker, because a crop that
follows every nod is tiring to watch and reads as automatic:

- **It holds still** while the face stays within a comfortable zone around
  the centre of the frame (:data:`DEAD_ZONE`, a share of the crop's width).
- **It glides** when the face leaves that zone: one move for one change of
  place, however long the walk, easing in from where the camera was,
  following the face's own path (smoothed over about a second, centred, so
  it neither leads nor lags) and settling where the face comes to rest. It
  starts a little early: the whole recording is known in advance, so the
  camera can anticipate rather than chase.
- **It never jumps.** Brief misses, a hand across the face, or a second face
  passing through are smoothed out before the camera reacts.

The path is a short list of keyframes, which the renderer turns into a crop
that moves frame by frame (``render/compose/footage.py``).

Finding the face
----------------
YuNet, a small face detector from OpenCV's model zoo, bundled with the app
(MIT, 230 KB), run by OpenCV, which Voxframe already uses. Frames are read a
few times a second at a small size: where a face is does not need detail.
When several faces are in view, the speaker is the one that was there
before, else the largest. A recording where faces are found too rarely
(slides, a screen recording, someone filmed from behind) has no path, and
the crop falls back to where the picture moves, as before.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import structlog

from voxframe.render.encode.probe import FFmpegCapabilities, FootageInfo
from voxframe.render.ffpath import FFmpegError, run_ffmpeg

__all__ = [
    "DEAD_ZONE",
    "MODEL_PATH",
    "SAMPLES_PER_SECOND",
    "FaceSample",
    "Keyframe",
    "detector_available",
    "find_faces",
    "plan_camera",
    "primary_faces",
]

log = structlog.get_logger(__name__)

MODEL_PATH = Path(__file__).resolve().parents[2] / "assets" / "models" / (
    "face_detection_yunet_2023mar.onnx"
)

#: How often a frame is examined. Faster than a person moves across a frame,
#: slow enough that a long recording is read in a fraction of its length.
SAMPLES_PER_SECOND = 4

#: Width frames are examined at. A face filmed in a wide shot is still 30 or
#: more pixels high at this size, and YuNet finds faces from about 10.
WORK_WIDTH = 320

#: Seconds of recording read per FFmpeg call, so a long recording never needs
#: more than a few megabytes of frames on disk at once.
WINDOW_SECONDS = 30.0

#: Below this confidence a detection is ignored.
MIN_SCORE = 0.7

#: A face smaller than this share of the frame's height is a face in the
#: background, not the speaker.
MIN_FACE_HEIGHT = 0.04

#: When faces are found in less than this share of the samples, the
#: recording has no path and the crop falls back to where the picture moves.
MIN_COVERAGE = 0.3

#: How far the face may drift from the centre of the crop, as a share of the
#: crop's width (or height), before the camera moves.
DEAD_ZONE = 0.16

#: Seconds over which the camera eases into a move, which is also the
#: shortest move; and how far ahead of the face leaving the zone it begins.
EASE_SECONDS = 0.6
ANTICIPATE_SECONDS = 0.3

#: Samples in the median that smooths the raw positions: about a second.
MEDIAN_SAMPLES = 5


@dataclass(frozen=True, slots=True)
class FaceSample:
    """The speaker's face at one moment, in fractions of the frame.

    ``x`` and ``y`` are the face's centre, ``h`` its height; ``t`` is seconds
    on the recording's sound clock (the clock word timings use).
    """

    t: float
    x: float
    y: float
    h: float


@dataclass(frozen=True, slots=True)
class Keyframe:
    """Where the camera is centred at one moment; linear between keyframes."""

    t: float
    x: float
    y: float
    #: The face's height at that moment, so captions can keep clear of it.
    h: float


def detector_available() -> bool:
    """Whether faces can be found here: OpenCV with YuNet, and the model file."""
    if not MODEL_PATH.is_file():
        return False
    try:
        import cv2
    except ImportError:
        return False
    return hasattr(cv2, "FaceDetectorYN")


def find_faces(
    path: Path, info: FootageInfo, caps: FFmpegCapabilities
) -> list[list[tuple[float, float, float, float, float]]]:
    """Every detected face, per sample: ``(x, y, h, score, area)`` in fractions.

    Returns:
        One list per sample, in time order, possibly empty; the sample times
        are ``i / SAMPLES_PER_SECOND`` on the sound's clock.
    """
    import cv2

    height = max(2, round(WORK_WIDTH * info.height / info.width / 2) * 2)
    frame_bytes = WORK_WIDTH * height * 3
    detector = cv2.FaceDetectorYN.create(
        str(MODEL_PATH), "", (WORK_WIDTH, height), MIN_SCORE, 0.3, 20
    )
    found: list[list[tuple[float, float, float, float, float]]] = []
    # Sample times are on the sound's clock, like the words: the file's own
    # time is that plus the offset to its first sound (D-192).
    total = max(0.0, info.duration - info.audio_offset)
    with tempfile.TemporaryDirectory(prefix="vf-faces-") as scratch:
        raw = Path(scratch) / "window.bgr"
        start = 0.0
        while start < total:
            length = min(WINDOW_SECONDS, total - start)
            expected = max(1, math.ceil(length * SAMPLES_PER_SECOND - 1e-9))
            try:
                run_ffmpeg(
                    caps.ffmpeg_path,
                    [
                        "-loglevel", "error",
                        "-ss", f"{start + info.audio_offset:.6f}",
                        "-t", f"{length:.6f}",
                        "-i", str(path.resolve()),
                        "-an", "-sn", "-dn",
                        "-vf", (
                            f"fps={SAMPLES_PER_SECOND},"
                            f"scale={WORK_WIDTH}:{height},format=bgr24"
                        ),
                        "-frames:v", str(expected),
                        "-f", "rawvideo",
                        "-y", str(raw),
                    ],
                    timeout=600,
                )
                data = raw.read_bytes() if raw.is_file() else b""
            except (FFmpegError, OSError) as exc:
                log.info("faces.window_failed", start=round(start, 1), error=str(exc)[:120])
                data = b""
            frames = len(data) // frame_bytes
            for index in range(expected):
                if index >= frames:
                    found.append([])
                    continue
                image = np.frombuffer(
                    data[index * frame_bytes : (index + 1) * frame_bytes], dtype=np.uint8
                ).reshape(height, WORK_WIDTH, 3)
                _, faces = detector.detect(image)
                found.append(_as_fractions(faces, WORK_WIDTH, height))
            start += length
    return found


def _as_fractions(
    faces: np.ndarray | None, width: int, height: int
) -> list[tuple[float, float, float, float, float]]:
    if faces is None:
        return []
    result = []
    for face in faces:
        x, y, w, h, score = (float(value) for value in face[[0, 1, 2, 3, 14]])
        if score < MIN_SCORE or h / height < MIN_FACE_HEIGHT:
            continue
        centre_x, centre_y = (x + w / 2) / width, (y + h / 2) / height
        result.append((centre_x, centre_y, h / height, score, (w * h) / (width * height)))
    return result


def primary_faces(
    detections: Sequence[Sequence[tuple[float, float, float, float, float]]],
) -> list[FaceSample | None]:
    """The speaker's face in each sample, or ``None`` where it was not seen.

    The speaker is the face nearest where the speaker last was, so a second
    person passing through does not steal the camera; at the start, or after
    a long absence, the largest face.
    """
    chosen: list[FaceSample | None] = []
    last: FaceSample | None = None
    missing = 0
    for index, faces in enumerate(detections):
        t = index / SAMPLES_PER_SECOND
        if not faces:
            chosen.append(None)
            missing += 1
            continue
        if last is None or missing > 2 * SAMPLES_PER_SECOND:
            x, y, h, _, _ = max(faces, key=lambda face: face[4])
        else:
            x, y, h, _, _ = min(
                faces,
                key=lambda face, anchor=last: math.hypot(face[0] - anchor.x, face[1] - anchor.y)
                - 0.5 * face[2],
            )
        last = FaceSample(t=t, x=x, y=y, h=h)
        chosen.append(last)
        missing = 0
    return chosen


def plan_camera(
    samples: Sequence[FaceSample | None],
    *,
    crop_width: float,
    crop_height: float,
) -> list[Keyframe] | None:
    """A calm camera path through the samples, or ``None`` when faces are too rare.

    Args:
        samples: The speaker's face per sample, from :func:`primary_faces`.
        crop_width: The crop's width as a share of the footage's width
            (0.32 for a vertical video from landscape footage).
        crop_height: The crop's height as a share of the footage's height.

    Returns:
        Keyframes from the first sample to the last, at least two.
    """
    if not samples:
        return None
    seen = sum(1 for sample in samples if sample is not None)
    if seen / len(samples) < MIN_COVERAGE:
        return None

    filled = _fill_gaps(samples)
    xs = _median(np.array([s.x for s in filled]), MEDIAN_SAMPLES)
    ys = _median(np.array([s.y for s in filled]), MEDIAN_SAMPLES)
    hs = _median(np.array([s.h for s in filled]), MEDIAN_SAMPLES)
    times = [s.t for s in filled]

    zone_x = DEAD_ZONE * crop_width
    zone_y = DEAD_ZONE * crop_height
    # Where the face is over the first second, not in the first sample: a head
    # that bobs from the first frame should not start the camera off-centre.
    opening = slice(0, SAMPLES_PER_SECOND)
    camera_x, camera_y = float(np.median(xs[opening])), float(np.median(ys[opening]))
    keyframes = [Keyframe(times[0], camera_x, camera_y, float(hs[0]))]
    index = 1
    while index < len(times):
        off_x = abs(xs[index] - camera_x) > zone_x
        off_y = abs(ys[index] - camera_y) > zone_y
        if not (off_x or off_y):
            index += 1
            continue
        # One move for one change of place, however long the walk. The camera
        # follows the face's own path, smoothed, so it keeps pace with a
        # steady walk instead of racing ahead or lagging; it eases in from
        # where it was, and settles where the face comes to rest.
        settled = _settles(xs, ys, index, zone_x, zone_y)
        first = max(
            _first_at(times, keyframes[-1].t),
            index - round(ANTICIPATE_SECONDS * SAMPLES_PER_SECOND),
        )
        # Half a smoothing window past where it settles, so the last positions
        # average only the resting face and the camera centres on it.
        last = min(len(times) - 1, settled + SAMPLES_PER_SECOND // 2)
        while last + 1 < len(times) and times[last] - times[first] < EASE_SECONDS:
            last += 1
        smooth_x = _smoothed(xs, first, last)
        smooth_y = _smoothed(ys, first, last)
        follow_x = off_x or abs(smooth_x[-1] - camera_x) > zone_x
        follow_y = off_y or abs(smooth_y[-1] - camera_y) > zone_y
        begin = times[first]
        if begin > keyframes[-1].t:
            keyframes.append(Keyframe(begin, camera_x, camera_y, float(hs[first])))
        for offset, sample in enumerate(range(first + 1, last + 1), start=1):
            ramp = min(1.0, (times[sample] - begin) / EASE_SECONDS)
            eased = 0.5 - 0.5 * math.cos(math.pi * ramp)
            keyframes.append(
                Keyframe(
                    times[sample],
                    camera_x + (smooth_x[offset] - camera_x) * eased if follow_x else camera_x,
                    camera_y + (smooth_y[offset] - camera_y) * eased if follow_y else camera_y,
                    float(hs[sample]),
                )
            )
        camera_x, camera_y = keyframes[-1].x, keyframes[-1].y
        index = last + 1

    if keyframes[-1].t < times[-1]:
        keyframes.append(Keyframe(times[-1], camera_x, camera_y, float(hs[-1])))
    return [
        Keyframe(round(k.t, 3), round(k.x, 4), round(k.y, 4), round(k.h, 4))
        for k in keyframes
    ]


def _first_at(times: Sequence[float], t: float) -> int:
    """The first sample at or after ``t``."""
    for index, value in enumerate(times):
        if value >= t - 1e-9:
            return index
    return len(times) - 1


def _smoothed(values: np.ndarray, first: int, last: int) -> list[float]:
    """``values[first..last]``, each averaged over about a second around it.

    Centred, so it neither leads nor lags the face; the ends use what exists.
    """
    half = SAMPLES_PER_SECOND // 2
    return [
        float(np.mean(values[max(0, i - half) : min(len(values), i + half + 1)]))
        for i in range(first, last + 1)
    ]


def _settles(
    xs: np.ndarray, ys: np.ndarray, start: int, zone_x: float, zone_y: float
) -> int:
    """The first sample from ``start`` after which the face stays put for a second.

    "Stays put": every position in the next second within the dead zone of
    the first. A face that never settles settles at the end.
    """
    span = SAMPLES_PER_SECOND
    for index in range(start, len(xs)):
        window = slice(index, min(len(xs), index + span))
        if (
            np.all(np.abs(xs[window] - xs[index]) <= zone_x)
            and np.all(np.abs(ys[window] - ys[index]) <= zone_y)
        ):
            return index
    return len(xs) - 1


def _fill_gaps(samples: Sequence[FaceSample | None]) -> list[FaceSample]:
    """Every sample with a face: a miss takes the nearest face seen before it,
    or after it at the very start."""
    first = next(sample for sample in samples if sample is not None)
    filled: list[FaceSample] = []
    last = first
    for index, sample in enumerate(samples):
        if sample is not None:
            last = sample
        t = index / SAMPLES_PER_SECOND
        filled.append(FaceSample(t=t, x=last.x, y=last.y, h=last.h))
    return filled


def _median(values: np.ndarray, width: int) -> np.ndarray:
    """A running median, the same length, edges padded with their own values."""
    half = width // 2
    padded = np.concatenate([np.repeat(values[:1], half), values, np.repeat(values[-1:], half)])
    return np.array([np.median(padded[i : i + width]) for i in range(len(values))])
