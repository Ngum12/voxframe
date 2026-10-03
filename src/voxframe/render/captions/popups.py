"""Pop-ups drawn by libass: text, shapes, counters and the progress bar (D-198).

Drawn into the same document as the captions, so the studio shows them live
exactly as the video will (D-196). Stickers and images are pictures, which
libass cannot draw; FFmpeg lays those over the frame (``compose/stickers.py``).

Every pop-up appears when its word is said, comes on screen as chosen (pop,
slide, bounce, fade or simply there) and fades as it goes.
"""

from __future__ import annotations

from functools import lru_cache

from voxframe.plan.overlays import (
    Entrance,
    Overlay,
    OverlayKind,
    ShapeKind,
    TextLook,
    counter_parts,
)
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.captions.ass import _dialogue, _escape_text, _number

__all__ = ["POP_STYLE", "popup_events", "popup_styles"]

#: The style pop-ups are drawn in: centred, bold, no border of its own.
POP_STYLE = "VoxframePop"

#: Pop-ups sit above the captions: they are placed deliberately.
_BOX_LAYER, _TEXT_LAYER = 10, 11

#: How long the hook title stays, in seconds (D-199).
HOOK_SECONDS = 2.6

#: How long a pop-up takes to fade as it goes, in ms.
_OUT_MS = 150

#: A counter counts up for at most this long, in seconds, a step per frame
#: at this rate.
_COUNT_SECONDS = 1.0
_COUNT_STEPS_PER_SECOND = 20


def _bgr(rgb: tuple[int, int, int], alpha: int = 0) -> str:
    red, green, blue = rgb
    return f"&H{alpha:02X}{blue:02X}{green:02X}{red:02X}&"


