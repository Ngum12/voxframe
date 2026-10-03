"""Generate ASS subtitles with word-by-word highlighting.

ASS (Advanced SubStation Alpha) is used rather than SRT because it supports
inline timing tags, which is what makes word-level highlighting possible in a
single subtitle file that libass renders in one pass.

How the highlighting works
--------------------------
For each scene, one Dialogue line is emitted **per word**, each showing the
whole caption but colouring a different word. Each line is timed to its word's
start and end, so libass swaps between them as speech progresses::

    Dialogue: 0,0:00:01.00,0:00:01.42,...,{\\c&HFFFFFF&}The {\\c&H00D7FF&}quick {\\c&HFFFFFF&}fox
    Dialogue: 0,0:00:01.42,0:00:01.88,...,{\\c&HFFFFFF&}The quick {\\c&H00D7FF&}fox

The alternative, ``\\k`` karaoke tags, ties highlighting to a single line's
duration and cannot express gaps between words, so it drifts whenever the
speaker pauses mid-sentence.

Timing comes directly from word timestamps, so captions cannot drift from the
audio: there is no separate caption clock to fall out of step.

Format note
-----------
The ``Format:`` line declares field order, and every ``Style:`` and
``Dialogue:`` line must match it exactly. A field-count mismatch does not
error — libass silently misassigns fields, so text ends up in the wrong place
or vanishes. Both are built from one field list here for that reason.
"""

from __future__ import annotations

import textwrap
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from voxframe.config.style import (
    WORD_ANIMATIONS,
    CaptionAnimation,
    CaptionBacking,
    CaptionPosition,
    CaptionStyle,
    CaptionTransition,
)
from voxframe.models.scene import Scene
from voxframe.models.transcript import Word

__all__ = ["build_ass", "format_timestamp", "write_ass"]

#: ASS alignment codes (numpad layout): 2 = bottom centre, 5 = middle, 8 = top.
#: The style for captions moved to the top of a frame (D-193).
TOP_STYLE = "VoxframeTop"

#: The style each word is drawn in when words are placed one by one (D-196):
#: an outline style, since the box behind them is drawn separately.
WORD_STYLE = "VoxframeWord"

#: The style boxes are drawn in: flat colour, no outline.
BOX_STYLE = "VoxframeBox"

_ALIGNMENT = {
    CaptionPosition.BOTTOM: 2,
    CaptionPosition.CENTER: 5,
    CaptionPosition.TOP: 8,
}

_STYLE_FIELDS = (
    "Name", "Fontname", "Fontsize", "PrimaryColour", "SecondaryColour",
    "OutlineColour", "BackColour", "Bold", "Italic", "Underline", "StrikeOut",
    "ScaleX", "ScaleY", "Spacing", "Angle", "BorderStyle", "Outline", "Shadow",
    "Alignment", "MarginL", "MarginR", "MarginV", "Encoding",
)

_EVENT_FIELDS = (
    "Layer", "Start", "End", "Style", "Name",
    "MarginL", "MarginR", "MarginV", "Effect", "Text",
)


def format_timestamp(seconds: float) -> str:
    """Format seconds as an ASS timestamp (``H:MM:SS.cc``).

    ASS uses centisecond precision, so times are rounded to 1/100 s. At 30 fps
    one frame is 3.3 cs, which is finer than the format can express — a known
    limit of ASS, not of the timing.
    """
    seconds = max(0.0, seconds)
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    centiseconds = round((seconds - int(seconds)) * 100)

    if centiseconds == 100:  # rounding pushed us into the next second
        centiseconds = 0
        secs += 1
        if secs == 60:
            secs = 0
            minutes += 1
            if minutes == 60:
                minutes = 0
                hours += 1

    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def _escape_text(text: str) -> str:
    """Escape text for an ASS Dialogue field.

    ``{`` and ``}`` delimit override tags, and a literal brace would be read as
    a malformed tag and dropped. Newlines become the explicit ``\\N`` break.
    """
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


