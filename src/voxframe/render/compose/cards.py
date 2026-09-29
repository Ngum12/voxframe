"""Title and chapter cards.

A title card is the one piece of text in the video that does not come from the
speech, which makes it the one piece that can be wrong in a way nobody notices
until it is published. So it appears **only when the user passes ``--title``**
(D-092). Deriving one from the filename would put "Recording (3)" across the
opening frame of someone's first video.

Chapter cards are different: they mark structure the audio actually has. A long
silence is a real boundary the speaker made, so a card there reports something
true rather than inventing it. Their text is the opening words of the section
that follows, not a generated summary.

Both are rendered as scene segments like any other, so they sit on the frame
grid (D-013) and pass through transitions and captions unchanged.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import structlog

from voxframe.plan.scene_plan import PlannedScene
from voxframe.render.encode.probe import FFmpegCapabilities
from voxframe.render.ffpath import drawtext_filter, run_ffmpeg

__all__ = [
    "MAX_CARD_CHARACTERS",
    "Card",
    "CardKind",
    "card_lines",
    "plan_chapter_cards",
    "render_card_segment",
]

log = structlog.get_logger(__name__)

#: A silence at least this long marks a chapter. Well above the pause used for
#: scene boundaries (0.35s) and for transitions (0.45s): a chapter break is a
#: speaker stopping, gathering, and starting a new thought.
CHAPTER_PAUSE_SECONDS = 2.0

#: Minimum audio length before chapter cards are considered at all. Below this
#: a chapter card is longer than the section it introduces.
MIN_AUDIO_FOR_CHAPTERS = 180.0

#: How long a card holds, in seconds.
TITLE_CARD_SECONDS = 3.0
CHAPTER_CARD_SECONDS = 2.0

#: Words taken from the following section to label a chapter.
CHAPTER_LABEL_WORDS = 6


class CardKind:
    """What a card is for. Plain strings; these reach the plan as text."""

    TITLE = "title"
    CHAPTER = "chapter"


@dataclass(frozen=True, slots=True)
class Card:
    """A title or chapter card to be inserted.

    Attributes:
        kind: ``title`` or ``chapter``.
        text: What to display. For a title this is the user's own words; for a
            chapter it is the opening of the section that follows.
        before_scene: Index of the scene this card precedes.
        seconds: How long it holds.
    """

    kind: str
    text: str
    before_scene: int
    seconds: float

    def frames(self, fps: float) -> int:
        """Length in whole frames, on the grid."""
        return max(1, round(self.seconds * fps))


def plan_chapter_cards(
    scenes: Sequence[PlannedScene],
    fps: float,
    audio_seconds: float,
    *,
    enabled: bool = True,
) -> list[Card]:
    """Find chapter boundaries in the speech.

    A chapter is a silence of at least :data:`CHAPTER_PAUSE_SECONDS`. That is a
    real structural break — the speaker stopped, not merely breathed — so a
    card there reports something the audio contains rather than something
    invented (D-092).

    Args:
        scenes: Planned scenes, in order.
        fps: Frame rate.
        audio_seconds: Total audio length.
        enabled: When false, no cards.

    Returns:
        Cards to insert, in scene order. Empty for short audio, where a card
        would be longer than the section it introduces.
    """
    if not enabled or audio_seconds < MIN_AUDIO_FOR_CHAPTERS:
        return []

    cards: list[Card] = []
    ordered = list(scenes)

    for previous, following in pairwise(ordered):
        if not previous.words or not following.words:
            continue

        pause = following.words[0].start - previous.words[-1].end
        if pause < CHAPTER_PAUSE_SECONDS:
            continue

        label = " ".join(
            word.text.strip()
            for word in following.words[:CHAPTER_LABEL_WORDS]
            if word.text.strip()
        )
        if not label:
            continue

        cards.append(
            Card(
                kind=CardKind.CHAPTER,
                text=label,
                before_scene=following.index,
                seconds=CHAPTER_CARD_SECONDS,
            )
        )

    log.info(
        "render.cards.chapters",
        found=len(cards),
        audio_seconds=round(audio_seconds, 1),
    )

    return cards


#: Longest card text accepted from a person (D-145). A card is read in two or
#: three seconds; more than this is a paragraph, not a title.
MAX_CARD_CHARACTERS = 90

#: Average glyph width as a share of the font size, for the bundled fonts.
#: Deliberately generous, so a line measured as fitting does fit.
_GLYPH_WIDTH = 0.56

#: Share of the frame width a card's text may use.
_TEXT_WIDTH = 0.86


def card_lines(text: str, *, width: int, font_size: int) -> list[str]:
    """Break card text into lines that fit the frame (D-145).

    A card used to be one line, centred: fine for "The Golden River" at 16:9,
    off both edges for a longer title or any title in 9:16, where barely a
    dozen title-sized characters fit across.
    """
    per_line = max(6, int(width * _TEXT_WIDTH / (font_size * _GLYPH_WIDTH)))
    lines = textwrap.wrap(" ".join(text.split()), per_line, break_long_words=True)
    return lines or [""]


def render_card_segment(
    caps: FFmpegCapabilities,
    card: Card,
    output: Path,
    *,
    width: int,
    height: int,
    fps: float,
    background: str,
    intermediate_args: Sequence[str],
    font_path: Path | None = None,
) -> Path:
    """Render one card to its own segment.

    The card sits on the same gradient used for unmatched scenes (D-050), so it
    reads as part of the video rather than as an interruption from a different
    tool.
    """
    frames = card.frames(fps)

    # A title is the largest text in the video; a chapter label is closer to
    # caption size, because it is a signpost rather than a statement.
    ratio = 0.085 if card.kind == CardKind.TITLE else 0.055
    font_size = max(16, round(height * ratio))

    # A gentle fade so the card arrives rather than appearing. Expressed as an
    # alpha ramp rather than a `fade` filter, so it applies to the text alone
    # and leaves the background steady.
    hold = max(0.1, card.seconds - 0.4)
    alpha = (
        f"if(lt(t,0.4),t/0.4,"
        f"if(lt(t,{hold:.2f}),1,max(0,({card.seconds:.2f}-t)/0.4)))"
    )

    # The text goes in files, not the filter string. Card text is the
    # speaker's own words, and an apostrophe in it — "Ruskin's" — cannot be
    # escaped at any layer of FFmpeg's parser (D-021). This surfaced the first
    # time a chapter card was generated from real speech (D-106).
    #
    # One drawtext per line, each centred on its own, so wrapped text stays
    # centred without drawtext's text_align, which older FFmpeg lacks (D-145).
    lines = card_lines(card.text, width=width, font_size=font_size)
    line_height = round(font_size * 1.3)
    block = line_height * len(lines)
    filters: list[str] = []
    text_paths: list[Path] = []
    cwd: Path | None = None
    for number, line in enumerate(lines):
        text_path = output.parent / f"{output.stem}.cardtext{number}.txt"
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(line, encoding="utf-8", newline="\n")
        text_paths.append(text_path)
        line_filter, cwd = drawtext_filter(
            textfile=text_path,
            fontfile=font_path,
            fontsize=font_size,
            fontcolor="white",
            # Centred: a card has nothing else in the frame to balance against.
            x="(w-text_w)/2",
            y=f"(h-{block})/2+{number * line_height}",
            extra={"alpha": alpha},
        )
        filters.append(line_filter)
    text_filter = ",".join(filters)

    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"gradients=s={width}x{height}:c0={background}:c1={background}"
                  f":x0=0:y0=0:x1=0:y1={height}:d={frames / fps:.3f}:r={fps}",
            "-vf", text_filter,
            "-frames:v", str(frames),
            "-r", str(fps),
            *intermediate_args,
            "-y", str(output.resolve()),
        ],
        cwd=cwd,
    )

    for text_path in text_paths:
        text_path.unlink(missing_ok=True)

    log.info(
        "render.card",
        kind=card.kind,
        frames=frames,
        text=card.text[:40],
    )

    return output
