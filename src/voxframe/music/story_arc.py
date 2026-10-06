"""Music dynamics across the spoken story, layered below speech protection.

These are editorial level envelopes, not guesses about the meaning of words.
They follow the rendered speech clock, including trimmed shorts and cards.
"""

from typing import Literal

MusicArc = Literal["steady", "rise", "punch"]


def arc_points(
    arc: MusicArc, opening: float, landing: float, video_end: float, fps: float,
) -> tuple[tuple[float, float], ...]:
    """Smooth frame-aligned attenuation (seconds, dB); never boosts a bed.

    The final spoken line gets breathing room, then music releases into the
    existing ring-out. Very short stories compress the same shape safely.
    """
    if arc == "steady" or video_end <= 0:
        return ((0.0, 0.0), (max(0.0, video_end), 0.0))
    first = max(0.0, min(opening, video_end))
    last = max(first, min(landing, video_end))
    duration = last - first
    if duration <= 1 / fps:
        return ((0.0, 0.0), (video_end, 0.0))
    if arc == "rise":
        relative = ((0, -10), (.12, -10), (.65, -3), (.82, 0), (.92, -6), (1, -6))
    else:
        relative = ((0, 0), (.12, 0), (.22, -8), (.65, -4), (.82, 0), (.92, -10), (1, -10))
    points = [(0.0, relative[0][1])]
    for position, level in relative:
        time = min(video_end, max(0.0, round((first + position * duration) * fps) / fps))
        if time == points[-1][0]:
            points[-1] = (time, level)
        else:
            points.append((time, level))
    release = min(video_end, max(points[-1][0], round((last + .45) * fps) / fps))
    if release > points[-1][0]:
        points.append((release, 0.0))
    if video_end > points[-1][0]:
        points.append((video_end, points[-1][1]))
    return tuple(points)
