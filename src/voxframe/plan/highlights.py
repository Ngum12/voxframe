"""Select the passages worth keeping from a long recording.

A 40-minute lesson is not a video anyone will watch. Highlights mode produces a
short one from it — two or three minutes of the passages that carry the most —
without the tool pretending to understand the content.

**What this deliberately does not do.** It does not summarise, rank by
importance, or decide what the speaker's point was. Those need a language
model, which is Phase 7 and optional by design; a highlights mode that silently
required one would make the core pipeline depend on something the brief says
must stay optional.

**What it does instead** is use signals already in the transcript and the plan,
each of which is a fact rather than an inference:

- **Speech density.** A passage where the speaker is talking steadily carries
  more than one punctuated by long pauses. Measured as words per second over
  the scene.
- **A matched image.** The matcher already judged that this scene depicts
  something findable (D-081). A scene it could not illustrate is, on the
  evidence available, less concrete.
- **Position.** Openings and closings carry disproportionate weight in spoken
  material: people state their subject at the start and their conclusion at the
  end. This is a structural regularity, not a judgement about the content.
- **Length.** A very short scene rarely stands alone once extracted.

The result is honest about what it is: a sample of the recording, chosen by
measurable proxies, not an edit that understood it. The CLI says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from voxframe.plan.scene_plan import PlannedScene, PlanWord, ScenePlan

__all__ = [
    "HighlightSelection",
    "audio_ranges",
    "build_highlights_plan",
    "extract_highlight_audio",
    "select_highlights",
]

log = structlog.get_logger(__name__)

#: Default length of a highlights video, in seconds.
DEFAULT_TARGET_SECONDS = 150.0

#: Audio shorter than this is not worth reducing — the whole thing already is
#: the highlight.
MIN_SOURCE_SECONDS = 300.0

#: Scenes shorter than this are skipped. Extracted from context, a two-second
#: fragment reads as a mistake.
MIN_SCENE_SECONDS = 3.0

#: Fraction of the recording at each end treated as structurally significant.
EDGE_FRACTION = 0.12

#: Bands the timeline is divided into, each guaranteed a share of the budget.
#:
#: Without this the edge bonuses win everything and the middle of a recording
#: is never sampled (D-107). Five is enough to span a talk without reducing
#: each band to a single scene.
HIGHLIGHT_BANDS = 5


@dataclass(frozen=True, slots=True)
class HighlightSelection:
    """Which scenes were kept, and why.

    Attributes:
        scene_indices: Indices into the source plan, in order.
        total_seconds: Length of the selection.
        reasons: Per scene, why it was chosen — recorded so a surprising
            selection can be understood without re-running anything.
    """

    scene_indices: tuple[int, ...]
    total_seconds: float
    reasons: dict[int, str]

    @property
    def count(self) -> int:
        return len(self.scene_indices)


def _score_scene(
    scene: PlannedScene, fps: float, position: float
) -> tuple[float, str]:
    """Score one scene, with the reason.

    Returns:
        ``(score, reason)``. Higher is more likely to be kept.
    """
    duration = scene.duration_frames / fps
    if duration <= 0:
        return 0.0, "empty scene"

    parts: list[str] = []
    score = 0.0

    # Speech density: words per second, normalised against a typical speaking
    # rate of about 2.5 words/second.
    words = len(scene.words)
    density = words / duration if duration else 0.0
    density_score = min(1.0, density / 2.5)
    score += density_score
    parts.append(f"density {density:.1f} w/s")

    # A matched image means the matcher found something concrete to show.
    if scene.asset is not None:
        score += 0.4
        parts.append("has imagery")

    # Openings and closings: people state their subject and their conclusion.
    if position <= EDGE_FRACTION:
        score += 0.5
        parts.append("opening")
    elif position >= 1.0 - EDGE_FRACTION:
        score += 0.35
        parts.append("closing")

    # A card is structure, not content, and makes no sense extracted alone.
    if scene.is_card:
        return 0.0, "card"

    return score, "; ".join(parts)


def select_highlights(
    plan: ScenePlan,
    *,
    target_seconds: float = DEFAULT_TARGET_SECONDS,
    min_scene_seconds: float = MIN_SCENE_SECONDS,
) -> HighlightSelection:
    """Choose scenes for a short version of a long recording.

    Scenes are scored, taken best-first until the target is reached, then
    **restored to chronological order**. Playing them by score would produce a
    video that jumps around its own timeline, which is disorienting in a way
    the score cannot see.

    Args:
        plan: The full plan.
        target_seconds: How long the highlights should run.
        min_scene_seconds: Scenes shorter than this are never selected.

    Returns:
        The selection. Empty when the source is already short enough, which
        the caller treats as "render it all".
    """
    source_seconds = plan.total_frames / plan.fps

    if source_seconds < MIN_SOURCE_SECONDS:
        log.info(
            "highlights.not_needed",
            source_seconds=round(source_seconds, 1),
            minimum=MIN_SOURCE_SECONDS,
        )
        return HighlightSelection((), 0.0, {})

    scored: list[tuple[float, int, float, str]] = []

    for scene in plan.scenes:
        duration = scene.duration_frames / plan.fps
        if duration < min_scene_seconds:
            continue

        position = (
            scene.start_frame / plan.total_frames if plan.total_frames else 0.0
        )
        score, reason = _score_scene(scene, plan.fps, position)

        if score > 0:
            scored.append((score, scene.index, duration, reason))

    # Best first, then by position so a tie prefers earlier material.
    scored.sort(key=lambda item: (-item[0], item[1]))

    # Selecting purely by score clusters everything at the edges, because the
    # opening and closing bonuses dominate. Measured on a 14:41 recording:
    # 19 scenes chosen, all from the first 20% and last 20%, nothing at all
    # from the middle 60% (D-107). A highlights video that skips most of the
    # recording is not representative of it.
    #
    # So the timeline is divided into bands and each is given a share of the
    # budget. Within a band the score still decides; across bands, coverage
    # does.
    chosen: list[tuple[int, float, str]] = []
    total = 0.0
    per_band = target_seconds / HIGHLIGHT_BANDS
    band_totals = [0.0] * HIGHLIGHT_BANDS

    def band_of(index: int) -> int:
        scene = plan.scenes[index]
        fraction = scene.start_frame / plan.total_frames if plan.total_frames else 0.0
        return min(HIGHLIGHT_BANDS - 1, int(fraction * HIGHLIGHT_BANDS))

    for _score, index, duration, reason in scored:
        band = band_of(index)
        if band_totals[band] + duration > per_band:
            continue
        if total + duration > target_seconds and chosen:
            continue
        chosen.append((index, duration, reason))
        band_totals[band] += duration
        total += duration

    # A band with nothing worth taking leaves budget unspent, so a second pass
    # fills the remainder by score alone rather than returning a short video.
    if total < target_seconds * 0.85:
        taken = {index for index, _, _ in chosen}
        for _score, index, duration, reason in scored:
            if index in taken or total + duration > target_seconds:
                continue
            chosen.append((index, duration, reason))
            total += duration

    # Chronological, not by score: a video that jumps around its own timeline
    # is disorienting whatever the scores say.
    chosen.sort(key=lambda item: item[0])

    selection = HighlightSelection(
        scene_indices=tuple(index for index, _, _ in chosen),
        total_seconds=total,
        reasons={index: reason for index, _, reason in chosen},
    )

    log.info(
        "highlights.selected",
        scenes=selection.count,
        of=len(plan.scenes),
        seconds=round(total, 1),
        target=target_seconds,
        source_seconds=round(source_seconds, 1),
    )

    return selection


def _rebase_words(
    words: tuple[PlanWord, ...],
    *,
    source_start: float,
    source_end: float,
    destination_start: float,
) -> tuple[PlanWord, ...]:
    """Move a scene's words onto the extracted timeline.

    Words are clamped into their scene first. A word is assigned to the scene
    holding its midpoint (D-011), so one straddling a boundary starts before
    its own scene — fine over continuous audio, wrong once the preceding audio
    is cut away.

    Returns:
        Rebased words, each inside its scene.
    """
    rebased: list[PlanWord] = []
    span = max(0.0, source_end - source_start)

    for word in words:
        start = min(max(word.start, source_start), source_end)
        end = min(max(word.end, start), source_end)

        rebased.append(
            word.model_copy(
                update={
                    "start": destination_start + (start - source_start),
                    "end": destination_start + min(end - source_start, span),
                }
            )
        )

    return tuple(rebased)


def build_highlights_plan(
    plan: ScenePlan, selection: HighlightSelection
) -> ScenePlan:
    """Build a plan containing only the selected scenes.

    The timeline is rebuilt from zero so the result tiles without gaps
    (D-013).

    **The audio must be extracted to match**, which :func:`extract_highlight_audio`
    does. Without it the video plays the original recording's first N seconds
    against scenes taken from throughout it, and the captions describe words
    nobody is saying — verified on a real render before this was added
    (D-111).

    Returns:
        A new plan. The original is unchanged.
    """
    if not selection.scene_indices:
        return plan

    keep = dict(enumerate(plan.scenes))
    rebuilt = []
    cursor = 0

    for new_index, original_index in enumerate(selection.scene_indices):
        scene = keep[original_index]
        duration = scene.duration_frames

        rebuilt.append(
            scene.model_copy(
                update={
                    "index": new_index,
                    "start_frame": cursor,
                    "end_frame": cursor + duration,
                    # Word timings are relative to the original recording and
                    # would be wildly wrong here, so they are rebased onto the
                    # new position.
                    #
                    # Clamped into the scene, because a word is assigned to
                    # the scene containing its *midpoint* (D-011), so a word
                    # straddling a boundary legitimately starts before its own
                    # scene — by up to 1.7s in a real plan. That is harmless
                    # for captions over continuous audio and wrong here, where
                    # the preceding audio has been cut away (D-111).
                    "words": _rebase_words(
                        scene.words,
                        source_start=scene.start_frame / plan.fps,
                        source_end=scene.end_frame / plan.fps,
                        destination_start=cursor / plan.fps,
                    ),
                }
            )
        )
        cursor += duration

    return plan.model_copy(
        update={
            "scenes": tuple(rebuilt),
            "total_frames": cursor,
            "audio_duration": cursor / plan.fps,
        }
    )


def audio_ranges(
    plan: ScenePlan, selection: HighlightSelection
) -> list[tuple[float, float]]:
    """Source audio ranges for the selected scenes, in order.

    Returns:
        ``(start, end)`` in seconds, referring to the **original** recording.
        The plan's frames are on the video's clock, where cards add time, so
        the cards before each scene are subtracted (D-144): without that, a
        titled highlight reel cut every range 3s late.
    """
    ranges = []
    for index in selection.scene_indices:
        scene = plan.scenes[index]
        before = plan.card_seconds_before(index)
        ranges.append(
            (scene.start_frame / plan.fps - before, scene.end_frame / plan.fps - before)
        )
    return ranges


def extract_highlight_audio(
    audio_path: Path,
    ranges: list[tuple[float, float]],
    destination: Path,
    ffmpeg_path: str,
) -> Path:
    """Cut and concatenate the audio for the selected scenes.

    Each range is extracted at the same sample rate and joined, so the result
    lines up with the rebuilt timeline frame for frame. Without this the
    captions describe words nobody is saying (D-111).

    Returns:
        The written audio file.
    """
    from voxframe.render.ffpath import run_ffmpeg

    destination.parent.mkdir(parents=True, exist_ok=True)

    # One filter graph rather than N temporary files: atrim per range, then
    # concat. Simpler to reason about and leaves nothing to clean up.
    parts = []
    labels = []

    for index, (start, end) in enumerate(ranges):
        label = f"a{index}"
        parts.append(
            f"[0:a]atrim=start={start:.4f}:end={end:.4f},"
            f"asetpts=PTS-STARTPTS[{label}]"
        )
        labels.append(f"[{label}]")

    chain = ";".join(parts)
    chain += f";{''.join(labels)}concat=n={len(ranges)}:v=0:a=1[out]"

    run_ffmpeg(
        ffmpeg_path,
        [
            "-loglevel", "error",
            "-i", str(audio_path.resolve()),
            "-filter_complex", chain,
            "-map", "[out]",
            "-y", str(destination.resolve()),
        ],
    )

    log.info(
        "highlights.audio.extracted",
        ranges=len(ranges),
        seconds=round(sum(e - s for s, e in ranges), 1),
        destination=destination.name,
    )

    return destination
