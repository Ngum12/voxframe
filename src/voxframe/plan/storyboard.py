"""One inspectable draft for passage selection, visual direction and final save."""
from voxframe.plan.scene_plan import ScenePlan, Shot
from voxframe.plan.shorts import build_short
from voxframe.plan.visual_director import direct


def audition(plan: ScenePlan, first: int, last: int, *, vertical: bool = True,
             look: str | None = None, match_captions: bool = False) -> ScenePlan:
    draft = build_short(plan, first, last, vertical=vertical)
    return direct(draft, look, match_captions=match_captions) if look else draft


def storyboard(plan: ScenePlan) -> dict:
    """Report what this actual draft will render, without rendering or saving."""
    beats = []
    for scene in plan.scenes:
        speaker = (plan.footage is not None and scene.footage_start is not None
                   and scene.shot is Shot.SPEAKER)
        visual = scene.visual_beat
        beats.append({
            "index": scene.index, "start": scene.start_frame / plan.fps,
            "end": scene.end_frame / plan.fps,
            "shot": "speaker" if speaker else "picture" if scene.asset else "background",
            "reason": (scene.shot_reason if speaker else
                       "Supporting visual" if scene.asset else "No supporting visual selected"),
            "role": visual.kind if visual else "passage",
            "text": visual.text if visual else "",
            "quote": " ".join(w.text for w in scene.caption_words()),
            "zoom": visual.zoom if visual else 1,
            "audio_start": scene.audio_start, "footage_start": scene.footage_start,
        })
    return {"seconds": plan.total_frames / plan.fps, "beats": beats,
            "has_speaker": any(b["shot"] == "speaker" for b in beats),
            "note": "This is the planned edit. Preview it to judge the result. "
                    "Uses the visuals already selected in your project."}