class _TextWidth:
    """How wide caption text is drawn, in frame pixels, from the bundled font.

    libass sizes a font so its ascent and descent together equal the style's
    font size; Pillow sizes it by its em. Converting one to the other from the
    font's own metrics predicted a vertical caption at 1053 px that libass drew
    at 1059 (D-194), so a small allowance is added.
    """

    #: Allowance for the difference measured above, and for rounding.
    ALLOWANCE = 1.02

    #: How much wider libass draws a line than the font's advances add up to:
    #: measured on whole caption lines, in both shapes of frame (D-196).
    LIBASS_WIDTH = 1.007

    def __init__(self, style: CaptionStyle, height: int) -> None:
        from PIL import ImageFont

        from voxframe.assets import fonts_dir

        size = style.font_size_px(height)
        face = "Inter-Bold.ttf" if style.font_weight >= 600 else "Inter-Regular.ttf"
        self._font = ImageFont.truetype(str(fonts_dir() / face), size)
        ascent, descent = self._font.getmetrics()
        self._scale = size / max(1, ascent + descent) * self.ALLOWANCE
        self._outline = 2 * style.outline_width
        self._uppercase = style.uppercase

    def __call__(self, text: str) -> float:
        if self._uppercase:
            text = text.upper()
        return self._font.getlength(text) * self._scale + self._outline

    def advance(self, text: str) -> float:
        """How far libass moves along the line for ``text``: its advance, with
        no allowance and no outline. Words placed one by one are placed by it,
        and land where libass's own line layout puts them (D-196)."""
        if self._uppercase:
            text = text.upper()
        return self._font.getlength(text) * self._scale / self.ALLOWANCE * self.LIBASS_WIDTH

    @classmethod
    def for_frame(cls, style: CaptionStyle, height: int) -> _TextWidth | None:
        """A measurer, or ``None`` when the fonts are missing: then lines are
        limited by their number of characters alone, as they always were."""
        from voxframe.assets import FontsMissing

        try:
            return cls(style, height)
        except (FontsMissing, OSError):
            return None


def _usable_width(style: CaptionStyle, width: int, measure: _TextWidth) -> float:
    """The width a line may take: inside the side margins, less a box's padding."""
    usable = width - 2 * style.margin_horizontal_px(width)
    if style.backing is CaptionBacking.BOX:
        usable -= measure(_PAD_CHAR * (2 * style.box_padding_chars))
    return usable


def _wrap_words(
    words: tuple[Word, ...],
    style: CaptionStyle,
    width: int | None = None,
    height: int | None = None,
    emphasis: frozenset[int] = frozenset(),
) -> list[list[int]]:
    """Group word indices into display lines that fit the caption box.

    A line ends at ``max_chars_per_line`` characters, and, given the frame's
    size, when it would be wider than the frame allows (D-194). A vertical
    frame is under a third as wide as a landscape one at the same height and
    the same font size, so 32 characters ran off its edges; a landscape frame
    fits them easily, and its lines are as they were.

    Returns:
        Lists of word indices, one per line. Every word is placed; limiting how
        many lines are on screen at once is :func:`_paginate`'s job. A single
        word too wide for any line still gets a line of its own.
    """
    if not words:
        return []

    measure = (
        _TextWidth.for_frame(style, height) if width is not None and height is not None else None
    )
    usable = _usable_width(style, width, measure) if measure is not None and width else 0.0

    lines: list[list[int]] = []
    current: list[int] = []
    current_text = ""
    # An emphasised word is drawn larger, and takes that much more of a line.
    extra = 0.0
    grow = style.emphasis_scale - 1.0

    for index, word in enumerate(words):
        token = word.text.strip()
        if not token:
            continue

        candidate = f"{current_text} {token}" if current else token
        added = grow * measure.advance(token) if measure is not None and index in emphasis else 0.0
        too_long = len(candidate) > style.max_chars_per_line
        too_wide = measure is not None and measure(candidate) + extra + added > usable

        if current and (too_long or too_wide):
            lines.append(current)
            current = [index]
            current_text = token
            extra = added
        else:
            current.append(index)
            current_text = candidate
            extra += added

    if current:
        lines.append(current)

    return lines