def _ink_on(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Text that reads on a box of ``rgb``: near black on light, white on dark."""
    red, green, blue = rgb
    light = 0.299 * red + 0.587 * green + 0.114 * blue > 150
    return (17, 17, 17) if light else (255, 255, 255)


@lru_cache(maxsize=16)
def _font(px: int):  # type: ignore[no-untyped-def]
    from PIL import ImageFont

    from voxframe.assets import fonts_dir

    font = ImageFont.truetype(str(fonts_dir() / "Inter-Bold.ttf"), 100)
    ascent, descent = font.getmetrics()
    return font, px / (ascent + descent) / 100 * 1.007


def _width(text: str, px: int) -> float:
    """How wide libass draws ``text`` in bold Inter at ``px``."""
    try:
        font, scale = _font(px)
    except Exception:
        return len(text) * px * 0.55
    return float(font.getlength(text)) * 100 * scale


def _wrap(text: str, px: int, room: float) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if lines and _width(f"{lines[-1]} {word}", px) <= room:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return lines or [""]


def _rounded(w: float, h: float, r: float) -> str:
    from voxframe.render.captions.words import _rounded_rect

    return _rounded_rect(w, h, r)


class _Timeline:
    """The Dialogue lines of one pop-up: its entrance, then it at rest, then
    its fade, each part a line of its own where a part moves."""

    def __init__(
        self, start: float, end: float, x: float, y: float, height: int, entrance: Entrance
    ) -> None:
        self.start, self.end, self.x, self.y = start, end, x, y
        self.height = height
        self.entrance = entrance
        self.events: list[str] = []

    def _pos(self) -> str:
        return f"\\an5\\pos({_number(self.x)},{_number(self.y)})"

    def add(self, layer: int, body: str, *, tags: str = "") -> None:
        """One drawn part (a box, its text), entering and leaving with the rest."""
        start, end = self.start, self.end
        if end - start < 0.02:
            return
        out = f"\\fad(0,{min(_OUT_MS, round((end - start) * 300))})"
        x, y = _number(self.x), _number(self.y)
        kind = self.entrance
        if kind is Entrance.BOUNCE:
            drop = 0.07 * self.height
            first = min(start + 0.16, end)
            second = min(first + 0.1, end)
            high, low = _number(self.y - drop), _number(self.y + drop * 0.18)
            falling = round((first - start) * 1000)
            settling = round((second - first) * 1000)
            self.events.append(_dialogue(
                start, first,
                f"{{\\an5\\move({x},{high},{x},{low},0,{falling})\\fad(60,0){tags}}}{body}",
                POP_STYLE, layer,
            ))
            if second > first:
                self.events.append(_dialogue(
                    first, second,
                    f"{{\\an5\\move({x},{low},{x},{y},0,{settling}){tags}}}{body}",
                    POP_STYLE, layer,
                ))
            if end > second:
                self.events.append(_dialogue(
                    second, end, f"{{{self._pos()}{out}{tags}}}{body}", POP_STYLE, layer
                ))
            return
        if kind is Entrance.POP:
            enter = "\\fscx40\\fscy40\\t(0,90,\\fscx112\\fscy112)\\t(90,170,\\fscx100\\fscy100)"
            fade_in = 60
            position = self._pos()
        elif kind is Entrance.SLIDE:
            enter = ""
            fade_in = 160
            below = _number(self.y + 0.04 * self.height)
            position = f"\\an5\\move({x},{below},{x},{y},0,220)"
        elif kind is Entrance.FADE:
            enter, fade_in, position = "", 220, self._pos()
        else:
            enter, fade_in, position = "", 0, self._pos()
        fade_out = min(_OUT_MS, round((end - start) * 300))
        self.events.append(_dialogue(
            start, end, f"{{{position}\\fad({fade_in},{fade_out}){tags}{enter}}}{body}",
            POP_STYLE, layer,
        ))


def _text_popup(overlay: Overlay, timeline: _Timeline, width: int, text: str | None = None) -> None:
    px = max(12, round(overlay.size * width / 4))
    lines = _wrap(text if text is not None else overlay.text, px, width * 0.86)
    shown = "\\N".join(_escape_text(line) for line in lines)
    longest = max(_width(line, px) for line in lines)
    colour = overlay.rgb
    if overlay.look is TextLook.BOLD:
        border = max(2, round(px * 0.08))
        timeline.add(
            _TEXT_LAYER, shown,
            tags=f"\\fs{px}\\c{_bgr(colour)}\\3c&H000000&\\bord{border}\\shad{max(1, border // 2)}",
        )
        return
    box_colour, ink = (colour, _ink_on(colour)) if overlay.look is TextLook.PILL else (
        (255, 255, 255), (17, 17, 17)
    )
    pad_x, pad_y = px * 0.55, px * 0.32
    box_w, box_h = longest + 2 * pad_x, len(lines) * px + 2 * pad_y
    radius = box_h * (0.5 if len(lines) == 1 and overlay.look is TextLook.PILL else 0.18)
    timeline.add(
        _BOX_LAYER, f"{{\\p1}}{_rounded(box_w, box_h, radius)}",
        tags=f"\\c{_bgr(box_colour)}\\bord0\\shad{2 if overlay.look is TextLook.NOTE else 0}"
        "\\4c&H000000&\\4a&H90&",
    )
    timeline.add(_TEXT_LAYER, shown, tags=f"\\fs{px}\\c{_bgr(ink)}\\bord0\\shad0")


def _arrow(w: float) -> str:
    """An arrow pointing down, ``w`` wide, drawn from its top left."""
    shaft, head = w * 0.3, w * 0.55
    total = w * 1.15
    left, right = (w - shaft) / 2, (w + shaft) / 2
    return (
        f"m {_number(left)} 0 l {_number(right)} 0 l {_number(right)} {_number(total - head)} "
        f"l {_number(w)} {_number(total - head)} l {_number(w / 2)} {_number(total)} "
        f"l 0 {_number(total - head)} l {_number(left)} {_number(total - head)}"
    )


def _ellipse(cx: float, cy: float, rx: float, ry: float, clockwise: bool = True) -> str:
    k = 0.5523
    n = _number
    if clockwise:
        return (
            f"m {n(cx)} {n(cy - ry)} "
            f"b {n(cx + k * rx)} {n(cy - ry)} {n(cx + rx)} {n(cy - k * ry)} {n(cx + rx)} {n(cy)} "
            f"b {n(cx + rx)} {n(cy + k * ry)} {n(cx + k * rx)} {n(cy + ry)} {n(cx)} {n(cy + ry)} "
            f"b {n(cx - k * rx)} {n(cy + ry)} {n(cx - rx)} {n(cy + k * ry)} {n(cx - rx)} {n(cy)} "
            f"b {n(cx - rx)} {n(cy - k * ry)} {n(cx - k * rx)} {n(cy - ry)} {n(cx)} {n(cy - ry)} "
        )
    return (
        f"m {n(cx)} {n(cy - ry)} "
        f"b {n(cx - k * rx)} {n(cy - ry)} {n(cx - rx)} {n(cy - k * ry)} {n(cx - rx)} {n(cy)} "
        f"b {n(cx - rx)} {n(cy + k * ry)} {n(cx - k * rx)} {n(cy + ry)} {n(cx)} {n(cy + ry)} "
        f"b {n(cx + k * rx)} {n(cy + ry)} {n(cx + rx)} {n(cy + k * ry)} {n(cx + rx)} {n(cy)} "
        f"b {n(cx + rx)} {n(cy - k * ry)} {n(cx + k * rx)} {n(cy - ry)} {n(cx)} {n(cy - ry)} "
    )


#: The turn that points the down arrow each way: libass turns counter-clockwise.
_TURN = {
    ShapeKind.ARROW_DOWN: 0,
    ShapeKind.ARROW_UP: 180,
    ShapeKind.ARROW_LEFT: 270,
    ShapeKind.ARROW_RIGHT: 90,
}


def _shape_popup(overlay: Overlay, timeline: _Timeline, width: int) -> None:
    w = overlay.size * width
    colour = _bgr(overlay.rgb)
    outline = f"\\3c&H000000&\\bord{_number(max(2.0, w * 0.035))}\\shad0"
    if overlay.shape in _TURN:
        turn = _TURN[overlay.shape]
        timeline.add(
            _TEXT_LAYER, f"{{\\p1}}{_arrow(w)}",
            tags=f"\\c{colour}{outline}\\frz{turn}",
        )
        return
    if overlay.shape is ShapeKind.RING:
        rx, ry = w / 2, w * 0.36
        thick = w * 0.06
        hole = _ellipse(rx, ry, rx - thick, ry - thick, clockwise=False)
        drawing = _ellipse(rx, ry, rx, ry) + hole
        timeline.add(_TEXT_LAYER, f"{{\\p1}}{drawing}", tags=f"\\c{colour}\\bord0\\shad0")
        return
    # An underline: a gently curved stroke, drawn on from left to right.
    thick = w * 0.07
    n = _number
    drawing = (
        f"m 0 {n(thick)} "
        f"b {n(w * 0.3)} {n(-thick * 0.4)} {n(w * 0.7)} {n(-thick * 0.4)} {n(w)} {n(thick * 0.6)} "
        f"l {n(w)} {n(thick * 1.6)} "
        f"b {n(w * 0.7)} {n(thick * 0.6)} {n(w * 0.3)} {n(thick * 0.6)} 0 {n(thick * 2)}"
    )
    left = timeline.x - w / 2
    top = timeline.y - w
    bottom = timeline.y + w
    wipe = (
        f"\\clip({n(left)},{n(top)},{n(left + 1)},{n(bottom)})"
        f"\\t(0,260,\\clip({n(left)},{n(top)},{n(left + w + 2)},{n(bottom)}))"
    )
    timeline.add(_TEXT_LAYER, f"{{\\p1}}{drawing}", tags=f"\\c{colour}\\bord0\\shad0{wipe}")


def _counter_popup(overlay: Overlay, plan: ScenePlan, start: float, end: float,
                   width: int, height: int, x: float, y: float) -> list[str]:
    parts = counter_parts(overlay.text)
    if parts is None:
        timeline = _Timeline(start, end, x, y, height, overlay.entrance)
        _text_popup(overlay, timeline, width)
        return timeline.events
    before, target, places, commas, after = parts
    counting = min(_COUNT_SECONDS, (end - start) * 0.6)
    steps = max(1, round(counting * _COUNT_STEPS_PER_SECOND))
    events: list[str] = []
    for step in range(steps + 1):
        a = start + counting * step / steps
        b = start + counting * (step + 1) / steps if step < steps else end
        if b - a < 0.01:
            continue
        progress = 1 - (1 - step / steps) ** 3  # eases to its value
        value = target * progress
        number = f"{value:,.{places}f}" if commas else f"{value:.{places}f}"
        timeline = _Timeline(a, b, x, y, height, overlay.entrance if step == 0 else Entrance.NONE)
        _text_popup(overlay, timeline, width, f"{before}{number}{after}")
        if step < steps:
            # Only the last step fades out; the steps between join seamlessly.
            timeline.events = [event.replace(f"\\fad(0,{min(_OUT_MS, round((b - a) * 300))})", "")
                               for event in timeline.events]
        events.extend(timeline.events)
    return events


def popup_styles(width: int, height: int) -> list[str]:
    """The style the pop-ups are drawn in, as V4+ Style values."""
    del width
    return [
        f"{POP_STYLE},Inter,{max(12, round(height * 0.05))},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        "&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1"
    ]


def popup_events(plan: ScenePlan, width: int, height: int) -> list[str]:
    """The Dialogue lines of every pop-up libass draws, and of the progress bar."""
    events: list[str] = []
    for overlay in plan.overlays:
        if not overlay.drawn_by_libass:
            continue
        start, end = plan.overlay_times(overlay)
        if end - start < 0.05:
            continue
        x, y = overlay.x * width, overlay.y * height
        if overlay.kind is OverlayKind.COUNTER:
            events.extend(_counter_popup(overlay, plan, start, end, width, height, x, y))
            continue
        timeline = _Timeline(start, end, x, y, height, overlay.entrance)
        if overlay.kind is OverlayKind.TEXT:
            _text_popup(overlay, timeline, width)
        else:
            _shape_popup(overlay, timeline, width)
        events.extend(timeline.events)

    if plan.hook_title.strip():
        # The hook title (D-199): big words over the video's first seconds,
        # whatever opens it, the cold open included.
        hook = Overlay(
            id="hook", kind=OverlayKind.TEXT, scene=0, text=plan.hook_title.strip(),
            look=TextLook.BOLD, colour="white", size=0.62, x=0.5, y=0.2,
        )
        seconds = min(HOOK_SECONDS, plan.total_frames / plan.fps)
        timeline = _Timeline(0.0, seconds, 0.5 * width, 0.2 * height, height, Entrance.POP)
        _text_popup(hook, timeline, width)
        events.extend(timeline.events)

    if plan.progress_bar:
        total = plan.total_frames / plan.fps
        bar = max(4, round(height * 0.007))
        top = height - bar
        n = _number
        track = f"{{\\p1}}m 0 0 l {width} 0 l {width} {bar} l 0 {bar}"
        events.append(_dialogue(
            0.0, total, f"{{\\an7\\pos(0,{n(top)})\\c&HFFFFFF&\\1a&HA0&\\bord0\\shad0}}{track}",
            POP_STYLE, _BOX_LAYER,
        ))
        events.append(_dialogue(
            0.0, total,
            f"{{\\an7\\pos(0,{n(top)})\\c&H00D7FF&\\bord0\\shad0\\fscx0"
            f"\\t(0,{round(total * 1000)},\\fscx100)}}{track}",
            POP_STYLE, _TEXT_LAYER,
        ))
    return events
