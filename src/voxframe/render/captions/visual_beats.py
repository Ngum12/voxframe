"""Graphic text beats in the export ASS, clear of captions and tracked faces."""
from __future__ import annotations

from pathlib import Path

from voxframe.config.style import CaptionPosition, CaptionStyle, StyleTemplate
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.captions.ass import _escape_text, _TextWidth, format_timestamp
from voxframe.render.compose.footage import face_extent

COLORS = {"authority": "&HFFFFFF&", "energy": "&HD0F070&", "cinema": "&H8BC3F5&"}
LABELS = {"opening": "OPENING", "keypoint": "KEY POINT", "number": "IN FOCUS", "closing": "CLOSING"}


def _layout(text: str, width: int, height: int) -> tuple[int, list[str]]:
    for size in range(max(12, round(height * .06)), 11, -1):
        measure = _TextWidth(CaptionStyle(font_size_ratio=size / height, font_weight=700), height)
        lines, current = [], ""
        for word in text.split():
            if current and measure(current + " " + word) > width:
                lines.append(current)
                current = ""
            # Very long identifiers still wrap instead of leaving the frame.
            for char in word:
                proposed = current + char
                if measure(proposed) > width and current:
                    lines.append(current)
                    current = ""
                current += char
            current += " "
        if current.strip():
            lines.append(current.strip())
        if len(lines) <= 3 or size == 12:
            return size, [line.strip() for line in lines]
    return 12, [text]


def append_visual_beats(path: Path, plan: ScenePlan, template: StyleTemplate,
                        width: int, height: int) -> None:
    from voxframe.render.compose.from_plan import _captions_above_face

    top_captions = _captions_above_face(plan, template, width, height)
    original = path.read_text(encoding="utf-8")
    if "[Events]" in original:
        beat_style = ("Style: VoxframeBeat,Inter,24,&H00FFFFFF,&H00FFFFFF,&H00000000,"
                      "&HFF000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n")
        path.write_text(original.replace("[Events]", beat_style + "[Events]", 1),
                        encoding="utf-8")
    events = []
    for scene in plan.scenes:
        beat = scene.visual_beat
        if scene.is_card or not beat or not beat.text.strip():
            continue
        if beat.source == "director" and beat.text not in " ".join(
                word.text for word in scene.caption_words()):
            continue  # Later word trims or caption corrections invalidate an automatic quote.
        fontsize, lines = _layout(beat.text, round(width * .73), height)
        label_size = max(9, round(fontsize * .48))
        padding = max(6, round(height * .018))
        block = padding * 2 + label_size * 1.5 + fontsize * 1.35 * len(lines)
        choices = [height * .07, height * .43, height * .64]
        treatment = scene.caption_treatment or plan.caption_treatment
        captions = treatment.apply(template.captions) if treatment else template.captions
        margin = captions.margin_vertical_px(width, height)
        caption_height = captions.font_size_px(height) * captions.max_lines * 1.3
        if scene.index in top_captions or captions.position == CaptionPosition.TOP:
            caption_region = (margin, margin + caption_height)
        elif captions.position == CaptionPosition.CENTER:
            caption_region = (height / 2 - caption_height / 2, height / 2 + caption_height / 2)
        else:
            caption_region = (height - margin - caption_height, height - margin)
        face = None
        if plan.shows_speaker(scene) and plan.footage and scene.footage_start is not None:
            face = face_extent(plan.footage, width, height, scene.footage_start,
                               scene.duration_frames / plan.fps, zoom=beat.zoom)
        if beat.position == "auto":
            regions = [caption_region, *([face] if face else [])]
            clear = [y for y in choices if y + block < height * .9 and
                     all(y + block <= lo or y >= hi for lo, hi in regions)]
            if not clear:
                continue  # Speech stays readable when no safe text space exists.
            y = clear[0]
        else:
            y = choices[0 if beat.position == "top" else 1]
        x = width * .07
        box_width = width * .79
        start, end = scene.start_frame / plan.fps, scene.end_frame / plan.fps
        fade = min(120, round((end - start) * 1000 / 4))
        timing = f"{format_timestamp(start)},{format_timestamp(end)},VoxframeBeat,,0,0,0,,"
        plate = (f"{{\\an7\\pos({x:.1f},{y:.1f})\\p1\\bord0\\shad0"
                 f"\\1c&H201814&\\1a&H30&\\fad({fade},{fade})}}"
                 f"m 0 0 l {box_width:.1f} 0 {box_width:.1f} {block:.1f} 0 {block:.1f}")
        events.append("Dialogue: 1," + timing + plate)
        rail = (f"{{\\an7\\pos({x:.1f},{y:.1f})\\p1\\bord0\\shad0"
                f"\\1c{COLORS[beat.look]}\\fad({fade},{fade})}}"
                f"m 0 0 l 3 0 3 {block:.1f} 0 {block:.1f}")
        events.append("Dialogue: 1," + timing + rail)
        body = (f"{{\\an7\\pos({x + padding:.1f},{y + padding:.1f})\\bord0\\shad0"
                f"\\b1\\fs{label_size}\\1c{COLORS[beat.look]}\\fad({fade},{fade})}}"
                + LABELS[beat.kind] + f"\\N{{\\fs{fontsize}\\1c&HFFFFFF&}}"
                + "\\N".join(_escape_text(line) for line in lines))
        events.append("Dialogue: 2," + timing + body)
    if events:
        with path.open("a", encoding="utf-8") as file:
            file.write("\n" + "\n".join(events) + "\n")
