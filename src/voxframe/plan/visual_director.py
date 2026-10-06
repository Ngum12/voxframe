"""A reversible beat sequence over the existing source edit, without invented text."""
from __future__ import annotations

from itertools import pairwise

from voxframe.config.captions import CAPTION_PRESETS
from voxframe.config.transitions import TransitionTreatment
from voxframe.config.visuals import LOOKS, VisualBeat
from voxframe.plan.editing import EditError, _replace, _scene
from voxframe.plan.highlights import _rebase_words
from voxframe.plan.scene_plan import PlanWord, ScenePlan
from voxframe.plan.shots import choose_shots


def set_visual(plan: ScenePlan, index: int, beat: VisualBeat | None) -> ScenePlan:
    scene = _scene(plan, index)
    if scene.is_card:
        raise EditError("Cards have their own text; choose a spoken beat.")
    chosen = beat.model_copy(update={"source": "user"}) if beat else None
    return _replace(plan, scene.model_copy(update={"visual_beat": chosen}))


def _quote(words: list[str]) -> str:
    chosen = words[:8]
    while chosen and len(" ".join(chosen)) > 96:
        chosen.pop()
    return " ".join(chosen)


def _restore_automatic_scenes(plan: ScenePlan) -> ScenePlan:
    """Remove only joins this director created, so a new look changes cadence."""
    restored = []
    stable = ("asset", "motion", "camera_move", "motion_reason", "asset_source",
              "caption_treatment",
              "queries", "query_source", "match_score", "semantic_score", "match_reason",
              "alternatives", "near_misses")
    for scene in plan.scenes:
        previous = restored[-1] if restored else None
        can_join = previous is not None and previous.director_join and not scene.is_card
        if can_join:
            pair = (previous, scene)
            can_join = (all(s.shot_source != "user" and s.asset_source != "user" and
                            (s.visual_beat is None or s.visual_beat.source != "user") for s in pair)
                        and all(getattr(previous, key) == getattr(scene, key) for key in stable)
                        and previous.transition_after is not None
                        and previous.transition_after.kind.value == "cut"
                        and previous.audio_start is not None and scene.audio_start is not None
                        and abs(previous.audio_start + previous.duration_frames / plan.fps
                                - scene.audio_start) < 1e-6)
            if can_join:
                a, b = previous.footage_start, scene.footage_start
                can_join = (a is None and b is None) or (
                    a is not None and b is not None
                    and abs(a + previous.duration_frames / plan.fps - b) < 1e-6)
        if can_join:
            offset = len(previous.caption_words())
            spoken = " ".join(w.text for s in (previous, scene) for w in s.caption_words())
            corrected = previous.is_corrected or scene.is_corrected
            restored[-1] = previous.model_copy(update={
                "end_frame": scene.end_frame, "director_join": scene.director_join,
                "transition_after": scene.transition_after, "visual_beat": None,
                "text": (previous.text + " " + scene.text) if corrected else spoken,
                "caption_text": spoken if corrected else "",
                "words": tuple(PlanWord.from_word(w) for s in (previous, scene)
                               for w in s.caption_words()),
                "caption_emphasis": (*previous.caption_emphasis,
                                     *(i + offset for i in scene.caption_emphasis)),
            })
        else:
            restored.append(scene)
    scenes = tuple(s.model_copy(update={"index": i}) for i, s in enumerate(restored))
    return plan.model_copy(update={"scenes": scenes})


