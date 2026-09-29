"""A user-supplied music bed, ducked under the narration.

``--music track.mp3`` only. Voxframe does not source music (D-091): licensing
free music is materially harder than licensing images, because the terms vary
per track rather than per site, and getting it wrong exposes a user to a
takedown on something they have published.

Three things have to be right, and each has an obvious wrong version:

**Ducking.** The music must drop when the speaker talks and return when they
stop. `sidechaincompress` does this properly — it reacts to the narration's own
envelope — where a fixed low volume either buries the music or fights the
speech in the quiet parts.

**Length.** A track is rarely the length of the narration. Short means looping,
and a loop with an audible seam is the audio equivalent of the visible video
loop D-085 refuses. The loop point is crossfaded rather than butt-joined.

**Level.** Music that is merely quiet still competes. The bed sits well under
the speech and ducks further when speech is present, so the narration is never
something the listener has to work for.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

__all__ = ["MusicSettings", "music_filter_chain"]

log = structlog.get_logger(__name__)

#: Music level relative to full scale, before ducking.
#:
#: Measured rather than guessed (D-100). Against narration averaging -25 dB,
#: this puts the bed about 6 dB under the speech in quiet passages — present,
#: not competing — and the sidechain then drops it a further 11 dB when the
#: speaker talks, to roughly 17 dB under. The first value tried, 0.18, was
#: inaudible under speech at 22 dB down.
DEFAULT_MUSIC_GAIN = 0.30

#: How far the music drops when the speaker is talking, as a ratio of the
#: already-reduced bed level.
DUCK_RATIO = 8.0

#: Narration level, linear, above which the duck engages.
#:
#: Measured: speech averages about -25 dB (linear 0.054) on a normal recording,
#: so this sits comfortably below it and engages on speech rather than on room
#: tone. Verified to drop the bed by 11.2 dB during speech and leave it
#: untouched in silence (D-100).
DUCK_THRESHOLD = 0.03

#: Milliseconds for the duck to engage and release. Fast attack so the music
#: is out of the way before the first syllable lands; slow release so it does
#: not pump between words.
DUCK_ATTACK_MS = 20.0
DUCK_RELEASE_MS = 600.0

#: Crossfade at the loop point, in seconds. Long enough to hide a seam,
#: short enough not to audibly double the music either side of it.
LOOP_CROSSFADE_SECONDS = 2.0

#: Fade at the start and end of the finished bed.
FADE_IN_SECONDS = 1.5
FADE_OUT_SECONDS = 2.5


@dataclass(frozen=True, slots=True)
class MusicSettings:
    """How a music bed is mixed under the narration.

    Attributes:
        path: The user's track.
        gain: Bed level before ducking, 0.0 to 1.0.
        duck: Whether to duck under speech. Off gives a flat bed, which suits
            a track already mixed for the purpose.
        fade_in: Seconds.
        fade_out: Seconds.
        credit: Optional attribution, written into the credits file. Voxframe
            does not invent one: a track with no stated provenance is recorded
            as user-supplied (D-091).
    """

    path: Path
    gain: float = DEFAULT_MUSIC_GAIN
    duck: bool = True
    fade_in: float = FADE_IN_SECONDS
    fade_out: float = FADE_OUT_SECONDS
    credit: str = ""

    def attribution(self) -> str:
        """The credits line for this track."""
        if self.credit.strip():
            return f"Music: {self.credit.strip()}"
        return f"Music: {self.path.name} (supplied by the user)"


def music_filter_chain(
    settings: MusicSettings,
    video_seconds: float,
    music_seconds: float,
    *,
    narration_label: str = "0:a",
    music_label: str = "1:a",
    output_label: str = "aout",
) -> str:
    """Build the filter graph mixing narration and music.

    The narration is used twice: once as the sidechain control signal and once
    as the audible track. `asplit` is what makes that possible — feeding the
    same stream to two filters without it consumes it at the first.

    Args:
        settings: How to mix.
        video_seconds: Length of the finished video.
        music_seconds: Length of the supplied track.
        narration_label: Input label for the speech.
        music_label: Input label for the music.
        output_label: Label for the mixed result.

    Returns:
        A ``filter_complex`` string.
    """
    parts: list[str] = []

    # --- the music bed: length, then level ---
    bed = _length_chain(music_label, music_seconds, video_seconds, parts)

    parts.append(
        f"[{bed}]volume={settings.gain:.3f},"
        f"afade=t=in:st=0:d={settings.fade_in:.2f},"
        f"afade=t=out:st={max(0.0, video_seconds - settings.fade_out):.2f}"
        f":d={settings.fade_out:.2f}[bed]"
    )

    if not settings.duck:
        parts.append(f"[{narration_label}][bed]amix=inputs=2:normalize=0[{output_label}]")
        return ";".join(parts)

    # --- ducking ---
    # The narration drives the compressor and is also heard, so it must be
    # split: a stream consumed by sidechaincompress is not available again.
    parts.append(f"[{narration_label}]asplit=2[speech][key]")
    parts.append(
        f"[bed][key]sidechaincompress="
        f"threshold={DUCK_THRESHOLD}"
        f":ratio={DUCK_RATIO:.0f}"
        f":attack={DUCK_ATTACK_MS:.0f}"
        f":release={DUCK_RELEASE_MS:.0f}"
        f":makeup=1[ducked]"
    )
    # normalize=0: amix otherwise divides by the input count, halving the
    # narration for no reason.
    parts.append(f"[speech][ducked]amix=inputs=2:normalize=0[{output_label}]")

    return ";".join(parts)


def _length_chain(
    label: str, music_seconds: float, video_seconds: float, parts: list[str]
) -> str:
    """Make the music exactly as long as the video.

    Returns:
        The label carrying the length-adjusted music.
    """
    if music_seconds <= 0:
        # Unknown length: trim and pad, which is correct either way.
        parts.append(f"[{label}]atrim=0:{video_seconds:.3f},apad[len]")
        return "len"

    if music_seconds >= video_seconds:
        parts.append(f"[{label}]atrim=0:{video_seconds:.3f}[len]")
        return "len"

    # Shorter than the video: loop with a crossfade at the seam.
    #
    # `aloop` alone butt-joins the end to the start, and unless the track was
    # produced for looping that edge is audible — the same fault as a visible
    # video loop (D-085). acrossfade between two copies hides it.
    crossfade = min(LOOP_CROSSFADE_SECONDS, music_seconds / 3)
    # Each pass contributes this much before the next overlaps it.
    effective = music_seconds - crossfade
    passes = max(2, int(video_seconds / max(effective, 0.1)) + 2)

    parts.append(f"[{label}]asplit={passes}" + "".join(f"[m{i}]" for i in range(passes)))

    current = "m0"
    for index in range(1, passes):
        label_out = f"lp{index}"
        parts.append(
            f"[{current}][m{index}]acrossfade=d={crossfade:.2f}:c1=tri:c2=tri"
            f"[{label_out}]"
        )
        current = label_out

    parts.append(f"[{current}]atrim=0:{video_seconds:.3f}[len]")

    log.info(
        "render.music.looped",
        music_seconds=round(music_seconds, 1),
        video_seconds=round(video_seconds, 1),
        passes=passes,
        crossfade=round(crossfade, 2),
    )

    return "len"
