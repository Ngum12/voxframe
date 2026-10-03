"""Stickers and images laid over the picture when their word is said (D-198).

libass draws text and shapes, not pictures, so these are laid over the
frame by FFmpeg as the captions are burned in: under the captions, which
must stay readable, and each with the same entrance as a pop-up libass
draws: growing in from its own centre, rising, dropping in, or fading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import structlog

from voxframe.plan.overlays import Entrance, Overlay, OverlayKind
from voxframe.plan.scene_plan import ScenePlan

__all__ = ["PictureOverlay", "overlay_graph", "picture_overlays", "sticker_names", "sticker_path"]

log = structlog.get_logger(__name__)

STICKERS = Path(__file__).resolve().parents[2] / "assets" / "stickers"


@lru_cache(maxsize=1)
def _manifest() -> tuple[dict[str, object], ...]:
    path = STICKERS / "stickers.json"
    if not path.is_file():
        return ()
    return tuple(json.loads(path.read_text(encoding="utf-8")))


def sticker_names() -> frozenset[str]:
    return frozenset(str(entry["name"]) for entry in _manifest())


def stickers() -> tuple[dict[str, object], ...]:
    """The bundled stickers: name, label, and the words that suggest each."""
    return _manifest()


def sticker_path(name: str) -> Path | None:
    """The file of a bundled sticker, or ``None`` for a name not in the set."""
    if name not in sticker_names():
        return None
    path = STICKERS / f"{name}.png"
    return path if path.is_file() else None


@dataclass(frozen=True)
class PictureOverlay:
    path: Path
    start: float
    end: float
    #: Centre and width, in pixels.
    x: float
    y: float
    width: int
    entrance: Entrance

    def signature(self) -> str:
        """What makes it look as it does: the file's content identity too."""
        try:
            stat = self.path.stat()
            identity = f"{stat.st_size}:{stat.st_mtime_ns}"
        except OSError:
            identity = "missing"
        return (
            f"{self.path}:{identity}:{self.start:.3f}:{self.end:.3f}:{self.x:.1f}:"
            f"{self.y:.1f}:{self.width}:{self.entrance.value}"
        )


def _file(overlay: Overlay) -> Path | None:
    if overlay.kind is OverlayKind.STICKER:
        return sticker_path(overlay.sticker)
    if overlay.kind is OverlayKind.IMAGE and overlay.image_path:
        path = Path(overlay.image_path)
        return path if path.is_file() else None
    return None


def picture_overlays(plan: ScenePlan, width: int, height: int) -> list[PictureOverlay]:
    """The stickers and images of a plan, timed and placed for a frame."""
    found: list[PictureOverlay] = []
    for overlay in plan.overlays:
        if overlay.drawn_by_libass:
            continue
        path = _file(overlay)
        if path is None:
            log.warning("render.overlay.missing", overlay=overlay.id, kind=overlay.kind.value)
            continue
        start, end = plan.overlay_times(overlay)
        if end - start < 0.05:
            continue
        found.append(
            PictureOverlay(
                path=path,
                start=start,
                end=end,
                x=overlay.x * width,
                y=overlay.y * height,
                width=max(2, round(overlay.size * width / 2) * 2),
                entrance=overlay.entrance,
            )
        )
    return found


def _scale(item: PictureOverlay) -> str:
    """Its width at each moment: popping in from 40% to 112% and settling."""
    a = f"{item.start:.4f}"
    if item.entrance is not Entrance.POP:
        return str(item.width)
    grow = f"(0.4+0.72*(t-{a})/0.09)"
    settle = f"(1.12-0.12*(t-{a}-0.09)/0.08)"
    factor = f"if(lt(t,{a}),0.4,if(lt(t,{a}+0.09),{grow},if(lt(t,{a}+0.17),{settle},1)))"
    return f"max(2,trunc({item.width}*{factor}/2)*2)"


def _offset(item: PictureOverlay, height: int) -> str:
    """How far below (or above) its place it is at each moment."""
    a = f"{item.start:.4f}"
    if item.entrance is Entrance.SLIDE:
        rise = 0.04 * height
        return f"if(lt(t,{a}+0.22),{rise:.1f}*(1-max(0,t-{a})/0.22),0)"
    if item.entrance is Entrance.BOUNCE:
        drop = 0.07 * height
        low = drop * 0.18
        return (
            f"if(lt(t,{a}+0.16),-{drop:.1f}+{drop + low:.1f}*max(0,t-{a})/0.16,"
            f"if(lt(t,{a}+0.26),{low:.1f}*(1-(t-{a}-0.16)/0.1),0))"
        )
    return "0"


def overlay_graph(
    items: list[PictureOverlay], fps: float, height: int, first_input: int = 1
) -> tuple[list[str], str, str]:
    """The FFmpeg inputs for the overlays, and the filter graph laying them
    over ``[base]``, ending at ``[laid]``.

    Returns:
        The input arguments, the graph (empty when there is nothing to lay),
        and the label the result is at.
    """
    inputs: list[str] = []
    steps: list[str] = []
    current = "base"
    for number, item in enumerate(items):
        stream = first_input + number
        inputs += [
            "-loop", "1", "-framerate", f"{fps:g}", "-t", f"{item.end + 0.1:.3f}",
            "-i", str(item.path.resolve()),
        ]
        fade_in = {Entrance.POP: 0.06, Entrance.SLIDE: 0.16, Entrance.FADE: 0.22}.get(
            item.entrance, 0.0
        )
        fade_out = min(0.15, (item.end - item.start) / 3)
        chain = f"[{stream}:v]format=rgba,scale=w='{_scale(item)}':h=-2:eval=frame"
        if fade_in:
            chain += f",fade=t=in:st={item.start:.4f}:d={fade_in}:alpha=1"
        chain += f",fade=t=out:st={item.end - fade_out:.4f}:d={fade_out:.4f}:alpha=1[s{number}]"
        steps.append(chain)
        label = f"o{number}"
        steps.append(
            f"[{current}][s{number}]overlay=x='{item.x:.1f}-w/2':"
            f"y='{item.y:.1f}-h/2+{_offset(item, height)}':"
            f"enable='between(t,{item.start:.4f},{item.end:.4f})':eval=frame:"
            f"eof_action=pass[{label}]"
        )
        current = label
    return inputs, ";".join(steps), current
