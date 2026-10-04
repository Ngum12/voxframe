"""Edits a person makes to a scene plan.

Every function here takes a plan and returns a new one; nothing is edited in
place and nothing touches the disk. The plan is the renderer's only input
(D-011), so an edit is just a different plan, and re-rendering it reuses every
segment whose inputs did not change (D-101).

**The rule these share: a person's choice is theirs.** An edited scene is marked
``asset_source="user"``, and nothing automatic may replace it afterwards
(D-128). Re-rendering an edited plan renders it; it never re-matches.

**Choices come from the plan, never from a path.** A client names an asset by
the id the plan already recorded -- a runner-up or a near miss -- and the
function looks it up. There is no edit that accepts a filesystem path for an
existing asset, so an edit cannot be used to point a scene at an arbitrary file
(D-115).
"""

from __future__ import annotations

import structlog

from voxframe.plan.scene_plan import MotionKind, PlanAsset, PlannedScene, ScenePlan, Shot

__all__ = [
    "MAX_CAPTION_CHARACTERS",
    "USER",
    "EditError",
    "add_chapter",
    "add_title",
    "choose_image",
    "correct_caption",
    "edit_card_text",
    "edited_scene_count",
    "remove_card",
    "remove_image",
    "set_motion",
    "set_shot",
    "use_image",
]

log = structlog.get_logger(__name__)

#: ``asset_source`` for a scene a person has changed.
USER = "user"


class EditError(ValueError):
    """An edit that cannot be applied, with a message a person can act on."""


def _scene(plan: ScenePlan, index: int) -> PlannedScene:
    if not 0 <= index < len(plan.scenes):
        raise EditError(f"There is no scene {index}.")
    scene = plan.scenes[index]
    if scene.is_card:
        # A card is drawn text; giving it an image would change what a card is.
        raise EditError("Title and chapter cards do not take an image.")
    return scene


def _replace(plan: ScenePlan, scene: PlannedScene) -> ScenePlan:
    scenes = list(plan.scenes)
    scenes[scene.index] = scene
    return plan.model_copy(update={"scenes": tuple(scenes)})


def _without(assets: tuple[PlanAsset, ...], asset_id: str) -> tuple[PlanAsset, ...]:
    return tuple(asset for asset in assets if asset.id != asset_id)


#: ``motion_reason`` for a scene whose camera movement a person turned off.
MOTION_OFF = "camera movement turned off by you"


def _motion_after_new_image(scene: PlannedScene, reason: str) -> dict[str, object]:
    """Motion for a scene that just got a new image.

    Ken Burns, unless the person turned movement off for this scene: that is
    their choice about the scene, not about the old image (D-154).
    """
    if scene.motion_reason == MOTION_OFF:
        return {"motion": MotionKind.NONE, "motion_reason": MOTION_OFF}
    return {"motion": MotionKind.KEN_BURNS, "motion_reason": reason}


def _as_candidate(asset: PlanAsset, similarity: float | None) -> PlanAsset:
    """Keep a displaced image as a runner-up, so the choice can be undone."""
    return asset.model_copy(update={"similarity": similarity})


def choose_image(plan: ScenePlan, index: int, asset_id: str) -> ScenePlan:
    """Show one of the scene's recorded candidates.

    Covers both "choose another" (a runner-up) and "use this image anyway" (a
    near miss). The image it replaces becomes a runner-up, so choosing it again
    is always one click away -- an edit that cannot be undone the same way it
    was made is an edit people hesitate to try.

    Raises:
        EditError: The scene is a card, or the asset is not one of its
            recorded candidates.
    """
    scene = _scene(plan, index)

    candidates = {asset.id: asset for asset in scene.alternatives}
    candidates.update({asset.id: asset for asset in scene.near_misses})
    chosen = candidates.get(asset_id)
    if chosen is None:
        raise EditError(
            "That image is not one of this scene's candidates. Choose from the "
            "images offered for this scene."
        )

    alternatives = _without(scene.alternatives, asset_id)
    near_misses = _without(scene.near_misses, asset_id)
    if scene.asset is not None:
        alternatives = (
            _as_candidate(scene.asset, scene.semantic_score or None),
            *alternatives,
        )

    edited = scene.model_copy(
        update={
            "asset": chosen.model_copy(update={"similarity": None}),
            "alternatives": alternatives,
            "near_misses": near_misses,
            "asset_source": USER,
            **_motion_after_new_image(scene, "image chosen by you"),
            **_show_the_picture(plan),
            "semantic_score": chosen.similarity or 0.0,
            "match_score": chosen.similarity or 0.0,
            "match_reason": "chosen by you",
        }
    )

    log.info("plan.edit.choose", scene=index, asset=asset_id)
    return _replace(plan, edited)