def _paginate(
    lines: list[list[int]], style: CaptionStyle
) -> list[list[list[int]]]:
    """Group display lines into pages of at most ``max_lines``.

    A scene longer than one caption box used to have its overflow **dropped**,
    so roughly half of a 23-word scene was never captioned at all — "accomplish"
    was missing from the output entirely while the word after it appeared. That
    is silent content loss, and it is worse than the overflow it was avoiding.

    Paging instead keeps every word: each page is shown during the span of its
    own words, so the caption advances through a long scene rather than freezing
    on its opening and discarding the rest.
    """
    return [
        lines[start : start + style.max_lines]
        for start in range(0, len(lines), style.max_lines)
    ]


#: The character a backing box is padded with: a non-breaking space, which
#: libass keeps where it would drop an ordinary one.
_PAD_CHAR = "\u00a0"

#: Width of a space in the bundled Inter font, as a fraction of font size.
#: Measured, not assumed: ``ImageFont.getlength`` reports 24 px at size 100.
_SPACE_WIDTH_RATIO = 0.24

#: Average width of a text character in Inter, as a fraction of font size.
#: Between 'n' (0.62) and 'M' (0.93), weighted toward lowercase since captions
#: are mostly lowercase.
_TEXT_WIDTH_RATIO = 0.55


def _pad_lines(
    rendered: list[str], plain: list[str], style: CaptionStyle, width: int
) -> list[str]:
    """Pad lines so a backing box has even edges (D-060).

    ASS sizes a box to its text, so lines of different length leave a ragged
    right edge. Padding both sides to a common width squares them off.

    Args:
        rendered: Lines with colour tags, which must not be measured.
        plain: The same lines without tags, for measuring.
        style: Caption styling.
        width: Frame width, used to estimate how many characters span it.

    Returns:
        Padded lines, in the same order.
    """
    if style.backing is CaptionBacking.OUTLINE or not rendered:
        return rendered

    if style.backing is CaptionBacking.BAND:
        # Fill the usable width so every line's box spans the frame.
        #
        # The padding character is a non-breaking space, and in Inter that is
        # **0.24** of the font size, not the 0.5 this originally assumed — so
        # it added roughly half the padding needed and the band came out only
        # slightly wider than a box. That is the "too conservative" fault
        # D-060 recorded as unfixed; measured from the bundled font rather
        # than estimated again (D-093).
        usable = width - 2 * style.margin_horizontal_px(width)
        pad_width = max(1.0, style.font_size_px(width) * _SPACE_WIDTH_RATIO)

        # Text characters are wider than the padding, so the shortfall is
        # measured in text widths and converted into padding characters.
        text_width = max(1.0, style.font_size_px(width) * _TEXT_WIDTH_RATIO)
        longest = max(len(p) for p in plain)

        shortfall_px = max(0.0, usable - longest * text_width)
        target = longest + int(shortfall_px / pad_width)
    else:
        target = max(len(p) for p in plain) + style.box_padding_chars * 2

    padded: list[str] = []
    for line, text in zip(rendered, plain, strict=True):
        shortfall = max(0, target - len(text))
        left = shortfall // 2
        # A non-breaking space, so libass keeps the padding rather than
        # collapsing it as leading and trailing whitespace.
        pad = " "  # noqa: RUF001 - deliberate; see comment above
        padded.append(f"{pad * left}{line}{pad * (shortfall - left)}")

    return padded


@dataclass(frozen=True)
class _Timing:
    """Where the spoken word falls within one Dialogue line, in milliseconds
    from the line's start: what karaoke fills and typewriter reveals by."""

    delay: int = 0
    length: int = 0


