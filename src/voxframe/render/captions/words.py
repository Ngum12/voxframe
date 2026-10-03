"""Captions drawn word by word: pop, bounce and spotlight (D-196).

libass lays a caption out as lines, and a word that grows or jumps inside a
line pushes the words beside it. So these animations place every word
themselves, each at the point libass's own layout would put it: the same
font, measured the same way, and a line exactly one font size tall, as
measured from libass's output. A word can then grow from its own centre,
jump, or have a box glide behind it, and nothing else on screen moves.

Every change is timed by the words' own timestamps, as all captions are, so
none can drift from the voice.
"""

from __future__ import annotations

from dataclasses import dataclass

from voxframe.config.style import CaptionAnimation, CaptionBacking, CaptionStyle
from voxframe.render.captions.ass import (
    _PAD_CHAR,
    BOX_STYLE,
    WORD_STYLE,
    _alpha_byte,
    _dialogue,
    _escape_text,
    _Motion,
    _motion,
    _number,
    _Page,
    _Placement,
    _spans,
    _TextWidth,
)

__all__ = ["word_events"]

#: Layers, bottom to top: the caption's box, the spotlight, the words.
_BOX_LAYER, _SPOT_LAYER, _WORD_LAYER = 0, 1, 2

#: How high a bouncing word jumps, as a fraction of the font size, and how
#: long it takes to go up and to come down, in seconds.
_BOUNCE_HEIGHT = 0.22
_BOUNCE_UP = 0.09
_BOUNCE_DOWN = 0.11

#: A popping word grows from this fraction of its size, overshoots to the
#: next, and settles, in these many milliseconds.
_POP_FROM = 0.4
_POP_OVER = 1.1
_POP_GROW_MS = 80
_POP_SETTLE_MS = 70

#: How long the spotlight takes to glide from one word to the next, in ms.
_GLIDE_MS = 70

#: The colour of the spoken word on the spotlight: near black, for contrast.
_ON_SPOTLIGHT = "&H00101010"


@dataclass(frozen=True)
class _Word:
    """One word, placed: its centre, its advance, and its scale."""

    index: int
    text: str
    x: float
    y: float
    width: float
    scale: float


@dataclass(frozen=True)
class _Line:
    words: tuple[_Word, ...]
    x: float
    y: float
    width: float
    height: float


def _layout(
    page: _Page, style: CaptionStyle, placement: _Placement, measure: _TextWidth, height: int
) -> list[_Line]:
    """Place each word of a page where libass's line layout would put it."""
    size = style.font_size_px(height)
    emphasis = frozenset(page.scene.emphasis)
    space = measure.advance(" ")
    rows: list[tuple[list[tuple[int, str, float, float]], float, float]] = []
    for line in page.lines:
        entries = []
        for index in line:
            token = page.scene.words[index].text.strip()
            scale = style.emphasis_scale if index in emphasis else 1.0
            entries.append((index, token, measure.advance(token) * scale, scale))
        line_width = sum(entry[2] for entry in entries) + space * (len(entries) - 1)
        line_height = size * max(entry[3] for entry in entries)
        rows.append((entries, line_width, line_height))

    block = sum(row[2] for row in rows)
    if placement.alignment == 2:
        top = placement.y - block
    elif placement.alignment == 8:
        top = placement.y
    else:
        top = placement.y - block / 2

    lines: list[_Line] = []
    for entries, line_width, line_height in rows:
        y = top + line_height / 2
        x = placement.x - line_width / 2
        placed = []
        for index, token, advance, scale in entries:
            text = token.upper() if style.uppercase else token
            placed.append(_Word(index, text, x + advance / 2, y, advance, scale))
            x += advance + space
        lines.append(_Line(tuple(placed), placement.x, y, line_width, line_height))
        top += line_height
    return lines