def use_image(plan: ScenePlan, index: int, asset: PlanAsset) -> ScenePlan:
    """Show an image the person supplied themselves.

    The asset arrives already built by the caller from a file it stored and
    checked, with provenance filled in -- author and licence default to the
    person's own work (owner's decision 4), but they are never blank (D-035).

    Raises:
        EditError: The scene is a card.
    """
    scene = _scene(plan, index)

    alternatives = scene.alternatives
    if scene.asset is not None:
        alternatives = (
            _as_candidate(scene.asset, scene.semantic_score or None),
            *alternatives,
        )

    edited = scene.model_copy(
        update={
            "asset": asset,
            "alternatives": alternatives,
            "asset_source": USER,
            **_motion_after_new_image(scene, "your own image"),
            **_show_the_picture(plan),
            "semantic_score": 0.0,
            "match_score": 0.0,
            "match_reason": "your own image",
        }
    )

    log.info("plan.edit.own_image", scene=index, asset=asset.id)
    return _replace(plan, edited)


def remove_image(plan: ScenePlan, index: int) -> ScenePlan:
    """Show a plain background instead, deliberately.

    The removed image becomes a runner-up, so this is undoable like the rest.

    Raises:
        EditError: The scene is a card, or already has no image.
    """
    scene = _scene(plan, index)
    if scene.asset is None:
        raise EditError("This scene already shows a plain background.")

    edited = scene.model_copy(
        update={
            "asset": None,
            "alternatives": (
                _as_candidate(scene.asset, scene.semantic_score or None),
                *scene.alternatives,
            ),
            "asset_source": USER,
            "motion": MotionKind.NONE,
            "motion_reason": "image removed by you",
            "match_score": 0.0,
            "match_reason": "image removed by you",
        }
    )

    log.info("plan.edit.remove", scene=index)
    return _replace(plan, edited)


#: Longest caption correction accepted. A scene is a few sentences; a caption
#: many times longer than anything said in it is a paste gone wrong, and would
#: be spread across the scene's timings until it was unreadable.
MAX_CAPTION_CHARACTERS = 2000


def correct_caption(plan: ScenePlan, index: int, text: str) -> ScenePlan:
    """Change what a scene's captions say.

    Stored as ``caption_text`` beside the original ``text``, which is never
    overwritten: the renderer derives word timings from the originals when it
    applies the correction, including when one word becomes two or two become
    one (D-062), and the person can always see what was actually heard.

    Setting the text back to exactly what was heard removes the correction, so
    "revert" is the same edit as any other.

    Raises:
        EditError: The scene is a card, has no speech to correct, or the text
            is empty or implausibly long.
    """
    scene = _scene(plan, index)

    cleaned = " ".join(text.split())
    if not cleaned:
        # An empty caption is almost always an accident, and silently blanking a
        # scene would be worse than refusing (D-062).
        raise EditError("A caption cannot be empty.")
    if len(cleaned) > MAX_CAPTION_CHARACTERS:
        raise EditError(
            f"That is longer than a scene's caption can be "
            f"({MAX_CAPTION_CHARACTERS} characters)."
        )
    if not scene.words and not scene.text:
        raise EditError("This scene has no speech to caption.")

    original = " ".join(scene.text.split())
    edited = scene.model_copy(
        update={"caption_text": "" if cleaned == original else cleaned,
                "caption_emphasis": () if cleaned != scene.display_text else scene.caption_emphasis}
    )

    log.info(
        "plan.edit.caption",
        scene=index,
        reverted=cleaned == original,
        words=len(cleaned.split()),
    )
    return _replace(plan, edited)