@dataclass(frozen=True)
class _Motion:
    """A caption's transition as it applies to one Dialogue line of it.

    A caption is drawn as several lines in turn, one per spoken word, and a
    transition can outlast the first of them. Each line therefore takes up
    the transition where the one before left it, so it runs on unbroken
    whatever the words' timing (D-196).
    """

    #: An ``\fade`` tag, or nothing.
    fade: str = ""
    #: The block's scale when this line starts, and how long until it is
    #: full size, in ms from the line's start.
    scale_from: float = 1.0
    scale_ms: int = 0
    accel: float = 1.0
    #: How far below its place the block still is when this line starts, in
    #: pixels, and how long until it is in place.
    lift_from: float = 0.0
    lift_ms: int = 0

    @property
    def scaling(self) -> bool:
        return self.scale_ms > 0 and self.scale_from != 1.0

    @property
    def lifting(self) -> bool:
        return self.lift_ms > 0 and self.lift_from > 0


_NO_TIMING = _Timing()
_STILL = _Motion()

#: How much smaller a caption starts when it pops in, and how much larger when
#: it zooms in.
_POP_FROM = 0.82
_ZOOM_FROM = 1.18

#: How far a sliding caption rises, as a fraction of its font size.
_SLIDE_RISE = 0.6

#: An ``\t`` acceleration below 1 starts fast and settles: an ease-out.
_EASE_OUT = 0.5


def _centis(seconds: float) -> float:
    """A time as ASS stores it, to the centisecond: the clock that the times
    within a line (``\\t``, ``\\fade``, ``\\kf``) are counted from."""
    return round(max(0.0, seconds) * 100) / 100


def _motion(
    style: CaptionStyle,
    start: float,
    end: float,
    page_start: float,
    page_end: float,
    height: int,
) -> _Motion:
    """The transition, for the Dialogue line from ``start`` to ``end`` of a
    caption shown from ``page_start`` to ``page_end``."""
    kind = style.transition
    if kind is CaptionTransition.CUT:
        return _STILL

    page_ms = (_centis(page_end) - _centis(page_start)) * 1000
    if page_ms <= 0:
        return _STILL
    # A short caption keeps most of its time still: the transitions take at
    # most 40% of it coming in and 30% going.
    in_ms = min(float(style.transition_ms), page_ms * 0.4)
    out_ms = min(style.transition_ms * 0.7, page_ms * 0.3)
    out_start = page_ms - out_ms

    t0 = (_centis(start) - _centis(page_start)) * 1000
    t1 = (_centis(end) - _centis(page_start)) * 1000
    duration = max(0.0, t1 - t0)

    def alpha(t: float) -> int:
        coming = 255 * (1 - t / in_ms) if in_ms > 0 and t < in_ms else 0.0
        going = 255 * (t - out_start) / out_ms if out_ms > 0 and t > out_start else 0.0
        return round(min(255.0, max(coming, going, 0.0)))

    t2 = min(max(in_ms - t0, 0.0), duration)
    t3 = min(max(out_start - t0, t2), duration)
    a1, a2, a3 = alpha(t0), alpha(t0 + t2), alpha(t1)
    fade = ""
    if a1 or a2 or a3:
        fade = f"\\fade({a1},{a2},{a3},0,{round(t2)},{round(t3)},{round(duration)})"

    remaining = max(0.0, in_ms - t0)
    if remaining <= 0:
        return _Motion(fade=fade)
    # One line holding the whole entrance can ease it; one shared between
    # lines moves evenly, so the pieces join without a jolt.
    whole = t0 == 0 and duration >= in_ms
    accel = _EASE_OUT if whole else 1.0
    progress = t0 / in_ms

    if kind in (CaptionTransition.POP, CaptionTransition.ZOOM):
        origin = _POP_FROM if kind is CaptionTransition.POP else _ZOOM_FROM
        return _Motion(
            fade=fade,
            scale_from=origin + (1 - origin) * progress,
            scale_ms=round(remaining),
            accel=accel,
        )
    if kind is CaptionTransition.SLIDE:
        rise = _SLIDE_RISE * style.font_size_px(height)
        return _Motion(fade=fade, lift_from=rise * (1 - progress), lift_ms=round(remaining))
    return _Motion(fade=fade)


@dataclass(frozen=True)
class _Placement:
    """Where a caption block is anchored: an ASS alignment and the point it
    is aligned to, the same point libass finds from the margins."""

    alignment: int
    x: float
    y: float


