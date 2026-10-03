"""The video, projected from the plan and its pace (D-199).

The plan keeps the whole recording: its scenes and words as they were said,
on the plan's clock (the recording's, plus cards, D-144). Its ``pace`` lists
what to leave out, what to play first, and where to zoom. This module turns
the two into the video:

- a plan on the **video's** clock, whose scenes are the plan's scenes with
  their cut stretches taken out (a scene cut away entirely is gone), led by
  the cold open's copy when there is one; whose words are where they are
  said in the video (a word cut away is gone); and whose speaker scenes know
  the stretches of the recording they play (``footage_spans``) and their
  punch-ins (``zooms``);
- the **pieces** of the recording the video's sound is made of, in order:
  the edited recording. Laid under the video with the cards' pauses as
  before, it keeps every word in sync with its picture, because both are cut
  from the same list.

The renderer and the studio both work from this projection, so the studio
shows exactly what the video will be. With nothing cut, nothing changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from math import floor

from voxframe.plan.pace import PaceEdits
from voxframe.plan.scene_plan import FootageSpan, PlannedScene, PlanWord, ScenePlan, Zoom

__all__ = ["Piece", "Projection", "project"]

#: How much closer the camera is on every other stretch of a cut scene.
ALTERNATE_ZOOM = 1.08

#: A word mostly cut away is cut: less than this much of it left, it goes.
_WORD_KEPT = 0.5


@dataclass(frozen=True)
class Piece:
    """An unbroken stretch of the recording, as it plays in the video."""

    #: Where it is on the plan's clock.
    story_start: float
    story_end: float
    #: Where it is in the recording itself (no cards).
    source_start: float
    #: Where it starts in the video.
    video_start: float
    #: The plan scene it belongs to, by position.
    scene: int
    teaser: bool = False

    @property
    def seconds(self) -> float:
        return self.story_end - self.story_start

    def to_video(self, story: float) -> float:
        return self.video_start + (story - self.story_start)


@dataclass(frozen=True)
class Projection:
    plan: ScenePlan
    #: The recording's pieces, in the order the video plays them.
    pieces: tuple[Piece, ...] = ()
    #: Plan scene index -> video scene index, for the scenes still there.
    scene_map: dict[int, int] = field(default_factory=dict)
    identity: bool = True

    def story_to_video(self, story: float) -> float | None:
        """Where a moment of the plan plays in the video (not the cold open's
        copy), or ``None`` when it is cut."""
        if self.identity:
            return story
        for piece in self.pieces:
            if not piece.teaser and piece.story_start - 1e-6 <= story <= piece.story_end + 1e-6:
                return piece.to_video(story)
        return None

    def source_pieces(self) -> list[tuple[float, float]]:
        """The edited recording, as ``(start, end)`` stretches of the original."""
        return [(p.source_start, p.source_start + p.seconds) for p in self.pieces]


def _subtract(
    start: float, end: float, cuts: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """``[start, end)`` with the cuts taken out. Slivers under a frame's
    worth go too: they would be a flash."""
    kept: list[tuple[float, float]] = []
    cursor = start
    for cut_start, cut_end in cuts:
        if cut_end <= cursor or cut_start >= end:
            continue
        if cut_start > cursor:
            kept.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if cursor < end:
        kept.append((cursor, end))
    return [(a, b) for a, b in kept if b - a > 0.02]


def _project_words(
    scene: PlannedScene, pieces: list[Piece]
) -> tuple[tuple[PlanWord, ...], dict[int, int]]:
    """A scene's words where they are said in the video; and, for each word
    kept, its new position."""
    words: list[PlanWord] = []
    positions: dict[int, int] = {}
    for index, word in enumerate(scene.words):
        overlaps = [
            (max(word.start, p.story_start), min(word.end, p.story_end), p)
            for p in pieces
            if min(word.end, p.story_end) > max(word.start, p.story_start)
        ]
        length = max(word.end - word.start, 1e-6)
        kept = sum(b - a for a, b, _ in overlaps)
        if not overlaps or kept / length < _WORD_KEPT:
            continue
        first, last = overlaps[0], overlaps[-1]
        start = first[2].to_video(first[0])
        end = max(start + 0.01, last[2].to_video(last[1]))
        positions[index] = len(words)
        words.append(word.model_copy(update={"start": round(start, 4), "end": round(end, 4)}))
    return tuple(words), positions


def project(plan: ScenePlan) -> Projection:
    """The video a plan makes, with its pace applied."""
    pace: PaceEdits = plan.pace
    if not pace.active:
        return Projection(plan=plan, scene_map={s.index: s.index for s in plan.scenes})

    fps = plan.fps
    cuts = pace.merged_cuts()

    # --- the pieces of each scene, and the cold open's ----------------------
    groups: list[tuple[int, bool, list[tuple[float, float]]]] = []
    if pace.cold_open is not None:
        hook_start, hook_end = pace.cold_open
        for position, scene in enumerate(plan.scenes):
            if scene.is_card:
                continue
            start = max(scene.start_frame / fps, hook_start)
            end = min(scene.end_frame / fps, hook_end)
            if end > start:
                stretches = _subtract(start, end, cuts)
                if stretches:
                    groups.append((position, True, stretches))
    for position, scene in enumerate(plan.scenes):
        start, end = scene.start_frame / fps, scene.end_frame / fps
        stretches = [(start, end)] if scene.is_card else _subtract(start, end, cuts)
        groups.append((position, False, stretches))

    # --- laid end to end -------------------------------------------------------
    cursor = 0.0
    laid: list[tuple[int, bool, float, float, list[Piece]]] = []
    for position, teaser, stretches in groups:
        scene = plan.scenes[position]
        if not stretches:
            continue
        begin = cursor
        pieces: list[Piece] = []
        if scene.is_card:
            cursor += stretches[0][1] - stretches[0][0]
        else:
            offset = plan.card_seconds_before(position)
            for story_start, story_end in stretches:
                source = round(story_start - offset, 6)
                pieces.append(Piece(story_start, story_end, source, cursor, position, teaser))
                cursor += story_end - story_start
        laid.append((position, teaser, begin, cursor, pieces))

    # --- on the frame grid ---------------------------------------------------------
    total_frames = max(1, round(cursor * fps))
    boundaries = [min(total_frames, round(item[2] * fps)) for item in laid] + [total_frames]

    scenes: list[PlannedScene] = []
    all_pieces: list[Piece] = []
    scene_map: dict[int, int] = {}
    word_maps: dict[int, dict[int, int]] = {}
    for number, (position, teaser, _begin, _end, pieces) in enumerate(laid):
        start_frame, end_frame = boundaries[number], boundaries[number + 1]
        if end_frame <= start_frame:
            continue
        original = plan.scenes[position]
        update: dict[str, object] = {
            "index": len(scenes),
            "start_frame": start_frame,
            "end_frame": end_frame,
            "story_index": original.index,
            "teaser": teaser,
        }
        if not original.is_card:
            words, positions = _project_words(original, pieces)
            update["words"] = words
            if not teaser:
                word_maps[original.index] = positions
            if original.emphasis and len(positions) != len(original.words):
                update["emphasis"] = (
                    ()
                    if original.caption_text
                    else tuple(positions[i] for i in original.emphasis if i in positions)
                )
            if original.footage_start is not None and pieces:
                scene_start = original.start_frame / fps
                spans = tuple(
                    FootageSpan(
                        source=round(original.footage_start + (p.story_start - scene_start), 6),
                        seconds=round(p.seconds, 6),
                        zoom=ALTERNATE_ZOOM if pace.alternate_zoom and i % 2 == 1 else 1.0,
                    )
                    for i, p in enumerate(pieces)
                )
                update["footage_start"] = spans[0].source
                single = len(spans) == 1 and spans[0].zoom == 1.0
                update["footage_spans"] = () if single else spans
        all_pieces.extend(pieces)
        if not teaser:
            scene_map[original.index] = len(scenes)
        scenes.append(original.model_copy(update=update))

    # --- punch-ins, on the scenes they fall in ---------------------------------------
    projection = Projection(
        plan=plan, pieces=tuple(all_pieces), scene_map=scene_map, identity=False
    )
    zooms: dict[int, list[Zoom]] = {}
    for punch in pace.punch_ins:
        if not punch.on:
            continue
        start = projection.story_to_video(punch.start)
        end = projection.story_to_video(punch.end)
        if start is None or end is None or end <= start:
            continue
        for scene in scenes:
            scene_start = scene.start_frame / fps
            if scene_start - 1e-6 <= start < scene.end_frame / fps and not scene.teaser:
                zooms.setdefault(scene.index, []).append(
                    Zoom(
                        start=round(start - scene_start, 4),
                        end=round(min(end, scene.end_frame / fps) - scene_start, 4),
                        factor=punch.zoom,
                    )
                )
                break
    if zooms:
        scenes = [s.model_copy(update={"zooms": tuple(zooms.get(s.index, ()))}) for s in scenes]

    # --- pop-ups, on their words where they are now -----------------------------------
    overlays = []
    for overlay in plan.overlays:
        if overlay.scene not in scene_map:
            continue
        positions = word_maps.get(overlay.scene, {})
        word = positions.get(overlay.word)
        if word is None:
            later = [new for old, new in sorted(positions.items()) if old > overlay.word]
            word = later[0] if later else 0
        moved = {"scene": scene_map[overlay.scene], "word": word}
        overlays.append(overlay.model_copy(update=moved))

    spoken = sum(p.seconds for p in all_pieces)
    digest = hashlib.sha256(
        json.dumps([plan.audio_sha256, [(p.source_start, p.seconds) for p in all_pieces]]).encode()
    ).hexdigest()
    video = plan.model_copy(
        update={
            "scenes": tuple(scenes),
            "total_frames": total_frames,
            "overlays": tuple(overlays),
            "pace": PaceEdits(),
            "audio_duration": max(spoken, 1.0 / fps),
            "audio_sha256": digest,
        }
    )
    # Checked whole, as a plan loaded from disk would be.
    ScenePlan.model_validate(video.model_dump())
    return Projection(
        plan=video, pieces=tuple(all_pieces), scene_map=scene_map, identity=False
    )


def frames_of(seconds: list[float], fps: float) -> list[int]:
    """Whole frames for stretches laid end to end, rounding where they meet
    rather than each on its own, so they add up to the whole."""
    out: list[int] = []
    cursor = 0.0
    done = 0
    for length in seconds:
        cursor += length
        edge = floor(cursor * fps + 0.5)
        out.append(max(0, edge - done))
        done = edge
    return out