def edited_scene_count(plan: ScenePlan) -> int:
    """How many scenes carry a person's choice."""
    return sum(1 for scene in plan.scenes if scene.asset_source == USER)


# --- cards (D-145) -------------------------------------------------------------


def _card(plan: ScenePlan, index: int) -> PlannedScene:
    if not 0 <= index < len(plan.scenes):
        raise EditError(f"There is no scene {index}.")
    scene = plan.scenes[index]
    if not scene.is_card:
        raise EditError("That scene is not a title or chapter card.")
    return scene


def _card_text(text: str) -> str:
    from voxframe.render.compose.cards import MAX_CARD_CHARACTERS

    cleaned = " ".join(text.split())
    if not cleaned:
        raise EditError("A card needs some text. To take it out, remove the card.")
    if len(cleaned) > MAX_CARD_CHARACTERS:
        raise EditError(
            f"A card is read in a few seconds; keep it under "
            f"{MAX_CARD_CHARACTERS} characters."
        )
    return cleaned


def _retile(plan: ScenePlan, scenes: list[PlannedScene]) -> ScenePlan:
    """Lay scenes end to end again after a card is added or removed.

    Every spoken scene keeps its length and moves as a whole, and its words
    move with it: the plan is on the video's clock, where cards add time, and
    the renderer pauses the speech for each card (D-110, D-144). Scenes are
    renumbered in order.
    """
    rebuilt: list[PlannedScene] = []
    cursor = 0
    for position, scene in enumerate(scenes):
        duration = scene.duration_frames
        shift = (cursor - scene.start_frame) / plan.fps
        update: dict[str, object] = {
            "index": position,
            "start_frame": cursor,
            "end_frame": cursor + duration,
        }
        if shift and scene.words:
            update["words"] = tuple(
                word.model_copy(
                    update={"start": word.start + shift, "end": word.end + shift}
                )
                for word in scene.words
            )
        rebuilt.append(scene.model_copy(update=update))
        cursor += duration
    return plan.model_copy(update={"scenes": tuple(rebuilt), "total_frames": cursor})


def _new_card(plan: ScenePlan, kind: str, text: str, seconds: float) -> PlannedScene:
    frames = max(1, round(seconds * plan.fps))
    return PlannedScene(
        index=0,
        start_frame=0,
        end_frame=frames,
        card_kind=kind,
        card_text=text,
        motion=MotionKind.NONE,
        motion_reason="card",
        asset_source=USER,
    )


def edit_card_text(plan: ScenePlan, index: int, text: str) -> ScenePlan:
    """Change what a title or chapter card says.

    Raises:
        EditError: Not a card, or the text is empty or too long to read.
    """
    scene = _card(plan, index)
    cleaned = _card_text(text)
    log.info("plan.edit.card_text", scene=index, kind=scene.card_kind)
    return _replace(
        plan, scene.model_copy(update={"card_text": cleaned, "asset_source": USER})
    )


def remove_card(plan: ScenePlan, index: int) -> ScenePlan:
    """Take a card out; everything after it moves up by its length.

    Raises:
        EditError: Not a card, or it is all the plan has.
    """
    scene = _card(plan, index)
    remaining = [other for other in plan.scenes if other.index != scene.index]
    if not remaining:
        raise EditError("A video needs at least one scene.")
    log.info("plan.edit.card_removed", scene=index, kind=scene.card_kind)
    return _retile(plan, remaining)


def add_title(plan: ScenePlan, text: str) -> ScenePlan:
    """Open the video with a title card.

    A title is only ever what the person typed (D-092).

    Raises:
        EditError: The video already has a title, or the text is unusable.
    """
    from voxframe.render.compose.cards import TITLE_CARD_SECONDS, CardKind

    cleaned = _card_text(text)
    if any(scene.card_kind == CardKind.TITLE for scene in plan.scenes):
        raise EditError("This video already has a title. Change its text instead.")
    card = _new_card(plan, CardKind.TITLE, cleaned, TITLE_CARD_SECONDS)
    log.info("plan.edit.title_added")
    return _retile(plan, [card, *plan.scenes])


