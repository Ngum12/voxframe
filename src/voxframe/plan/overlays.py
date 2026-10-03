"""Pop-ups: things that appear on screen when a word is said (D-198).

Each pop-up is tied to a word, not to a time: it appears when that word is
spoken, so if the words move (a card added before them, a cut), it moves
with them. Kept in the scene plan like every other decision (D-011).

Kinds:

- **text:** a callout, in one of three looks;
- **sticker:** one of the bundled Fluent Emoji;
- **shape:** an arrow, a ring round something, an underline;
- **counter:** a number counting up to its value;
- **image:** a picture of the person's own.

Every one has a place (its centre, as fractions of the frame), a size, how
long it stays, and how it comes on screen. A video-wide **progress bar** is a
switch on the plan, not a pop-up.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "Entrance",
    "Overlay",
    "OverlayKind",
    "ShapeKind",
    "TextLook",
]


class OverlayKind(StrEnum):
    TEXT = "text"
    STICKER = "sticker"
    SHAPE = "shape"
    COUNTER = "counter"
    IMAGE = "image"


class TextLook(StrEnum):
    #: White text on a box of the accent colour.
    PILL = "pill"
    #: Large text with a heavy outline, on nothing.
    BOLD = "bold"
    #: Dark text on a white card.
    NOTE = "note"


class ShapeKind(StrEnum):
    ARROW_DOWN = "arrow_down"
    ARROW_UP = "arrow_up"
    ARROW_LEFT = "arrow_left"
    ARROW_RIGHT = "arrow_right"
    #: A ring, to circle something.
    RING = "ring"
    UNDERLINE = "underline"


class Entrance(StrEnum):
    POP = "pop"
    SLIDE = "slide"
    BOUNCE = "bounce"
    FADE = "fade"
    #: Simply there.
    NONE = "none"


#: Colours a person chooses from, as RGB hex; "accent" is the caption
#: highlight's gold.
COLOURS = {
    "accent": "#FFD700",
    "white": "#FFFFFF",
    "red": "#FF3B30",
    "green": "#34C759",
    "blue": "#0A84FF",
    "black": "#111111",
}


class Overlay(BaseModel):
    """One pop-up. Only the fields its kind uses matter."""

    model_config = ConfigDict(frozen=True)

    #: Short and unique within the plan: what the studio edits it by.
    id: str = Field(pattern=r"^[a-z0-9]{1,16}$")
    kind: OverlayKind

    #: When: the scene, and the word in its captions it appears on.
    scene: int = Field(ge=0)
    word: int = Field(default=0, ge=0)
    #: How long it stays, in seconds; it never outlasts the video.
    seconds: float = Field(default=2.0, ge=0.3, le=30.0)

    #: Where its centre is, as fractions of the frame's width and height.
    x: float = Field(default=0.5, ge=0.0, le=1.0)
    y: float = Field(default=0.3, ge=0.0, le=1.0)
    #: Its size, as a fraction of the frame's width: a sticker's or image's
    #: width, a shape's width, text's height times five.
    size: float = Field(default=0.3, ge=0.05, le=1.0)

    entrance: Entrance = Entrance.POP
    colour: str = Field(default="accent")

    #: Text: what it says. Counter: the number it counts to, with anything
    #: before or after it ("$1,200", "90%").
    text: str = Field(default="", max_length=80)
    look: TextLook = TextLook.PILL
    #: Sticker: its name in the bundled set.
    sticker: str = Field(default="", pattern=r"^[a-z0-9-]*$", max_length=60)
    shape: ShapeKind = ShapeKind.ARROW_DOWN
    #: Image: the file, inside the job's own folder.
    image_path: str = Field(default="")

    @field_validator("colour")
    @classmethod
    def _known_colour(cls, value: str) -> str:
        if value not in COLOURS:
            raise ValueError(f"unknown colour {value!r}")
        return value

    @property
    def rgb(self) -> tuple[int, int, int]:
        code = COLOURS[self.colour].lstrip("#")
        return int(code[0:2], 16), int(code[2:4], 16), int(code[4:6], 16)

    @property
    def drawn_by_libass(self) -> bool:
        """Text, shapes and counters are drawn with the captions, and so are
        seen live in the studio exactly; stickers and images are laid over
        the picture by FFmpeg."""
        return self.kind in (OverlayKind.TEXT, OverlayKind.SHAPE, OverlayKind.COUNTER)


_NUMBER = re.compile(r"^(?P<before>[^\d]*)(?P<number>\d[\d,]*(?:\.\d+)?)(?P<after>.*)$")


def counter_parts(text: str) -> tuple[str, float, int, bool, str] | None:
    """A counter's text, taken apart: what comes before the number, the
    number, its decimal places, whether it is written with commas, and what
    comes after. ``None`` when there is no number to count to."""
    match = _NUMBER.match(text.strip())
    if match is None:
        return None
    digits = match["number"]
    places = len(digits.split(".")[1]) if "." in digits else 0
    return (
        match["before"],
        float(digits.replace(",", "")),
        places,
        "," in digits,
        match["after"],
    )
