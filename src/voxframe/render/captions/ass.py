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
from pathlib import Path

from voxframe.config.style import CaptionBacking, CaptionPosition, CaptionStyle
from voxframe.models.scene import Scene
from voxframe.models.transcript import Word

__all__ = ["build_ass", "format_timestamp", "write_ass"]

#: ASS alignment codes (numpad layout): 2 = bottom centre, 5 = middle, 8 = top.
#: The style for captions moved to the top of a frame (D-193).
TOP_STYLE = "VoxframeTop"

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


def _wrap_words(words: tuple[Word, ...], style: CaptionStyle) -> list[list[int]]:
    """Group word indices into display lines that fit the caption box.

    Returns:
        Lists of word indices, one per line. Every word is placed; limiting how
        many lines are on screen at once is :func:`_paginate`'s job.
    """
    if not words:
        return []

    lines: list[list[int]] = []
    current: list[int] = []
    current_length = 0

    for index, word in enumerate(words):
        token = word.text.strip()
        if not token:
            continue

        addition = len(token) + (1 if current else 0)

        if current and current_length + addition > style.max_chars_per_line:
            lines.append(current)
            current = [index]
            current_length = len(token)
        else:
            current.append(index)
            current_length += addition

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


def _render_caption(
    words: tuple[Word, ...],
    lines: list[list[int]],
    highlighted: int | None,
    style: CaptionStyle,
    width: int = 1920,
) -> str:
    """Build the text of one Dialogue line.

    Args:
        words: The scene's words.
        lines: Word indices grouped into display lines.
        highlighted: Index of the word to highlight, or ``None`` for no
            highlight.
        style: Caption styling.

    Returns:
        ASS text with inline colour tags.
    """
    rendered_lines: list[str] = []
    plain_lines: list[str] = []

    for line in lines:
        parts: list[str] = []
        for index in line:
            token = words[index].text.strip()
            if style.uppercase:
                token = token.upper()
            escaped = _escape_text(token)

            if highlighted is not None and index == highlighted:
                parts.append(f"{{\\c{style.highlight_color}}}{escaped}")
            else:
                parts.append(f"{{\\c{style.primary_color}}}{escaped}")

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
    return (
        "[V4+ Styles]\n"
        f"Format: {', '.join(_STYLE_FIELDS)}\n"
        f"Style: {','.join(str(values[field]) for field in _STYLE_FIELDS)}\n"
        f"Style: {','.join(str(top[field]) for field in _STYLE_FIELDS)}\n"
    )


def _dialogue(start: float, end: float, text: str, style: str = "Voxframe") -> str:
    """One Dialogue line, with fields in the declared order."""
    values = {
        "Layer": "0",
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


def build_ass(
    scenes: tuple[Scene, ...],
    style: CaptionStyle,
    width: int,
    height: int,
    fps: float,
    *,
    top_scenes: frozenset[int] = frozenset(),
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
    parts = [_script_info(width, height), _styles_block(style, width, height)]

    events = [f"[Events]\nFormat: {', '.join(_EVENT_FIELDS)}"]

    for scene in scenes:
        if scene.is_silent:
            continue

        all_lines = _wrap_words(scene.words, style)
        if not all_lines:
            continue

        pages = _paginate(all_lines, style)
        style_name = TOP_STYLE if scene.index in top_scenes else "Voxframe"
        scene_start = scene.start_seconds(fps)
        scene_end = scene.end_seconds(fps)

        for page_number, page in enumerate(pages):
            visible = [index for line in page for index in line]
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

            if not style.highlight_enabled:
                text = _render_caption(scene.words, page, None, style, width)
                events.append(_dialogue(page_start, page_end, text, style_name))
                continue

            # One Dialogue line per word, each highlighting a different word.
            # Consecutive lines abut exactly, so there is no flicker between
            # them.
            for position, index in enumerate(visible):
                word = scene.words[index]

                # The first caption of a page appears with the page, not with
                # its first word, so a page never opens blank.
                start_time = page_start if position == 0 else word.start

                # Hold until the next word begins, so a pause keeps the caption
                # on screen instead of blanking it.
                if position + 1 < len(visible):
                    end_time = scene.words[visible[position + 1]].start
                else:
                    end_time = page_end

                start_time = max(start_time, page_start)
                end_time = min(end_time, page_end)
                if end_time <= start_time:
                    continue

                text = _render_caption(scene.words, page, index, style, width)
                events.append(_dialogue(start_time, end_time, text, style_name))

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
) -> Path:
    """Write an ASS subtitle file.

    Written as UTF-8 without a BOM: libass handles UTF-8 natively, but a BOM
    can appear as a stray glyph in the first caption.

    Returns:
        The path written.
    """
    content = build_ass(scenes, style, width, height, fps, top_scenes=top_scenes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path