def add_chapter(plan: ScenePlan, before: int, text: str = "") -> ScenePlan:
    """Start a chapter before a spoken scene.

    Without text, the card shows the opening words of the scene it introduces,
    as automatic chapter cards do (D-092).

    Raises:
        EditError: The scene is a card, opens the video, or already follows a
            card -- two cards in a row read as a mistake.
    """
    from voxframe.render.compose.cards import (
        CHAPTER_CARD_SECONDS,
        CHAPTER_LABEL_WORDS,
        CardKind,
    )

    if not 0 <= before < len(plan.scenes):
        raise EditError(f"There is no scene {before}.")
    scene = plan.scenes[before]
    if scene.is_card:
        raise EditError("Choose a spoken scene to start the chapter before.")
    spoken_before = [other for other in plan.scenes[:before] if not other.is_card]
    if not spoken_before:
        raise EditError(
            "A chapter cannot open the video. Add a title for the opening instead."
        )
    if before > 0 and plan.scenes[before - 1].is_card:
        raise EditError("This scene already follows a card.")

    label = text or " ".join(scene.display_text.split()[:CHAPTER_LABEL_WORDS])
    card = _new_card(plan, CardKind.CHAPTER, _card_text(label), CHAPTER_CARD_SECONDS)
    scenes = list(plan.scenes)
    scenes.insert(before, card)
    log.info("plan.edit.chapter_added", before=before)
    return _retile(plan, scenes)


# --- the speaker or the picture (D-192) ----------------------------------------


def _show_the_picture(plan: ScenePlan) -> dict[str, object]:
    """A picture a person just chose is one they want to see (D-192).

    Choosing an image for a scene that shows the speaker would otherwise
    change nothing on screen, which reads as the edit not working.
    """
    if plan.footage is None:
        return {}
    return {"shot": Shot.PICTURE, "shot_source": USER, "shot_reason": "the picture you chose"}


def set_shot(plan: ScenePlan, index: int, shot: Shot) -> ScenePlan:
    """Show the speaker, or the scene's picture, in one scene.

    The choice is the person's: choosing shots again, as a new highlights cut
    does, leaves it alone (``shot_source="user"``).

    Raises:
        EditError: The video has no footage, or the scene is a card.
    """
    if 0 <= index < len(plan.scenes) and plan.scenes[index].is_card:
        raise EditError("A title or chapter card shows its text, not you or a picture.")
    scene = _scene(plan, index)
    if plan.footage is None:
        raise EditError(
            "This video was made from sound only, so there is no recording of you "
            "to show. Make it again from a video file with \"Use my video\" on."
        )
    if scene.footage_start is None:
        raise EditError("This scene has no part of your recording to show.")
    reason = "on you, as you chose" if shot is Shot.SPEAKER else (
        "the picture, as you chose" if scene.asset is not None
        else "a plain background, as you chose"
    )
    log.info("plan.edit.shot", scene=index, shot=shot.value)
    return _replace(
        plan,
        scene.model_copy(update={"shot": shot, "shot_source": USER, "shot_reason": reason}),
    )


# --- motion (D-154) ------------------------------------------------------------


def set_motion(plan: ScenePlan, index: int, on: bool) -> ScenePlan:
    """Turn a scene's camera movement on or off.

    Off holds the image still. On is the slow Ken Burns move every still gets
    by default. Only a still image has a camera to move: a clip moves by itself
    (D-085), a card is drawn text, and a plain background has nothing to move.

    Raises:
        EditError: The scene is a card, has no image, or shows a clip.
    """
    scene = _scene(plan, index)
    if scene.asset is None:
        raise EditError("This scene shows a plain background, which does not move.")
    if scene.asset.is_video:
        raise EditError("A clip moves by itself; camera movement is for photos.")

    update = (
        {"motion": MotionKind.KEN_BURNS, "motion_reason": "camera movement turned on by you"}
        if on
        else {"motion": MotionKind.NONE, "motion_reason": MOTION_OFF}
    )
    log.info("plan.edit.motion", scene=index, on=on)
    # The image is unchanged, so its source is too: an atmospheric scene stays
    # labelled atmospheric (D-137).
    return _replace(plan, scene.model_copy(update=update))