def _placement(style: CaptionStyle, width: int, height: int, *, top: bool) -> _Placement:
    if style.anchor_y is not None:
        return _Placement(5, width / 2, style.anchor_y * height)
    margin = style.margin_vertical_px(width, height)
    if top or style.position is CaptionPosition.TOP:
        return _Placement(8, width / 2, margin)
    if style.position is CaptionPosition.CENTER:
        return _Placement(5, width / 2, height / 2)
    return _Placement(2, width / 2, height - margin)


def _alpha_byte(colour: str) -> str:
    """The transparency byte of an ``&HAABBGGRR`` colour, as an ASS alpha."""
    digits = colour.removeprefix("&H").rjust(8, "0")
    return f"&H{digits[:2]}&"


def _number(value: float) -> str:
    """A number for an override tag: whole where it can be, short where not."""
    return f"{value:.0f}" if abs(value - round(value)) < 1e-6 else f"{value:.2f}"


def _render_caption(
    words: tuple[Word, ...],
    lines: list[list[int]],
    highlighted: int | None,
    style: CaptionStyle,
    width: int = 1920,
    *,
    animation: CaptionAnimation = CaptionAnimation.HIGHLIGHT,
    emphasis: frozenset[int] = frozenset(),
    timing: _Timing = _NO_TIMING,
    motion: _Motion = _STILL,
) -> str:
    """Build the text of one Dialogue line.

    Args:
        words: The scene's words.
        lines: Word indices grouped into display lines.
        highlighted: Index of the word being spoken, or ``None`` for none.
        style: Caption styling.
        animation: How the spoken word is shown (D-196).
        emphasis: Words drawn larger and in colour.
        timing: Where the spoken word falls within this line.
        motion: The transition, for its scale.

    Returns:
        ASS text with inline tags.
    """
    rendered_lines: list[str] = []
    plain_lines: list[str] = []
    primary, accent = style.primary_color, style.highlight_color
    sized = bool(emphasis) or motion.scaling
    hidden = "\\1a&HFF&\\3a&HFF&"
    shown = "\\1a&H00&\\3a&H00&"
    if style.backing is CaptionBacking.OUTLINE:
        # Without a box, the shadow is drawn in the back colour, and must
        # come and go with its word.
        hidden += "\\4a&HFF&"
        shown += f"\\4a{_alpha_byte(style.box_color)}"

    for line in lines:
        parts: list[str] = []
        for index in line:
            token = words[index].text.strip()
            if style.uppercase:
                token = token.upper()
            escaped = _escape_text(token)
            spoken = highlighted is not None and index == highlighted
            past = highlighted is not None and index < highlighted
            stressed = index in emphasis

            tags = ""
            lead = ""
            if animation is CaptionAnimation.KARAOKE and highlighted is not None:
                if spoken:
                    lead = f"{{\\k{round(timing.delay / 10)}}}"
                    tags = f"\\1c{accent}\\2c{primary}\\kf{max(1, round(timing.length / 10))}"
                elif past:
                    tags = f"\\k0\\1c{accent}\\2c{accent}"
                else:
                    tags = f"\\k0\\1c{primary}\\2c{primary}"
            else:
                coloured = stressed or (spoken and animation is CaptionAnimation.HIGHLIGHT)
                tags = f"\\c{accent if coloured else primary}"
                if animation is CaptionAnimation.TYPEWRITER and highlighted is not None:
                    if spoken:
                        reveal = min(80, max(1, timing.length))
                        tags += (
                            f"{hidden}\\t({timing.delay},{timing.delay + reveal},{shown})"
                        )
                    elif past:
                        tags += shown
                    else:
                        tags += hidden

            if sized:
                full = 100 * (style.emphasis_scale if stressed else 1.0)
                if motion.scaling:
                    begin = _number(full * motion.scale_from)
                    end = _number(full)
                    tags += (
                        f"\\fscx{begin}\\fscy{begin}\\t(0,{motion.scale_ms},"
                        f"{_number(motion.accel)},\\fscx{end}\\fscy{end})"
                    )
                else:
                    tags += f"\\fscx{_number(full)}\\fscy{_number(full)}"

            parts.append(f"{lead}{{{tags}}}{escaped}")

        rendered_lines.append(" ".join(parts))

        # The plain text is what a backing box is sized to, so it is
        # tracked separately: measuring the tagged string would count
        # colour tags as visible characters.
        plain_lines.append(
            " ".join(
                words[i].text.strip().upper()
                if style.uppercase
                else words[i].text.strip()
                for i in line
            )
        )

    padded = _pad_lines(rendered_lines, plain_lines, style, width)
    return "\\N".join(padded)


