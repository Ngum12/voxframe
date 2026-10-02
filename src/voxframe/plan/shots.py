"""Put the speaker on screen, and choose where the pictures cut in (D-192).

A recording with a picture of its own used to be reduced to its sound. With
footage attached, every scene can show the speaker, cut to the frame its words
were spoken over, and the matched pictures become **cutaways**: the person
mentions a mountain, and the mountain is on screen while they say it.

How the cutaways are chosen
---------------------------
The rules an editor follows by habit, written down so the result is the same
every time and each scene can say why it shows what it shows:

- **The speaker opens and closes.** The first seconds of a short decide
  whether anyone keeps watching, and a face holds attention where a stock
  photograph does not. The last spoken scene ends on the person too.
- **Only a good match cuts away.** A scene whose picture the matcher was
  confident about. A weak picture is worse than staying on the speaker.
- **Never two cutaways in a row**, so the video keeps coming back to the
  person talking rather than turning into a slideshow with a voice.
- **At most a share of the speaking time**, :data:`MAX_CUTAWAY_SHARE`.
- **Not too long.** A scene longer than :data:`MAX_CUTAWAY_SECONDS` stays on
  the speaker: a picture held that long stops illustrating and starts hiding.

Among the scenes the rules allow, the best matches are taken first. A person's
own choice of shot, in the studio, is never changed (``shot_source="user"``).
"""

from __future__ import annotations

import structlog

from voxframe.plan.scene_plan import Footage, PlannedScene, ScenePlan, Shot

__all__ = [
    "MAX_CUTAWAY_SECONDS",
    "MAX_CUTAWAY_SHARE",
    "MIN_CUTAWAY_SECONDS",
    "attach_footage",
    "choose_shots",
]

log = structlog.get_logger(__name__)

#: The most of the speaking time pictures may cover. Enough that the pictures
#: carry the subject; little enough that the video is still the person.
MAX_CUTAWAY_SHARE = 0.4

#: A cutaway shorter than this is a flash the eye cannot read.
MIN_CUTAWAY_SECONDS = 1.2

#: A cutaway longer than this hides the speaker rather than illustrating them.
MAX_CUTAWAY_SECONDS = 7.0


def attach_footage(plan: ScenePlan, footage: Footage, *, cutaways: bool = True) -> ScenePlan:
    """Give ``plan`` the recording's own picture, and choose each scene's shot.

    Call before cards or highlights change the plan: at that point the video's
    clock and the recording's are the same, so a scene's place in the
    recording is its start frame. Cards and highlights then move scenes on the
    video's clock and keep ``footage_start``, so the speaker stays in sync.

    Args:
        plan: A plan whose scenes are still on the recording's clock.
        footage: The recording's picture.
        cutaways: Let matched pictures cut in. ``False`` keeps the speaker on
            screen throughout.

    Returns:
        A new plan.
    """
    if any(scene.is_card for scene in plan.scenes):
        raise ValueError("attach footage before cards are inserted")

    scenes = tuple(
        scene.model_copy(update={"footage_start": round(scene.start_frame / plan.fps, 6)})
        for scene in plan.scenes
    )
    with_footage = plan.model_copy(update={"footage": footage, "scenes": scenes})
    return choose_shots(with_footage, cutaways=cutaways)


def choose_shots(plan: ScenePlan, *, cutaways: bool = True) -> ScenePlan:
    """Choose speaker or picture for every scene a person has not chosen.

    Returns:
        A new plan, or ``plan`` itself when it has no footage.
    """
    if plan.footage is None:
        return plan

    spoken = [
        position
        for position, scene in enumerate(plan.scenes)
        if not scene.is_card and scene.footage_start is not None
    ]
    if not spoken:
        return plan

    shots: dict[int, tuple[Shot, str]] = {}
    for position in spoken:
        scene = plan.scenes[position]
        if scene.shot_source == "user":
            shots[position] = (scene.shot, scene.shot_reason or "chosen by you")
        else:
            shots[position] = (Shot.SPEAKER, "on the speaker")

    if cutaways:
        _cut_away(plan, spoken, shots)

    rebuilt = list(plan.scenes)
    for position, (shot, reason) in shots.items():
        rebuilt[position] = plan.scenes[position].model_copy(
            update={"shot": shot, "shot_reason": reason}
        )
    chosen = plan.model_copy(update={"scenes": tuple(rebuilt)})

    log.info(
        "plan.shots.chosen",
        speaker=sum(1 for s, _ in shots.values() if s is Shot.SPEAKER),
        cutaways=sum(1 for s, _ in shots.values() if s is Shot.PICTURE),
    )
    return chosen


def _cut_away(
    plan: ScenePlan, spoken: list[int], shots: dict[int, tuple[Shot, str]]
) -> None:
    """Turn the best-matched scenes the rules allow into cutaways, in place."""
    fps = plan.fps
    speaking = sum(plan.scenes[p].duration_frames for p in spoken)
    budget = int(speaking * MAX_CUTAWAY_SHARE)
    used = sum(
        plan.scenes[p].duration_frames for p in spoken if shots[p][0] is Shot.PICTURE
    )
    neighbours = {p: (spoken[i - 1] if i else None, spoken[i + 1] if i + 1 < len(spoken) else None)
                  for i, p in enumerate(spoken)}

    candidates = sorted(
        (p for p in spoken if plan.scenes[p].shot_source != "user"),
        key=lambda p: (-plan.scenes[p].match_score, p),
    )
    first, last = spoken[0], spoken[-1]
    for position in candidates:
        scene = plan.scenes[position]
        reason = _why_not(scene, fps, position in (first, last))
        if reason:
            shots[position] = (Shot.SPEAKER, f"on the speaker: {reason}")
            continue
        before, after = neighbours[position]
        if any(n is not None and shots[n][0] is Shot.PICTURE for n in (before, after)):
            shots[position] = (Shot.SPEAKER, "on the speaker: the scene beside it cuts away")
            continue
        if used + scene.duration_frames > budget:
            shots[position] = (
                Shot.SPEAKER,
                f"on the speaker: pictures already cover {MAX_CUTAWAY_SHARE:.0%} of the speech",
            )
            continue
        shots[position] = (Shot.PICTURE, "cuts away to the picture it matched")
        used += scene.duration_frames


def _why_not(scene: PlannedScene, fps: float, is_end: bool) -> str:
    """Why this scene cannot cut away, or ``""`` when it can."""
    if scene.asset is None:
        return "no picture matched"
    if is_end:
        return "the video opens and closes on the speaker"
    seconds = scene.duration_frames / fps
    if seconds < MIN_CUTAWAY_SECONDS:
        return "too short to read a picture"
    if seconds > MAX_CUTAWAY_SECONDS:
        return "too long to hide the speaker"
    return ""
