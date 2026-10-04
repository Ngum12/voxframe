"""Transitions between scenes, on the frame grid.

Every cut is currently hard. A crossfade between scenes is the single change
most visible to a viewer, because it happens at every boundary rather than in
one place.

The hard constraint is the frame grid (D-013). A crossfade **overlaps** two
segments: N frames of scene A and N frames of scene B occupy the same N frames
of output, so the video gets *shorter* unless that is accounted for. Getting
this wrong desynchronises the audio from the captions, which is far worse than
having no transitions at all.

**Segments are rendered with the frames they give away.** A scene followed by
an N-frame crossfade is rendered N frames longer, and the overlap consumes that
padding rather than content. The output length is then exactly the sum of the
scene durations, so the grid is untouched and the audio stays in sync.

The alternative — absorbing the overlap out of the existing frames — is simpler
but wrong: it shortens the video by the total transition length. Measured on
three segments totalling 530 frames with two 12-frame crossfades: 506 frames
out, the video ending 0.8s early with every later caption drifting. That is the
failure D-025 exists to catch, and it is worth the extra padding to avoid
(D-097).

The motion planner is told the padded duration, so a Ken Burns move spans the
frames actually rendered rather than ending early and freezing.

Where a transition goes is a separate question from how long it is. A hard cut
on a sentence boundary reads as deliberate; a crossfade mid-sentence reads as
softness. :func:`plan_transitions` decides per boundary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import structlog

from voxframe.config.style import TransitionKind, get_template
from voxframe.config.transitions import TransitionDirection
from voxframe.plan.scene_plan import PlannedScene, ScenePlan

__all__ = [
    "TransitionPlan",
    "crossfade_filter",
    "plan_transitions",
]

log = structlog.get_logger(__name__)

#: Longest a transition may run, in seconds.
#:
#: Beyond this a crossfade stops reading as a transition and starts reading as
#: a dissolve effect — and it eats into how long each image is actually
#: legible, which matters more at the short scene durations narration produces.
MAX_TRANSITION_SECONDS = 0.6

#: Shortest worth doing. Below about 4 frames at 30fps the blend is over before
#: the eye registers it, and the cost is the same.
MIN_TRANSITION_FRAMES = 4

#: A transition may not consume more than this fraction of the shorter of the
#: two scenes it joins. A 1-second scene with a 0.6s crossfade at each end has
#: almost no clean frames left.
MAX_SCENE_FRACTION = 0.25

#: A gap of at least this long between scenes means the speaker paused, which
#: is where a hard cut reads as deliberate rather than abrupt.
SENTENCE_PAUSE_SECONDS = 0.45


@dataclass(frozen=True, slots=True)
class TransitionPlan:
    """One boundary between two scenes.

    Attributes:
        after_scene: Index of the outgoing scene. The transition sits between
            this scene and the next.
        kind: What to do at this boundary.
        frames: How many frames the blend occupies, taken from the end of the
            outgoing scene and the start of the incoming one.
        reason: Why this was chosen, recorded so a surprising cut is
            explicable from the plan rather than needing the code.
    """

    after_scene: int
    kind: TransitionKind
    frames: int = 0
    reason: str = ""
    direction: TransitionDirection = TransitionDirection.LEFT

    @property
    def is_blend(self) -> bool:
        return self.kind is not TransitionKind.CUT and self.frames > 0


def plan_transitions(
    plan: ScenePlan,
    *,
    default_seconds: float = 0.4,
    enabled: bool = True,
) -> list[TransitionPlan]:
    """Choose a transition for each boundary between scenes.

    The rules, in order:

    1. **A pause means a cut.** If the speaker stopped between scenes, the
       silence already marks the boundary and a crossfade would soften a break
       the audio makes clearly.
    2. **Same asset means a cut.** Crossfading an image into itself produces a
       visible nothing — the frame appears to stall.
    3. **A short scene gets a short transition, or none.** A blend may not eat
       more than a quarter of the shorter scene.
    4. **Otherwise, crossfade.**

    Args:
        plan: The scene plan.
        default_seconds: Nominal crossfade length.
        enabled: When false every boundary is a cut, which is the behaviour
            before this existed.

    Returns:
        One entry per boundary, so ``len(plan.scenes) - 1`` entries.
    """
    if not enabled or len(plan.scenes) < 2:
        return [
            TransitionPlan(
                after_scene=scene.index,
                kind=TransitionKind.CUT,
                reason="transitions disabled" if not enabled else "single scene",
            )
            for scene in plan.scenes[:-1]
        ]

    nominal = min(default_seconds, MAX_TRANSITION_SECONDS)
    transitions: list[TransitionPlan] = []

    for outgoing, incoming in zip(plan.scenes, plan.scenes[1:], strict=False):
        transitions.append(
            _plan_one(outgoing, incoming, plan.fps, nominal)
        )

    blended = sum(1 for t in transitions if t.is_blend)
    log.info(
        "render.transitions.planned",
        boundaries=len(transitions),
        blended=blended,
        cuts=len(transitions) - blended,
    )

    return transitions


def _plan_one(
    outgoing: PlannedScene,
    incoming: PlannedScene,
    fps: float,
    nominal: float,
) -> TransitionPlan:
    """Decide one boundary."""
    # A scene's words carry their own timings, so the gap between the last word
    # of one scene and the first of the next is the real pause — not the
    # difference between frame boundaries, which is always zero by
    # construction.
    pause = _pause_between(outgoing, incoming)

    if pause >= SENTENCE_PAUSE_SECONDS:
        return TransitionPlan(
            after_scene=outgoing.index,
            kind=TransitionKind.CUT,
            reason=f"speaker paused {pause:.2f}s; the silence marks the break",
        )

    if (
        outgoing.asset is not None
        and incoming.asset is not None
        and outgoing.asset.id == incoming.asset.id
    ):
        return TransitionPlan(
            after_scene=outgoing.index,
            kind=TransitionKind.CUT,
            reason="same asset either side; a blend would look like a stall",
        )

    shorter = min(outgoing.duration_frames, incoming.duration_frames)
    wanted = round(nominal * fps)
    allowed = int(shorter * MAX_SCENE_FRACTION)
    frames = min(wanted, allowed)

    if frames < MIN_TRANSITION_FRAMES:
        return TransitionPlan(
            after_scene=outgoing.index,
            kind=TransitionKind.CUT,
            reason=(
                f"scenes too short for a blend "
                f"({shorter} frames; {frames} would be available)"
            ),
        )

    return TransitionPlan(
        after_scene=outgoing.index,
        kind=TransitionKind.CROSSFADE,
        frames=frames,
        reason=f"crossfade {frames} frames ({frames / fps:.2f}s)",
    )


def _pause_between(outgoing: PlannedScene, incoming: PlannedScene) -> float:
    """Silence between the last word of one scene and the first of the next.

    Returns 0.0 when either scene has no word timings, which makes the caller
    fall through to a crossfade — the same as treating it as continuous
    speech.
    """
    if not outgoing.words or not incoming.words:
        return 0.0

    return max(0.0, float(incoming.words[0].start) - float(outgoing.words[-1].end))


def crossfade_filter(
    segments: list[Path],
    transitions: list[TransitionPlan],
    fps: float,
    durations: list[int],
) -> tuple[str, str]:
    """Build an ``xfade`` chain joining every segment.

    ``xfade`` overlaps its inputs: joining an A-second and a B-second clip with
    a D-second transition yields ``A + B - D`` seconds. Chaining them means
    each offset must account for every transition already consumed, which is
    what ``elapsed`` tracks below — computing offsets from nominal scene starts
    instead is the classic way to get progressive drift.

    Args:
        segments: Segment files, in order.
        transitions: One per boundary.
        fps: Frame rate.
        durations: Frame count of each segment.

    Returns:
        ``(filter_complex, final_label)``.
    """
    if len(segments) < 2:
        return "", "0:v"

    # xfade refuses inputs whose timebases differ, and concat emits 1/1000000
    # while a decoded h264 stream is typically 1/15360. The failure is
    # "First input link main timebase do not match", which says nothing about
    # what to change, so every input is normalised to a common timebase and
    # zero-based timestamps first (D-097).
    #
    # Sample aspect ratio is normalised for the same reason: concat also refuses
    # inputs whose SAR differs, and the error names the SAR without saying which
    # segment carried it (D-125). Segments are rendered square now, but a cache
    # entry, a user's clip or a future motion type could still arrive
    # otherwise, and this is the one place every input passes through.
    parts: list[str] = [
        f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,setsar=1[n{index}]"
        for index in range(len(segments))
    ]
    current = "n0"
    # Frames of output produced so far, after subtracting overlaps.
    elapsed = durations[0]

    for index, transition in enumerate(transitions):
        following = index + 1
        if following >= len(segments):
            break

        label = f"x{index}"

        if not transition.is_blend:
            # A hard cut still needs a concat node in the chain, because the
            # inputs are separate streams. settb again afterwards: concat
            # resets the timebase and a later xfade would reject it.
            parts.append(
                f"[{current}][n{following}]concat=n=2:v=1:a=0,settb=AVTB[{label}]"
            )
            elapsed += durations[following]
        else:
            # xfade starts the blend this many seconds into the accumulated
            # output, not into the incoming scene.
            offset = (elapsed - transition.frames) / fps
            parts.append(
                f"[{current}][n{following}]"
                f"xfade=transition={xfade_name(transition)}"
                f":duration={transition.frames / fps:.6f}"
                f":offset={offset:.6f},settb=AVTB[{label}]"
            )
            # The overlap means the output grows by less than the full segment.
            elapsed += durations[following] - transition.frames

        current = label

    return ";".join(parts), current


def padded_durations(
    scene_frames: list[int], transitions: list[TransitionPlan]
) -> list[int]:
    """How many frames each segment must be rendered with.

    A segment followed by a blend is rendered longer by that blend's length,
    because the overlap consumes those frames. Without this the timeline loses
    the total transition length (D-097).

    Returns:
        One padded frame count per scene, in order.
    """
    padded = list(scene_frames)

    for transition in transitions:
        if not transition.is_blend:
            continue
        # The outgoing segment supplies the overlap.
        if 0 <= transition.after_scene < len(padded):
            padded[transition.after_scene] += transition.frames

    return padded


def total_frames_after(durations: list[int], transitions: list[TransitionPlan]) -> int:
    """Frames the video will have once overlaps are subtracted.

    Given :func:`padded_durations` this equals the sum of the *unpadded* scene
    durations, which is what the plan's ``total_frames`` records. The caller
    asserts that (D-025).
    """
    return sum(durations) - sum(t.frames for t in transitions if t.is_blend)


def frames_for_seconds(seconds: float, fps: float) -> int:
    """Transition length in whole frames, never below the useful minimum."""
    return max(MIN_TRANSITION_FRAMES, math.floor(seconds * fps))


def has_saved_transitions(plan: ScenePlan) -> bool:
    return plan.transition_treatment is not None or any(s.transition_after for s in plan.scenes)


def resolved_transitions(plan: ScenePlan) -> list[TransitionPlan]:
    """Resolve deliberate overrides before template automatic choices."""
    base = get_template(plan.style).motion
    defaults = plan_transitions(plan, default_seconds=base.transition_seconds,
                                enabled=base.transition is not TransitionKind.CUT)
    result = []
    for outgoing, incoming, automatic in zip(plan.scenes, plan.scenes[1:], defaults, strict=False):
        treatment = outgoing.transition_after or plan.transition_treatment
        if treatment is None:
            result.append(automatic)
            continue
        frames = min(round(treatment.seconds * plan.fps),
                     int(min(outgoing.duration_frames, incoming.duration_frames) * .25))
        if treatment.kind is TransitionKind.CUT or frames < MIN_TRANSITION_FRAMES:
            result.append(TransitionPlan(outgoing.index, TransitionKind.CUT,
                reason="Chosen cut" if treatment.kind is TransitionKind.CUT
                else "Join too short for this transition"))
        else:
            result.append(TransitionPlan(outgoing.index, treatment.kind, frames,
                f"Chosen {treatment.kind.value}; {frames} frames", treatment.direction))
    return result


def xfade_name(transition: TransitionPlan) -> str:
    directional = {
        TransitionKind.SLIDE: {"left": "coverleft", "right": "coverright",
                               "up": "coverup", "down": "coverdown"},
        TransitionKind.PUSH: {"left": "slideleft", "right": "slideright",
                              "up": "slideup", "down": "slidedown"},
    }
    if transition.kind in directional:
        return directional[transition.kind][transition.direction.value]
    return {TransitionKind.CROSSFADE: "fade", TransitionKind.DIP_TO_BLACK: "fadeblack",
            TransitionKind.ZOOM: "zoomin", TransitionKind.SOFT_BLUR: "hblur",
            TransitionKind.CUT: "fade"}[transition.kind]


def xfade_options(transition: TransitionPlan) -> str:
    if transition.kind is not TransitionKind.DIP_TO_BLACK:
        return f"transition={xfade_name(transition)}"
    # Native fadeblack dips early. A balanced dip reaches neutral YUV black
    # halfway through, without fading chroma toward zero (which turns green).
    black = "if(eq(PLANE,0),16,128)"
    expression = (f"if(gte(P,0.5),(A-{black})*(2*P-1)+{black},"
                  f"(B-{black})*(1-2*P)+{black})")
    return f"transition=custom:expr='{expression}'"