def _line_prefix(placement: _Placement, motion: _Motion, *, placed: bool) -> str:
    """The tags that place and fade a whole Dialogue line."""
    tags = motion.fade
    if motion.lifting:
        x, y = _number(placement.x), _number(placement.y)
        below = _number(placement.y + motion.lift_from)
        tags = (
            f"\\an{placement.alignment}\\move({x},{below},{x},{y},0,{motion.lift_ms})"
            + tags
        )
    elif placed:
        tags = (
            f"\\an{placement.alignment}\\pos({_number(placement.x)},{_number(placement.y)})"
            + tags
        )
    return f"{{{tags}}}" if tags else ""


def _script_info(width: int, height: int) -> str:
    """The Script Info header.

    ``PlayResX``/``PlayResY`` define the coordinate space that font sizes and
    margins are interpreted in. Setting them to the real frame size means sizes
    computed in pixels land exactly as intended.
    """
    return textwrap.dedent(f"""\
        [Script Info]
        ; Generated by Voxframe
        ScriptType: v4.00+
        PlayResX: {width}
        PlayResY: {height}
        WrapStyle: 2
        ScaledBorderAndShadow: yes
        YCbCr Matrix: TV.709
        """)


def _styles_block(style: CaptionStyle, width: int, height: int) -> str:
    """The V4+ Styles section."""
    values = {
        "Name": "Voxframe",
        "Fontname": style.font_family,
        "Fontsize": style.font_size_px(height),
        "PrimaryColour": style.primary_color,
        "SecondaryColour": style.primary_color,
        "OutlineColour": style.outline_color,
        "BackColour": style.box_color,
        "Bold": -1 if style.font_weight >= 600 else 0,
        "Italic": 0,
        "Underline": 0,
        "StrikeOut": 0,
        "ScaleX": 100,
        "ScaleY": 100,
        "Spacing": 0,
        "Angle": 0,
        # 4 draws a box behind the text; 1 draws outline and shadow only.
        # `band` also uses 4 and achieves its full width through padding.
        "BorderStyle": 1 if style.backing is CaptionBacking.OUTLINE else 4,
        "Outline": style.outline_width,
        # Without a backing the text needs a drop shadow as well as an
        # outline, or it disappears into busy imagery (D-060).
        "Shadow": (
            max(style.shadow_depth, 2.0)
            if style.backing is CaptionBacking.OUTLINE
            else style.shadow_depth
        ),
        "Alignment": _ALIGNMENT[style.position],
        "MarginL": style.margin_horizontal_px(width),
        "MarginR": style.margin_horizontal_px(width),
        "MarginV": style.margin_vertical_px(width, height),
        "Encoding": 1,
    }

    # The same captions anchored at the top, for a scene where the speaker's
    # face reaches down into the caption area (D-193).
    top = {**values, "Name": TOP_STYLE, "Alignment": _ALIGNMENT[CaptionPosition.TOP]}
    styles = [values, top]
    if style.effective_animation in WORD_ANIMATIONS:
        # Words placed one by one carry the same outline; a box behind them
        # is drawn as a shape (D-196).
        styles.append({**values, "Name": WORD_STYLE, "BorderStyle": 1, "Alignment": 5})
        styles.append({
            **values,
            "Name": BOX_STYLE,
            "PrimaryColour": style.box_color,
            "BorderStyle": 1,
            "Outline": 0,
            "Shadow": 0,
            "Alignment": 5,
        })
    return (
        "[V4+ Styles]\n"
        f"Format: {', '.join(_STYLE_FIELDS)}\n"
        + "".join(
            f"Style: {','.join(str(entry[field]) for field in _STYLE_FIELDS)}\n"
            for entry in styles
        )
    )