def direct(plan: ScenePlan, look: str, *, match_captions: bool = False) -> ScenePlan:
    if look not in LOOKS:
        raise EditError("Choose one of the director looks.")
    if not 3 <= plan.total_frames / plan.fps <= 60 + 1e-7:
        raise EditError("Choose a 3-60 second passage in Shorts before directing it.")
    plan = _restore_automatic_scenes(plan)
    settings = LOOKS[look]
    rebuilt = []
    for scene in plan.scenes:
        source = scene.audio_start
        if source is None:
            source = max(0, scene.start_frame / plan.fps - plan.card_seconds_before(scene.index))
        displayed = tuple(PlanWord.from_word(w) for w in scene.caption_words())
        pinned = scene.visual_beat is not None and scene.visual_beat.source == "user"
        boundaries = [scene.start_frame]
        if not scene.is_card and not pinned:
            previous = scene.start_frame
            for word in displayed:
                point = round(word.start * plan.fps)
                if (point - previous >= settings["seconds"] * plan.fps
                        and scene.end_frame - point >= plan.fps):
                    boundaries.append(point)
                    previous = point
            # Existing emphasis choices anchor short punch shots on real words.
            for i in scene.caption_emphasis[:2]:
                if not 0 <= i < len(displayed):
                    continue
                word = displayed[i]
                for point in (round(word.start * plan.fps), round((word.end + .6) * plan.fps)):
                    if (scene.start_frame + plan.fps <= point <= scene.end_frame - plan.fps
                            and all(abs(point - b) >= .8 * plan.fps for b in boundaries)):
                        boundaries.append(point)
        boundaries = sorted({*boundaries, scene.end_frame})
        for a, b in pairwise(boundaries):
            selected = [(i, w) for i, w in enumerate(displayed)
                        if a / plan.fps <= (w.start + w.end) / 2 < b / plan.fps]
            words = _rebase_words(tuple(w for _, w in selected), source_start=a / plan.fps,
                                  source_end=b / plan.fps, destination_start=a / plan.fps)
            changes = {"index": len(rebuilt), "start_frame": a, "end_frame": b,
                       "audio_start": (None if scene.is_card else
                                       source + (a - scene.start_frame) / plan.fps)}
            if scene.footage_start is not None:
                changes["footage_start"] = scene.footage_start + (a - scene.start_frame) / plan.fps
            if len(boundaries) > 2:
                raw = [w.text for w in scene.words
                       if a / plan.fps <= (w.start + w.end) / 2 < b / plan.fps]
                spoken = " ".join(w.text for w in words)
                changes.update(words=words, text=" ".join(raw) if scene.is_corrected else spoken,
                               caption_text=spoken if scene.is_corrected else "",
                               caption_emphasis=tuple(n for n, (i, _) in enumerate(selected)
                                                      if i in scene.caption_emphasis))
                if b != scene.end_frame:
                    changes["transition_after"] = TransitionTreatment(kind="cut")
                    changes["director_join"] = True
            if not pinned:
                changes["visual_beat"] = None
            rebuilt.append(scene.model_copy(update=changes))
    draft = ScenePlan.model_validate({**plan.model_dump(), "scenes": rebuilt})
    draft = choose_shots(draft)
    spoken = [s.index for s in draft.scenes if not s.is_card and s.caption_words()]
    for position, index in enumerate(spoken):
        scene = draft.scenes[index]
        if scene.visual_beat and scene.visual_beat.source == "user":
            continue
        words = [w.text for w in scene.caption_words()]
        numeric = next((i for i, text in enumerate(words) if any(c.isdigit() for c in text)), None)
        kind = ("opening" if position == 0 else "closing" if position == len(spoken) - 1
                else "number" if numeric is not None else "keypoint")
        emphasis = scene.caption_emphasis
        punch = position not in (0, len(spoken) - 1) and (
            emphasis or numeric is not None or (look == "energy" and position % 2))
        show_text = (kind in {"opening", "closing", "number"} or emphasis
                     or (look == "energy" and position % 2))
        anchor = numeric if numeric is not None else emphasis[0] if emphasis else 0
        quoted = _quote(words[max(0, anchor - 2):]) if show_text else ""
        if kind == "closing":
            quoted = _quote(words[-8:])
        beat = VisualBeat(text=quoted, kind=kind, look=look,
                          zoom=settings["zoom"] if punch else 1, source="director")
        draft = _replace(draft, scene.model_copy(update={"visual_beat": beat}))
    if match_captions:
        draft = draft.model_copy(update={"caption_treatment": CAPTION_PRESETS[settings["caption"]],
            "scenes": tuple(s.model_copy(update={"caption_treatment": None})
                            for s in draft.scenes)})
    return draft
