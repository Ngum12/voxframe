"""Style templates: one object controlling how a video looks and feels.

Defined in Phase 2 rather than Phase 5 (DECISIONS.md D-006). Captions are the
first visible output, and every caption, font and pacing constant written in
Phases 2-4 would otherwise need retrofitting into this abstraction later.

A template bundles choices that must agree with each other. Bold social captions
with slow lecture pacing and gentle motion would look incoherent; grouping them
means a user picks one name and gets a consistent result, while still being able
to override any individual field.

Only the *schema* lands in Phase 2, along with one working default. The full set
of templates (clean educational, documentary, bold social, calm lecture) is
authored in Phase 5 when motion and transitions exist to tune.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

import structlog
from pydantic import BaseModel, Field, model_validator

log = structlog.get_logger(__name__)

__all__ = [
    "BUILTIN_TEMPLATES",
    "CaptionAnimation",
    "CaptionPosition",
    "CaptionStyle",
    "CaptionTransition",
    "MotionStyle",
    "PacingStyle",
    "StyleTemplate",
    "TransitionKind",
    "get_template",
]


class CaptionBacking(StrEnum):
    """How caption text is separated from the image behind it.

    Over a photograph the text needs *some* separation; which one is a taste
    decision rather than a correctness one, so all three are supported.
    """

    #: A translucent box sized to each line. Guarantees contrast, but ragged
    #: right edges when lines differ in length.
    BOX = "box"

    #: A translucent band spanning the frame width. Even edges, more of the
    #: image covered.
    BAND = "band"

    #: No backing: outline and drop shadow only. Cleanest over dark or busy
    #: imagery, least reliable over bright.
    OUTLINE = "outline"


class CaptionPosition(StrEnum):
    """Where captions sit within the safe area."""

    BOTTOM = "bottom"
    CENTER = "center"
    TOP = "top"


class CaptionAnimation(StrEnum):
    """How the words of a caption move as they are spoken (D-196).

    Every one is timed by each word's own timestamps, so none can drift from
    the voice.
    """

    #: The line, with the spoken word coloured. The original style.
    HIGHLIGHT = "highlight"
    #: The line fills with colour as each word is spoken.
    KARAOKE = "karaoke"
    #: The line builds up word by word.
    TYPEWRITER = "typewriter"
    #: Each word pops in as it is spoken, growing from its own centre.
    POP = "pop"
    #: The whole line is shown; the spoken word jumps.
    BOUNCE = "bounce"
    #: A coloured box glides behind the spoken word.
    SPOTLIGHT = "spotlight"
    #: The line, with no animation.
    PLAIN = "plain"


#: The animations drawn word by word, each word placed by Voxframe rather than
#: by libass's line layout, so one word can move without moving the others.
WORD_ANIMATIONS = frozenset(
    {CaptionAnimation.POP, CaptionAnimation.BOUNCE, CaptionAnimation.SPOTLIGHT}
)


class CaptionTransition(StrEnum):
    """How a caption comes on screen and goes (D-196)."""

    CUT = "cut"
    FADE = "fade"
    #: Grows in from slightly smaller.
    POP = "pop"
    #: Rises into place.
    SLIDE = "slide"
    #: Shrinks in from slightly larger.
    ZOOM = "zoom"


class TransitionKind(StrEnum):
    """How one scene becomes the next."""

    CUT = "cut"
    CROSSFADE = "crossfade"
    DIP_TO_BLACK = "dip_to_black"


class CaptionStyle(BaseModel):
    """Typography and layout for captions.

    Defaults are tuned for 1080p. Sizes are expressed relative to frame height
    so a template works across 9:16, 16:9, 1:1 and 4K without re-tuning.
    """

    model_config = {"frozen": True}

    font_family: str = Field(
        default="Inter",
        description="Font family name. Bundled families render identically everywhere.",
    )
    font_weight: int = Field(default=700, ge=100, le=900)

    #: Fraction of frame height. 0.045 is ~49px at 1080p: large enough to read
    #: on a phone, small enough not to dominate the image.
    font_size_ratio: float = Field(default=0.045, gt=0.01, le=0.2)

    primary_color: str = Field(default="&H00FFFFFF", description="ASS BGR hex.")
    highlight_color: str = Field(
        default="&H0000D7FF",
        description="Colour of the word currently being spoken (ASS BGR).",
    )
    outline_color: str = Field(default="&H00000000")
    outline_width: float = Field(default=3.0, ge=0, le=20)
    shadow_depth: float = Field(default=0.0, ge=0, le=20)

    #: How the text is separated from the image behind it (D-060).
    #:
    #: An outline alone is not enough over photographs: white text on snow
    #: survives only because of its outline and fails outright on a lighter
    #: image. Broadcast captions use a backing for exactly this reason.
    backing: CaptionBacking = Field(default=CaptionBacking.BOX)

    #: Deprecated alias kept so existing style files keep working. ``False``
    #: is equivalent to ``backing=outline``.
    caption_box: bool = Field(default=True)

    #: Backing colour, ASS &HAABBGGRR. The leading byte is *transparency*: 00
    #: is opaque, FF invisible. A0 is about 37% opaque — enough to carry the
    #: text without hiding the photograph behind it.
    box_color: str = Field(default="&HA0000000")

    position: CaptionPosition = Field(default=CaptionPosition.BOTTOM)

    #: Distance from the bottom edge as a fraction of height, for *vertical*
    #: video. Large enough to clear the caption bar and action buttons that
    #: TikTok, Reels and Shorts overlay on the lower third.
    margin_vertical_ratio: float = Field(default=0.14, ge=0.0, le=0.45)

    #: The same margin for landscape and square video (D-060). 0.09 sits in
    #: the 8-10% broadcast title-safe convention; the previous 0.055 placed
    #: captions closer to the edge than a viewer expects.
    #:
    #: Kept separate from the vertical value because one number cannot serve
    #: both: 14% of a 1080-high frame pushes captions a third of the way up a
    #: 16:9 frame, while 9% of a 1920-high vertical frame puts them under the
    #: platform UI.
    margin_vertical_ratio_wide: float = Field(default=0.09, ge=0.0, le=0.45)

    #: Side margin as a fraction of *width*. 6% leaves a comfortable gutter
    #: without narrowing the text box so far that lines wrap awkwardly.
    margin_horizontal_ratio: float = Field(default=0.06, ge=0.0, le=0.45)

    #: Spaces padded onto each line so a backing box has even edges (D-060).
    #: ASS sizes a box to its text, so lines of different length produce a
    #: ragged right edge; padding both sides equalises them.
    #:
    #: Ignored for ``band`` and ``outline`` backings, which have no per-line
    #: box to equalise.
    box_padding_chars: int = Field(default=2, ge=0, le=8)

    max_lines: int = Field(default=2, ge=1, le=4)
    max_chars_per_line: int = Field(default=32, ge=10, le=80)

    highlight_enabled: bool = Field(
        default=True, description="Highlight each word as it is spoken."
    )
    uppercase: bool = Field(default=False)

    #: How the words move as they are spoken (D-196). ``highlight_enabled``
    #: off still means no animation, as it always has.
    animation: CaptionAnimation = Field(default=CaptionAnimation.HIGHLIGHT)

    #: How each caption comes on screen and goes.
    transition: CaptionTransition = Field(default=CaptionTransition.CUT)
    transition_ms: int = Field(default=180, ge=40, le=600)

    #: Where the middle of the caption block sits, as a fraction of the
    #: frame's height, when a person has placed it. ``None`` keeps
    #: ``position`` and the margins.
    anchor_y: float | None = Field(default=None, ge=0.05, le=0.95)

    #: How much larger an emphasised word is drawn.
    emphasis_scale: float = Field(default=1.25, ge=1.0, le=2.0)

    @property
    def effective_animation(self) -> CaptionAnimation:
        """The animation drawn: none when highlighting is turned off."""
        if not self.highlight_enabled and self.animation is CaptionAnimation.HIGHLIGHT:
            return CaptionAnimation.PLAIN
        return self.animation

    @model_validator(mode="after")
    def _margins_leave_room(self) -> Self:
        """Reject margins that would leave no usable area."""
        if self.margin_horizontal_ratio * 2 >= 0.9:
            raise ValueError(
                f"margin_horizontal_ratio {self.margin_horizontal_ratio} leaves "
                "under 10% of the frame width for text"
            )
        return self

    def font_size_px(self, frame_height: int) -> int:
        """Font size in pixels for a given frame height."""
        return max(12, round(self.font_size_ratio * frame_height))

    def margin_vertical_px(self, frame_width: int, frame_height: int) -> int:
        """Bottom margin in pixels, chosen for the frame's shape.

        Vertical video uses the larger margin to stay clear of platform UI
        overlays; landscape and square use the smaller one, since the same
        fraction of a much taller frame would strand captions mid-screen.
        """
        is_portrait = frame_height > frame_width
        ratio = (
            self.margin_vertical_ratio if is_portrait else self.margin_vertical_ratio_wide
        )
        return round(ratio * frame_height)

    def margin_horizontal_px(self, frame_width: int) -> int:
        return round(self.margin_horizontal_ratio * frame_width)


class PacingStyle(BaseModel):
    """How often the visual changes."""

    model_config = {"frozen": True}

    min_scene_seconds: float = Field(default=4.0, gt=0.5, le=60)
    max_scene_seconds: float = Field(default=8.0, gt=1.0, le=120)

    #: A silence at least this long is a candidate scene boundary. Cutting on
    #: natural breath pauses is most of what makes pacing feel deliberate.
    pause_threshold_seconds: float = Field(default=0.35, ge=0.05, le=3.0)

    prefer_sentence_boundaries: bool = Field(default=True)

    @model_validator(mode="after")
    def _max_exceeds_min(self) -> Self:
        if self.max_scene_seconds <= self.min_scene_seconds:
            raise ValueError(
                f"max_scene_seconds ({self.max_scene_seconds}) must exceed "
                f"min_scene_seconds ({self.min_scene_seconds})"
            )
        return self


class MotionStyle(BaseModel):
    """Camera movement over stills."""

    model_config = {"frozen": True}

    #: 0 = static, 1 = maximum. Scales zoom and pan distance together.
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)

    ken_burns_enabled: bool = Field(default=True)
    parallax_enabled: bool = Field(default=True)

    #: Max parallax offset as a fraction of image width. Capped low by default
    #: so disocclusions stay small enough to inpaint invisibly (D-007).
    parallax_max_displacement: float = Field(default=0.04, gt=0.0, le=0.25)

    #: Below this depth-separation score, a scene silently falls back to Ken
    #: Burns and the plan records why (D-007).
    depth_quality_threshold: float = Field(default=0.45, ge=0.0, le=1.0)

    transition: TransitionKind = Field(default=TransitionKind.CROSSFADE)
    transition_seconds: float = Field(default=0.4, ge=0.0, le=3.0)


class StyleTemplate(BaseModel):
    """A complete look: captions, pacing, motion and transitions together.

    Templates are frozen. ``derive()`` produces a modified copy, so a user
    override cannot mutate a shared builtin.
    """

    model_config = {"frozen": True}

    name: str = Field(description="Identifier, e.g. 'clean-educational'.")
    description: str = Field(default="")

    captions: CaptionStyle = Field(default_factory=CaptionStyle)
    pacing: PacingStyle = Field(default_factory=PacingStyle)
    motion: MotionStyle = Field(default_factory=MotionStyle)

    def derive(self, **overrides: object) -> StyleTemplate:
        """Return a copy with fields replaced.

        Nested sections may be given as dicts and are merged field-wise, so a
        caller can change one caption property without restating the rest.
        """
        data = self.model_dump()
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key] = {**data[key], **value}
            else:
                data[key] = value
        return StyleTemplate.model_validate(data)


#: Phase 2 ships one working template. The rest are authored in Phase 5, when
#: motion and transitions exist to tune against real output.
BUILTIN_TEMPLATES: dict[str, StyleTemplate] = {
    "clean-educational": StyleTemplate(
        name="clean-educational",
        description=(
            "Readable, unfussy captions with moderate pacing. The default: it "
            "suits lessons and explainers and does nothing distracting."
        ),
    ),
    "documentary": StyleTemplate(
        name="documentary",
        description=(
            "Slow, composed pacing with long crossfades. For narration that "
            "wants the images to breathe."
        ),
        pacing=PacingStyle(
            # Longer scenes: a documentary holds a shot, and changing image
            # every four seconds reads as restless against measured narration.
            min_scene_seconds=6.0,
            max_scene_seconds=12.0,
            # A higher bar for what counts as a break, so scenes end on real
            # pauses rather than on every breath.
            pause_threshold_seconds=0.5,
        ),
        motion=MotionStyle(
            # Gentler Ken Burns to match the longer scenes; the same zoom over
            # twice the duration is half the apparent speed.
            intensity=0.3,
            transition=TransitionKind.CROSSFADE,
            transition_seconds=0.6,
        ),
        captions=CaptionStyle(
            backing=CaptionBacking.BOX,
            # Slightly smaller: the image is the subject here, not the text.
            font_size_ratio=0.042,
        ),
    ),
    "energetic": StyleTemplate(
        name="energetic",
        description=(
            "Fast cuts, punchy captions, no backing box. For social video "
            "where the pace is part of the format."
        ),
        pacing=PacingStyle(
            # Short scenes. The image changing often *is* the energy.
            min_scene_seconds=2.0,
            max_scene_seconds=4.5,
            # A low bar, so the cut lands on the smallest hesitation.
            pause_threshold_seconds=0.2,
        ),
        motion=MotionStyle(
            # Stronger movement over much shorter scenes.
            intensity=0.8,
            # Hard cuts: a crossfade is the opposite of punchy, and at 2-second
            # scenes there is no room for one anyway.
            transition=TransitionKind.CUT,
            transition_seconds=0.0,
        ),
        captions=CaptionStyle(
            # Outline rather than a box: cleanest over busy footage and the
            # look the format expects (D-095).
            backing=CaptionBacking.OUTLINE,
            # Larger and bolder — these are watched on a phone, often muted.
            font_size_ratio=0.058,
            uppercase=True,
        ),
    ),
    "minimal": StyleTemplate(
        name="minimal",
        description=(
            "Still images, no camera movement, hard cuts. For a slideshow "
            "look, and the fastest to render."
        ),
        pacing=PacingStyle(
            min_scene_seconds=5.0,
            max_scene_seconds=10.0,
        ),
        motion=MotionStyle(
            # No movement at all. Also the cheapest render: no zoompan, no
            # oversampling, no re-encode for transitions.
            ken_burns_enabled=False,
            parallax_enabled=False,
            transition=TransitionKind.CUT,
            transition_seconds=0.0,
        ),
        captions=CaptionStyle(backing=CaptionBacking.BOX),
    ),
}

DEFAULT_TEMPLATE = "clean-educational"


def get_template(name: str | None = None) -> StyleTemplate:
    """Look up a builtin template by name.

    Args:
        name: Template name. ``None`` selects the default.

    Returns:
        The template.

    Raises:
        KeyError: With the available names listed, since a typo here is
            otherwise a confusing failure.
    """
    key = name or DEFAULT_TEMPLATE
    try:
        template = BUILTIN_TEMPLATES[key]
    except KeyError:
        available = ", ".join(sorted(BUILTIN_TEMPLATES))
        raise KeyError(f"Unknown style template {key!r}. Available: {available}") from None

    # A caption-backing override applies on top of whatever the template
    # chose, so the three styles can be compared without editing templates
    # (D-093). Invalid values are ignored rather than fatal: a typo in an
    # environment variable should not stop a render.
    from voxframe.config.settings import get_settings

    override = get_settings().caption_backing
    if override:
        try:
            backing = CaptionBacking(override.strip().lower())
        except ValueError:
            log.warning(
                "style.caption_backing.unknown",
                value=override,
                allowed=[b.value for b in CaptionBacking],
            )
        else:
            if backing is not template.captions.backing:
                template = template.model_copy(
                    update={
                        "captions": template.captions.model_copy(
                            update={"backing": backing}
                        )
                    }
                )

    return template
