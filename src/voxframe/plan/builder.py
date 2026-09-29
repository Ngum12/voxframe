"""Build a scene plan from a transcript, scenes and matches.

The plan is the only input to rendering, so this is where every upstream
decision is frozen into an editable record.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from voxframe.config.settings import AspectRatio
from voxframe.config.style import MotionStyle, StyleTemplate
from voxframe.match.matcher import SceneMatch
from voxframe.models.scene import Scene
from voxframe.models.transcript import Transcript
from voxframe.plan.scene_plan import (
    MotionKind,
    PlanAsset,
    PlannedScene,
    PlanWord,
    ScenePlan,
)
from voxframe.render.compose.cards import Card
from voxframe.timeline.grid import FrameGrid

__all__ = ["build_plan", "insert_cards"]

log = structlog.get_logger(__name__)


def _choose_motion(
    match: SceneMatch, motion: MotionStyle
) -> tuple[MotionKind, str]:
    """Pick a camera move for a scene, with the reason recorded.

    Parallax needs a depth map, which Phase 5 provides. Until then every
    matched scene uses Ken Burns and records why, so the plan is honest about
    what it did rather than promising motion it cannot yet deliver.
    """
    if match.asset is None:
        return MotionKind.NONE, "no asset matched"

    if not motion.ken_burns_enabled:
        return MotionKind.NONE, "ken burns disabled by style"

    if motion.parallax_enabled:
        # Recorded explicitly: a user reading the plan should not have to guess
        # why a feature they enabled is absent.
        return MotionKind.KEN_BURNS, "parallax arrives in Phase 5; using ken burns"

    return MotionKind.KEN_BURNS, "ken burns"


def build_plan(
    audio_path: Path,
    transcript: Transcript,
    scenes: tuple[Scene, ...],
    matches: tuple[SceneMatch, ...] | None,
    grid: FrameGrid,
    style: StyleTemplate,
    *,
    aspect: AspectRatio = AspectRatio.HORIZONTAL,
    embed_model: str = "",
) -> ScenePlan:
    """Assemble a scene plan.

    Args:
        audio_path: The source audio.
        transcript: The transcript, for language and hash.
        scenes: Segmented scenes.
        matches: One match per scene, in the same order, or ``None`` when no
            image library was used. A plan with no imagery is still worth
            building: it carries the caption text and word timings, which is
            what makes caption correction possible without a library.
        grid: The frame grid this plan is built on.
        style: The style template in use.
        aspect: Output aspect ratio.
        embed_model: Identifier of the embedding models, for diagnostics.

    Returns:
        A complete, editable plan.

    Raises:
        ValueError: If scenes and matches disagree in length, which would
            silently mis-assign images.
    """
    if matches is None:
        # Every scene renders as a plain background. Motion is set to NONE
        # because there is no image to move over.
        matches = tuple(
            SceneMatch(
                scene_index=scene.index,
                asset=None,
                score=0.0,
                semantic_score=0.0,
                queries=(),
                reason="no image library supplied",
            )
            for scene in scenes
        )

    if len(scenes) != len(matches):
        raise ValueError(
            f"{len(scenes)} scenes but {len(matches)} matches; "
            "they must correspond one to one"
        )

    planned: list[PlannedScene] = []

    for scene, match in zip(scenes, matches, strict=True):
        motion_kind, motion_reason = _choose_motion(match, style.motion)

        planned.append(
            PlannedScene(
                index=scene.index,
                start_frame=scene.start_frame,
                end_frame=scene.end_frame,
                text=scene.text,
                words=tuple(PlanWord.from_word(word) for word in scene.words),
                queries=tuple(query.text for query in match.queries),
                asset=PlanAsset.from_asset(match.asset) if match.asset else None,
                alternatives=tuple(
                    PlanAsset.from_asset(asset, similarity)
                    for asset, similarity in match.alternatives
                ),
                near_misses=tuple(
                    PlanAsset.from_asset(asset, similarity)
                    for asset, similarity in match.near_misses
                ),
                motion=motion_kind,
                motion_reason=motion_reason,
                match_score=match.score,
                semantic_score=match.semantic_score,
                match_reason=match.reason,
            )
        )

    plan = ScenePlan(
        audio_path=str(audio_path),
        audio_sha256=transcript.audio_sha256,
        audio_duration=transcript.duration,
        language=transcript.language,
        language_probability=transcript.language_probability,
        fps=grid.fps,
        total_frames=grid.total_frames,
        aspect=aspect,
        style=style.name,
        scenes=tuple(planned),
        transcribe_model=transcript.model_id,
        embed_model=embed_model,
    )

    log.info(
        "plan.built",
        scenes=len(plan.scenes),
        matched=plan.matched_scenes,
        unique_assets=plan.unique_assets,
        frames=plan.total_frames,
    )

    return plan


def insert_cards(
    plan: ScenePlan,
    *,
    title: str = "",
    title_seconds: float = 3.0,
    chapters: bool = True,
) -> ScenePlan:
    """Return a plan with title and chapter cards inserted as scenes.

    A card **adds** time rather than taking it from a scene: the audio is
    unchanged, so stealing frames from the first scene would desynchronise
    everything after it. The video therefore becomes longer than the audio by
    the total card length, and the audio is padded to match — the same policy
    as the end-of-audio case (D-032).

    Args:
        plan: The plan to extend.
        title: Title text. Empty means no title card, which is the default:
            a title is never derived from a filename (D-092).
        title_seconds: How long the title holds.
        chapters: Whether to insert chapter cards at long pauses.

    Returns:
        A new plan. The original is unchanged.
    """
    from voxframe.render.compose.cards import (
        CardKind,
        plan_chapter_cards,
    )

    cards = []
    if title.strip():
        cards.append(
            Card(
                kind=CardKind.TITLE,
                text=title.strip(),
                before_scene=plan.scenes[0].index,
                seconds=title_seconds,
            )
        )

    cards.extend(
        plan_chapter_cards(
            plan.scenes,
            plan.fps,
            plan.audio_duration,
            enabled=chapters,
        )
    )

    if not cards:
        return plan

    by_scene: dict[int, list[Card]] = {}
    for card in cards:
        by_scene.setdefault(card.before_scene, []).append(card)

    rebuilt: list[PlannedScene] = []
    cursor = 0
    index = 0

    for scene in plan.scenes:
        for card in by_scene.get(scene.index, []):
            frames = card.frames(plan.fps)
            rebuilt.append(
                PlannedScene(
                    index=index,
                    start_frame=cursor,
                    end_frame=cursor + frames,
                    card_kind=card.kind,
                    card_text=card.text,
                    motion=MotionKind.NONE,
                    motion_reason="card",
                )
            )
            cursor += frames
            index += 1

        duration = scene.duration_frames

        # Cards push every later scene forward, and the word timings must move
        # with them. Without this the captions of every scene after a card
        # fire early by the total card length — 0.87s after one card, 1.74s
        # after two, and so on (D-110). Invisible on short audio, which has
        # no cards at all.
        shift = (cursor - scene.start_frame) / plan.fps

        rebuilt.append(
            scene.model_copy(
                update={
                    "index": index,
                    "start_frame": cursor,
                    "end_frame": cursor + duration,
                    "words": tuple(
                        word.model_copy(
                            update={
                                "start": word.start + shift,
                                "end": word.end + shift,
                            }
                        )
                        for word in scene.words
                    )
                    if shift
                    else scene.words,
                }
            )
        )
        cursor += duration
        index += 1

    log.info(
        "plan.cards.inserted",
        cards=len(cards),
        added_frames=cursor - plan.total_frames,
        scenes=len(rebuilt),
    )

    return plan.model_copy(
        update={"scenes": tuple(rebuilt), "total_frames": cursor}
    )
