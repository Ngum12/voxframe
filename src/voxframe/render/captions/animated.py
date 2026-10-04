"""Word-clock caption animation: no cumulative karaoke clock, no retiming."""
from __future__ import annotations

from voxframe.config.captions import CaptionAnimation, CaptionTreatment
from voxframe.config.style import CaptionStyle
from voxframe.models.scene import Scene
from voxframe.render.captions.ass import (
    _dialogue,
    _escape_text,
    _pad_lines,
    _TextWidth,
    _usable_width,
    _wrap_words,
)


def scene_events(
    scene: Scene, style: CaptionStyle, treatment: CaptionTreatment,
    width: int, height: int, fps: float, name: str,
) -> list[str]:
    # Reserve space for the largest emphasized glyphs before wrapping.
    measure = style.model_copy(update={
        "font_size_ratio": style.font_size_ratio * treatment.emphasis_scale,
    })
    lines = _wrap_words(scene.words, measure, width, height)
    pages: list[list[list[int]]] = []
    page: list[list[int]] = []
    count = 0
    for line in lines:
        for offset in range(0, len(line), treatment.words_per_page):
            part = line[offset:offset + treatment.words_per_page]
            if page and (count + len(part) > treatment.words_per_page
                         or len(page) >= treatment.max_lines):
                pages.append(page)
                page, count = [], 0
            page.append(part)
            count += len(part)
    if page:
        pages.append(page)
    events: list[str] = []
    start, end = scene.start_seconds(fps), scene.end_seconds(fps)
    size = style.font_size_px(height)
    marked = set(scene.emphasis)
    mode = treatment.animation
    measured = _TextWidth.for_frame(measure, height)
    usable = _usable_width(measure, width, measured) if measured else width

    def fitted_size(token: str, emphasized: bool) -> int:
        # A long unbroken word must fit even in a narrow portrait frame.
        ratio = min(1.0, usable / max(1, measured(token))) if measured else 1.0
        return max(1, int(size * (treatment.emphasis_scale if emphasized else 1) * ratio))

    def text_for(page: list[list[int]], active: int | None) -> str:
        rendered: list[str] = []
        for line in page:
            tokens: list[str] = []
            for index in line:
                token = scene.words[index].text.strip()
                token = token.upper() if style.uppercase else token
                emphasized = index in marked
                spoken = active is not None and index <= active
                color = style.highlight_color if (
                    emphasized or index == active or (mode == CaptionAnimation.KARAOKE and spoken)
                ) else style.primary_color
                if mode == CaptionAnimation.PLAIN and not emphasized:
                    color = style.primary_color
                font_size = fitted_size(token, emphasized)
                tags = f"\\r\\alpha&H00&\\fscx100\\fscy100\\fs{font_size}\\1c{color}\\2c{color}"
                if mode in {CaptionAnimation.POP, CaptionAnimation.TYPEWRITER} and not spoken:
                    tags += "\\alpha&HFF&"
                if mode == CaptionAnimation.KARAOKE and index == active:
                    duration = max(1, round(
                        (scene.words[index].end - scene.words[index].start) * 100
                    ))
                    tags += f"\\2c{style.primary_color}\\kf{duration}"
                elif mode == CaptionAnimation.KARAOKE:
                    tags += "\\k0"
                if index == active and mode in {CaptionAnimation.POP, CaptionAnimation.PULSE}:
                    tags += "\\fscx92\\fscy92\\t(0,110,\\fscx100\\fscy100)"
                tokens.append("{" + tags + "}" + _escape_text(token))
            rendered.append(" ".join(tokens))
        plain = [" ".join(scene.words[i].text for i in line) for line in page]
        text = "\\N".join(_pad_lines(rendered, plain, style, width))
        if mode == CaptionAnimation.SPOTLIGHT and active is not None:
            token = scene.words[active].text.strip()
            if style.uppercase:
                token = token.upper()
            font_size = fitted_size(token, active in marked)
            text = (f"{{\\fs{font_size}\\1c{style.highlight_color}\\fad(25,0)}}"
                    + _escape_text(token))
        return text

    for number, page in enumerate(pages):
        visible = [i for line in page for i in line]
        page_start = max(start, scene.words[visible[0]].start if number else start)
        page_end = min(end, scene.words[pages[number + 1][0][0]].start
                       if number + 1 < len(pages) else end)
        if page_end <= page_start:
            continue
        if mode in {CaptionAnimation.PLAIN, CaptionAnimation.EMPHASIS}:
            events.append(_dialogue(page_start, page_end, text_for(page, None), name))
            continue
        first_start = min(page_end, max(page_start, scene.words[visible[0]].start))
        if first_start > page_start and mode not in {
            CaptionAnimation.POP, CaptionAnimation.TYPEWRITER, CaptionAnimation.SPOTLIGHT,
        }:
            events.append(_dialogue(page_start, first_start, text_for(page, None), name))
        for position, index in enumerate(visible):
            word_start = max(page_start, scene.words[index].start)
            word_end = min(page_end, scene.words[visible[position + 1]].start
                           if position + 1 < len(visible) else page_end)
            if word_end > word_start:
                events.append(_dialogue(word_start, word_end, text_for(page, index), name))
    return events