def _dialogue(
    start: float, end: float, text: str, style: str = "Voxframe", layer: int = 0
) -> str:
    """One Dialogue line, with fields in the declared order."""
    values = {
        "Layer": str(layer),
        "Start": format_timestamp(start),
        "End": format_timestamp(end),
        "Style": style,
        "Name": "",
        "MarginL": "0",
        "MarginR": "0",
        "MarginV": "0",
        "Effect": "",
        "Text": text,
    }
    return "Dialogue: " + ",".join(values[field] for field in _EVENT_FIELDS)


@dataclass(frozen=True)
class _Page:
    """One caption on screen: its words, in lines, and when it shows."""

    scene: Scene
    lines: list[list[int]]
    start: float
    end: float

    @property
    def visible(self) -> list[int]:
        return [index for line in self.lines for index in line]


def _spans(page: _Page, *, from_page_start: bool = True) -> list[tuple[float, float, int]]:
    """When each word of a page is the one being spoken.

    The first word's span opens with the page, so a page never opens blank,
    unless ``from_page_start`` is off. Each span holds until the next word
    begins, so a pause keeps the caption on screen instead of blanking it.
    """
    visible = page.visible
    spans: list[tuple[float, float, int]] = []
    for position, index in enumerate(visible):
        word = page.scene.words[index]
        start = page.start if position == 0 and from_page_start else word.start
        if position + 1 < len(visible):
            end = page.scene.words[visible[position + 1]].start
        else:
            end = page.end
        start, end = max(start, page.start), min(end, page.end)
        if end > start:
            spans.append((start, end, index))
    return spans


def _line_events(
    page: _Page,
    style: CaptionStyle,
    style_name: str,
    animation: CaptionAnimation,
    placement: _Placement,
    width: int,
    height: int,
) -> list[str]:
    """A page drawn as libass lays out lines: highlight, karaoke, typewriter
    and plain."""
    scene = page.scene
    emphasis = frozenset(scene.emphasis)
    placed = style.anchor_y is not None

    if animation is CaptionAnimation.PLAIN:
        spans: list[tuple[float, float, int | None]] = [(page.start, page.end, None)]
    else:
        spans = list(_spans(page))

    events: list[str] = []
    for start, end, index in spans:
        timing = _Timing()
        if index is not None:
            word = scene.words[index]
            delay = max(0.0, word.start - start)
            length = max(0.0, min(word.end, end) - max(word.start, start))
            timing = _Timing(round(delay * 1000), round(length * 1000))
        motion = _motion(style, start, end, page.start, page.end, height)
        text = _render_caption(
            scene.words, page.lines, index, style, width,
            animation=animation, emphasis=emphasis, timing=timing, motion=motion,
        )
        prefix = _line_prefix(placement, motion, placed=placed)
        events.append(_dialogue(start, end, prefix + text, style_name))
    return events