def _rounded_rect(width: float, height: float, radius: float) -> str:
    """An ASS drawing of a rounded rectangle, ``width`` by ``height``."""
    w, h = round(width), round(height)
    r = max(0, min(round(radius), w // 2, h // 2))
    return (
        f"m {r} 0 l {w - r} 0 b {w} 0 {w} 0 {w} {r} l {w} {h - r} "
        f"b {w} {h} {w} {h} {w - r} {h} l {r} {h} b 0 {h} 0 {h} 0 {h - r} "
        f"l 0 {r} b 0 0 0 0 {r} 0"
    )


class _Emitter:
    """Writes the Dialogue lines of one page, each taking up the caption's
    transition where it stands at that line's start."""

    def __init__(self, page: _Page, style: CaptionStyle, height: int, centre: tuple[float, float]):
        self.page = page
        self.style = style
        self.height = height
        self.centre = centre
        self.events: list[str] = []

    def emit(
        self,
        start: float,
        end: float,
        *,
        layer: int,
        style_name: str,
        x: float,
        y: float,
        scale: float,
        tags: str,
        text: str,
        own_move: str = "",
        own_scale: str = "",
    ) -> None:
        """One Dialogue line, drawn at ``(x, y)``.

        ``own_move`` and ``own_scale`` are the element's own animation (a
        bounce, a pop, a glide). Within the caption's entrance the entrance
        moves and scales it instead, so the whole block comes in as one.
        """
        if end - start < 0.005:
            return
        page = self.page
        motion: _Motion = _motion(self.style, start, end, page.start, page.end, self.height)
        full = 100 * scale
        if motion.lifting:
            position = (
                f"\\move({_number(x)},{_number(y + motion.lift_from)},"
                f"{_number(x)},{_number(y)},0,{motion.lift_ms})"
            )
            sizing = own_scale or f"\\fscx{_number(full)}\\fscy{_number(full)}"
        elif motion.scaling:
            cx, cy = self.centre
            sx = cx + (x - cx) * motion.scale_from
            sy = cy + (y - cy) * motion.scale_from
            position = (
                f"\\move({_number(sx)},{_number(sy)},{_number(x)},{_number(y)},0,{motion.scale_ms})"
            )
            begin = _number(full * motion.scale_from)
            sizing = (
                f"\\fscx{begin}\\fscy{begin}\\t(0,{motion.scale_ms},{_number(motion.accel)},"
                f"\\fscx{_number(full)}\\fscy{_number(full)})"
            )
        else:
            position = own_move or f"\\pos({_number(x)},{_number(y)})"
            sizing = own_scale or f"\\fscx{_number(full)}\\fscy{_number(full)}"
        self.events.append(
            _dialogue(
                start, end, f"{{\\an5{position}{motion.fade}{sizing}{tags}}}{text}",
                style_name, layer,
            )
        )


def word_events(
    page: _Page,
    style: CaptionStyle,
    animation: CaptionAnimation,
    placement: _Placement,
    measure: _TextWidth,
    width: int,
    height: int,
) -> list[str]:
    """The Dialogue lines of one page, its words placed one by one."""
    lines = _layout(page, style, placement, measure, height)
    if not lines:
        return []
    size = style.font_size_px(height)
    top = lines[0].y - lines[0].height / 2
    bottom = lines[-1].y + lines[-1].height / 2
    emitter = _Emitter(page, style, height, (placement.x, (top + bottom) / 2))
    emphasis = frozenset(page.scene.emphasis)
    primary, accent = style.primary_color, style.highlight_color

    placed = {word.index: word for line in lines for word in line.words}
    spans = _spans(page, from_page_start=animation is not CaptionAnimation.POP)
    if not spans:
        return []

    # The box behind each line, drawn as libass would draw it, with rounded
    # corners. Words that pop in bring it with the first of them, so a
    # caption never opens as an empty box.
    if style.backing is not CaptionBacking.OUTLINE:
        box_start = spans[0][0]
        pad_x = measure.advance(_PAD_CHAR * style.box_padding_chars)
        pad_y = style.outline_width
        for line in lines:
            if style.backing is CaptionBacking.BAND:
                box_width = width - 2 * style.margin_horizontal_px(width)
            else:
                box_width = line.width + 2 * pad_x
            box_height = line.height + 2 * pad_y
            emitter.emit(
                box_start, page.end, layer=_BOX_LAYER, style_name=BOX_STYLE,
                x=line.x, y=line.y, scale=1.0, tags="",
                text=f"{{\\p1}}{_rounded_rect(box_width, box_height, box_height * 0.22)}",
            )

    previous: _Word | None = None

    for start, end, index in spans:
        word = placed.get(index)
        if word is None:
            continue
        text = _escape_text(word.text)
        stressed = index in emphasis
        resting = f"\\c{accent if stressed else primary}"
        full = 100 * word.scale

        def draw(
            a: float, b: float, tags: str, own_move: str = "", own_scale: str = "",
            *, at: _Word = word, body: str = text,
        ) -> None:
            emitter.emit(
                a, b, layer=_WORD_LAYER, style_name=WORD_STYLE, x=at.x, y=at.y,
                scale=at.scale, tags=tags, text=body, own_move=own_move, own_scale=own_scale,
            )

        if animation is not CaptionAnimation.POP:
            draw(page.start, start, resting)
        draw(end, page.end, resting)

        if animation is CaptionAnimation.POP:
            low, over = _number(full * _POP_FROM), _number(full * _POP_OVER)
            grow = (
                f"\\fscx{low}\\fscy{low}\\t(0,{_POP_GROW_MS},\\fscx{over}\\fscy{over})"
                f"\\t({_POP_GROW_MS},{_POP_GROW_MS + _POP_SETTLE_MS},"
                f"\\fscx{_number(full)}\\fscy{_number(full)})"
            )
            shadow = _alpha_byte(style.box_color)
            appear = (
                f"\\c{accent}\\1a&HFF&\\3a&HFF&\\4a&HFF&"
                f"\\t(0,60,\\1a&H00&\\3a&H00&\\4a{shadow})"
            )
            draw(start, end, appear, own_scale=grow)
        elif animation is CaptionAnimation.BOUNCE:
            lift = _BOUNCE_HEIGHT * size
            x, y, high = _number(word.x), _number(word.y), _number(word.y - lift)
            # Lit from the start of its turn, it jumps when it is said.
            said = min(max(start, page.scene.words[index].start), end)
            draw(start, said, f"\\c{accent}")
            up_end = min(said + _BOUNCE_UP, end)
            down_end = min(up_end + _BOUNCE_DOWN, end)
            up_ms = round((up_end - said) * 1000)
            down_ms = round((down_end - up_end) * 1000)
            rise = f"\\move({x},{y},{x},{high},0,{up_ms})"
            fall = f"\\move({x},{high},{x},{y},0,{down_ms})"
            draw(said, up_end, f"\\c{accent}", own_move=rise)
            draw(up_end, down_end, f"\\c{accent}", own_move=fall)
            draw(down_end, end, f"\\c{accent}")
        else:  # spotlight
            spot_w = word.width + 0.3 * size
            spot_h = size * word.scale * 1.08
            glide_move = glide_scale = ""
            # The box glides along a line; to the next line it grows in
            # place, rather than sweep across both.
            gliding = previous is not None and abs(previous.y - word.y) < 1
            # The word darkens as the box arrives under it, never before.
            darken = f"\\c{resting[2:]}\\t(0,{_GLIDE_MS},\\c{_ON_SPOTLIGHT})"
            draw(start, end, darken)
            if previous is not None and gliding:
                ratio_x = _number(100 * (previous.width + 0.3 * size) / spot_w)
                ratio_y = _number(100 * previous.scale / word.scale)
                glide_move = (
                    f"\\move({_number(previous.x)},{_number(previous.y)},"
                    f"{_number(word.x)},{_number(word.y)},0,{_GLIDE_MS})"
                )
                glide_scale = (
                    f"\\fscx{ratio_x}\\fscy{ratio_y}\\t(0,{_GLIDE_MS},\\fscx100\\fscy100)"
                )
            else:
                glide_scale = f"\\fscx60\\fscy60\\t(0,{_GLIDE_MS},\\fscx100\\fscy100)"
            emitter.emit(
                start, end, layer=_SPOT_LAYER, style_name=BOX_STYLE, x=word.x, y=word.y,
                scale=1.0, tags=f"\\c{accent}\\1a&H00&",
                text=f"{{\\p1}}{_rounded_rect(spot_w, spot_h, spot_h * 0.25)}",
                own_move=glide_move, own_scale=glide_scale,
            )
        previous = word

    return emitter.events
