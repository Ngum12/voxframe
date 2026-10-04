"""A progress rail on the real output clock; no timing or soundtrack changes."""
from pathlib import Path

from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.captions.ass import format_timestamp


def append_short_progress(path: Path, plan: ScenePlan, width: int, height: int) -> None:
    export = plan.short_export
    if not export or not export.progress:
        return
    area = export.safe_area
    x, y = round(area.left * width), round((area.top + .01) * height)
    length = round((1 - area.left - area.right) * width)
    thickness = max(2, round(height * .004))
    end = format_timestamp(plan.total_frames / plan.fps)
    full_ms = max(1, round((plan.total_frames - 1) / plan.fps * 1000))
    accent = "&H" + export.accent[5:7] + export.accent[3:5] + export.accent[1:3] + "&"
    style = ("Style: ShortProgress,Inter,12,&H00FFFFFF,&H00FFFFFF,&H00000000,"
             "&HFF000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n")
    original = path.read_text(encoding="utf-8")
    original = original.replace("[Events]", style + "[Events]", 1)
    polygon = f"m 0 0 l {length} 0 {length} {thickness} 0 {thickness}"
    tags = f"\\an7\\pos({x},{y})\\p1\\bord0\\shad0"
    timing = f"0:00:00.00,{end},ShortProgress,,0,0,0,,"
    track = f"{{{tags}\\1c&HFFFFFF&\\1a&HC0&}}{polygon}"
    fill = (f"{{{tags}\\1c{accent}\\clip({x},{y},{x},{y + thickness})"
            f"\\t(0,{full_ms},\\clip({x},{y},{x + length},{y + thickness}))}}{polygon}")
    path.write_text(original + "\nDialogue: -2," + timing + track
                    + "\nDialogue: -1," + timing + fill + "\n", encoding="utf-8")