def build_ass(
    scenes: tuple[Scene, ...],
    style: CaptionStyle,
    width: int,
    height: int,
    fps: float,
    *,
    top_scenes: frozenset[int] = frozenset(),
    animations: Mapping[int, CaptionAnimation] | None = None,
    anchors: Mapping[int, float] | None = None,
    extra_styles: tuple[str, ...] = (),
    extra_events: tuple[str, ...] = (),
) -> str:
    """Build a complete ASS subtitle document.

    Args:
        scenes: Scenes to caption. Silent scenes are skipped.
        style: Caption styling.
        width: Frame width in pixels. **Must** match the video this will be
            burned into.
        height: Frame height in pixels. Must match the video.
        fps: Frame rate, for converting scene frames to seconds.
        top_scenes: Indices of scenes whose captions go to the top of the
            frame instead, clear of the speaker's face (D-193).
        animations: Scenes given their own animation, by index; the rest use
            the style's (D-196).
        anchors: Scenes whose captions sit at their own height, by index, as
            a fraction of the frame: a split screen's divider (D-197).
        extra_styles: Further styles, as the values of a ``Style:`` line, and
        extra_events: Dialogue lines drawn with the captions: the pop-ups
            (D-198).

    Returns:
        The ASS document.

    Note:
        ``width`` and ``height`` become ``PlayResX``/``PlayResY``, the
        coordinate space libass interprets font sizes and margins in. If they
        disagree with the actual video dimensions, libass scales everything by
        the ratio: captions built for 1080p and burned into a 540p video come
        out at half the intended size, with no warning. Callers must pass the
        real output dimensions, which is why
        :func:`~voxframe.render.compose.captioned.render_captioned_video`
        derives both from one source.
    """
    from voxframe.render.captions.words import word_events

    animations = animations or {}
    word_drawn = any(
        animations.get(scene.index, style.effective_animation) in WORD_ANIMATIONS
        for scene in scenes
    )
    # The styles block declares the word styles whenever any scene needs them.
    header_style = (
        style.model_copy(update={"animation": CaptionAnimation.POP}) if word_drawn else style
    )
    styles = _styles_block(header_style, width, height)
    styles += "".join(f"Style: {line}\n" for line in extra_styles)
    parts = [_script_info(width, height), styles]

    events = [f"[Events]\nFormat: {', '.join(_EVENT_FIELDS)}"]
    measure = _TextWidth.for_frame(style, height) if word_drawn else None

    for scene in scenes:
        if scene.is_silent:
            continue

        emphasis = frozenset(scene.emphasis)
        all_lines = _wrap_words(scene.words, style, width, height, emphasis)
        if not all_lines:
            continue

        animation = animations.get(scene.index, style.effective_animation)
        if animation in WORD_ANIMATIONS and measure is None:
            # Without the fonts nothing can be measured to place words by.
            animation = CaptionAnimation.HIGHLIGHT
        top = scene.index in top_scenes
        style_name = TOP_STYLE if top else "Voxframe"
        scene_style = style
        if anchors and scene.index in anchors:
            scene_style = style.model_copy(update={"anchor_y": anchors[scene.index]})
        placement = _placement(scene_style, width, height, top=top)

        pages = _paginate(all_lines, style)
        scene_start = scene.start_seconds(fps)
        scene_end = scene.end_seconds(fps)

        for page_number, lines in enumerate(pages):
            visible = [index for line in lines for index in line]
            if not visible:
                continue

            # A page runs from its first word until the next page's first word,
            # so the scene's whole span is covered with no gap and no overlap.
            first_page = page_number == 0
            last_page = page_number == len(pages) - 1

            page_start = scene_start if first_page else scene.words[visible[0]].start

            if last_page:
                page_end = scene_end
            else:
                next_visible = pages[page_number + 1][0][0]
                page_end = scene.words[next_visible].start

            page_start = max(page_start, scene_start)
            page_end = min(page_end, scene_end)
            if page_end <= page_start:
                continue

            page = _Page(scene, lines, page_start, page_end)
            if animation in WORD_ANIMATIONS:
                assert measure is not None
                events.extend(
                    word_events(page, scene_style, animation, placement, measure, width, height)
                )
            else:
                events.extend(
                    _line_events(
                        page, scene_style, style_name, animation, placement, width, height
                    )
                )

    events.extend(extra_events)
    parts.append("\n".join(events) + "\n")
    return "\n".join(parts)


def write_ass(
    path: Path,
    scenes: tuple[Scene, ...],
    style: CaptionStyle,
    width: int,
    height: int,
    fps: float,
    *,
    top_scenes: frozenset[int] = frozenset(),
    animations: Mapping[int, CaptionAnimation] | None = None,
    anchors: Mapping[int, float] | None = None,
) -> Path:
    """Write an ASS subtitle file.

    Written as UTF-8 without a BOM: libass handles UTF-8 natively, but a BOM
    can appear as a stray glyph in the first caption.

    Returns:
        The path written.
    """
    content = build_ass(
        scenes, style, width, height, fps, top_scenes=top_scenes, animations=animations,
        anchors=anchors,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path
