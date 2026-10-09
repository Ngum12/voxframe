"""Manual time-range edits share pacing's source-clock-preserving assembly."""
from voxframe.plan.editing import EditError
from voxframe.plan.pacing import remove_ranges
from voxframe.plan.scene_plan import ScenePlan


def trim(plan: ScenePlan, start: int, end: int, mode: str) -> ScenePlan:
    if mode not in {"keep", "remove"}:
        raise EditError("Choose Keep section or Remove section.")
    if not 0 <= start < end <= plan.total_frames:
        raise EditError("Choose a non-empty section inside the current timeline.")
    ranges = ((start, end),) if mode == "remove" else tuple(
        (first, last) for first, last in ((0, start), (end, plan.total_frames)) if last > first
    )
    if not ranges:
        raise EditError("This selection already keeps the entire video.")
    for scene in plan.scenes:
        split = any(
            scene.start_frame < edge < scene.end_frame for pair in ranges for edge in pair
        )
        if scene.text and not scene.words and split:
            raise EditError("This scene has no word timings. Choose the whole scene "
                            "to preserve its captions.")
        if scene.is_corrected and any(
            scene.start_frame < edge < scene.end_frame for pair in ranges for edge in pair
        ):
            raise EditError("This cut splits corrected captions. Select the whole scene, "
                            "or restore its original captions before trimming inside it.")
    return remove_ranges(plan, ranges)
