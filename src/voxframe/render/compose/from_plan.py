"""Render a video from a scene plan.

The plan is the only input (D-011). Nothing upstream — transcript, library,
matcher — is consulted, so hand-editing the JSON, editing it in the web UI, and
regenerating it are the same operation as far as this code is concerned.

Order of operations, and why:

1. **Scene segments**, each rendered independently with its motion. Flat
   memory, independently cacheable, and a re-render after editing one scene
   touches only that scene.
2. **Concatenate** by stream copy, so intermediate quality survives.
3. **Burn captions** in one pass over the whole video, so caption timing
   derives directly from word timestamps and cannot drift.
4. **Lay in the audio** once, unmodified, padded to the frame boundary rather
   than truncated (D-032).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from voxframe.assets import FontsMissing, fonts_dir
from voxframe.config.settings import QualityPreset
from voxframe.config.style import StyleTemplate, TransitionKind
from voxframe.models.scene import Scene
from voxframe.models.transcript import Word
from voxframe.plan.scene_plan import ScenePlan
from voxframe.render.audio import MusicSettings, simple_bed_chain
from voxframe.render.audio.mixdown import (
    RATE,
    Stems,
    group_powers,
    mixdown,
    speech_levels,
    sum_groups,
    voice_loudness,
)
from voxframe.render.captions.ass import write_ass
from voxframe.render.captions.subtitles import write_srt, write_vtt
from voxframe.render.compose.captioned import (
    _CRF,
    _SPEED,
    RenderError,
    RenderResult,
    _dimensions,
    _verify_frame_count,
)
from voxframe.render.compose.scenes import (
    SegmentResult,
    cleanup_segments,
    concat_segments,
    render_scene_segments,
)
from voxframe.render.compose.segment_cache import SegmentCache
from voxframe.render.compose.transitions import (
    TransitionPlan,
    padded_durations,
    plan_transitions,
    total_frames_after,
)
from voxframe.render.encode.probe import FFmpegCapabilities, FFmpegNotFound
from voxframe.render.ffpath import (
    ass_filter,
    needs_staging,
    run_ffmpeg,
    stage_for_filters,
)

__all__ = ["render_from_plan"]

if TYPE_CHECKING:
    from voxframe.music.director import DirectedBed

log = structlog.get_logger(__name__)


def _narration_graph(plan: ScenePlan) -> tuple[str, bool]:
    """How the narration is laid under the video, pausing for every card.

    A card adds time to the video that the recording does not have, so the
    speech must wait while it is shown: silence the length of the card, at
    the point it was inserted (D-144). Only cards at the start used to delay
    the audio; after a chapter card mid-video, pictures and captions ran late
    by the card's length -- 2s after one, 4s after two, measured against the
    rendered soundtrack.

    Returns:
        ``(filter, is_graph)``. A simple ``-af`` chain when no card sits
        mid-video, as before; otherwise a complex graph reading ``[1:a]`` and
        ending in ``[narr]``. Both pad the end, so the audio is never clipped
        (D-032).
    """
    if any(scene.audio_start is not None for scene in plan.scenes):
        parts = []
        for i, scene in enumerate(plan.scenes):
            seconds = scene.duration_frames / plan.fps
            # Round absolute boundaries, not each duration: fractional frame
            # rates must not accumulate one sample of drift per retained span.
            samples = (round(scene.end_frame * RATE / plan.fps)
                       - round(scene.start_frame * RATE / plan.fps))
            if scene.is_card:
                parts.append(f"anullsrc=r={RATE}:cl=mono,atrim=end_sample={samples}[p{i}]")
            else:
                start = scene.audio_start
                if start is None:
                    raise RenderError("A cut timeline is missing a source audio position.")
                parts.append(
                    f"[1:a]asetpts=PTS-STARTPTS,atrim=start={start:.9f}:duration={seconds:.9f},"
                    f"asetpts=PTS-STARTPTS,aresample={RATE},aformat=channel_layouts=mono,"
                    f"apad,atrim=end_sample={samples}[p{i}]"
                )
        parts.append("".join(f"[p{i}]" for i in range(len(plan.scenes)))
                     + f"concat=n={len(plan.scenes)}:v=0:a=1,apad[narr]")
        return ";".join(parts), True
    pauses = plan.card_pauses()
    lead = sum(seconds for at, seconds in pauses if at <= 1e-6)
    middle = [(at, seconds) for at, seconds in pauses if at > 1e-6]
    delay = f"adelay={round(lead * 1000)}:all=1," if lead > 0 else ""

    if not middle:
        return f"{delay}apad", False

    pieces = len(middle) + 1
    parts = ["[1:a]asplit=" + str(pieces) + "".join(f"[s{i}]" for i in range(pieces))]
    for i in range(pieces):
        start = 0.0 if i == 0 else middle[i - 1][0]
        trim = f"atrim=start={start:.6f}"
        if i < len(middle):
            trim += f":end={middle[i][0]:.6f}"
        pad = f",apad=pad_dur={middle[i][1]:.6f}" if i < len(middle) else ""
        parts.append(f"[s{i}]{trim},asetpts=PTS-STARTPTS{pad}[p{i}]")
    parts.append(
        "".join(f"[p{i}]" for i in range(pieces))
        + f"concat=n={pieces}:v=0:a=1,{delay}apad[narr]"
    )
    return ";".join(parts), True


def _scenes_for_captions(plan: ScenePlan) -> tuple[Scene, ...]:
    """Rebuild caption-bearing scenes from the plan.

    Word timings come from the plan itself (``PlanWord``), so captions
    highlight exactly as precisely on a re-render as on the first render. Any
    correction in ``caption_text`` is applied here, which is what makes editing
    the JSON sufficient — the corrected words inherit the spoken words' timings,
    including when a correction splits or merges words.

    Scenes written by hand with text but no word timings fall back to even
    distribution across the span. That is less precise, but a hand-authored
    scene has no true timings to preserve, so there is nothing being lost.
    """
    scenes: list[Scene] = []

    for planned in plan.scenes:
        if planned.is_card:
            # A card has no captions: its text is drawn into the frame.
            continue

        # Already on the video's clock: inserting cards moves the words with
        # their scenes (D-110). Adding the leading cards' length again here
        # made every titled video's highlights late by the title (D-144).
        words = planned.caption_words()

        if not words:
            tokens = planned.display_text.split()
            if not tokens:
                scenes.append(
                    Scene(
                        index=planned.index,
                        start_frame=planned.start_frame,
                        end_frame=planned.end_frame,
                    )
                )
                continue

            words = _evenly_spaced(
                tokens,
                planned.start_seconds(plan.fps),
                planned.duration_seconds(plan.fps),
            )

        scenes.append(
            Scene(
                index=planned.index,
                start_frame=planned.start_frame,
                end_frame=planned.end_frame,
                # Scene.text is derived from its words, so the corrected text
                # arrives with them and must not be passed separately.
                words=words,
                emphasis=tuple(i for i in planned.caption_emphasis if 0 <= i < len(words)),
            )
        )

    return tuple(scenes)


def _captions_above_face(
    plan: ScenePlan, style: StyleTemplate, width: int, height: int
) -> frozenset[int]:
    """Speaker scenes whose captions go to the top, clear of the face (D-193).

    A scene moves its captions when the speaker's head, as framed, reaches
    down into the caption area while the top of the frame is clear of it. A
    face filling the frame, reaching both, keeps them at the bottom, where a
    viewer looks for them.
    """
    from voxframe.config.style import CaptionPosition
    from voxframe.render.compose.footage import face_extent

    if plan.footage is None or not plan.footage.track:
        return frozenset()
    moved = set()
    for scene in plan.scenes:
        if not plan.shows_speaker(scene) or scene.footage_start is None:
            continue
        treatment = scene.caption_treatment or plan.caption_treatment
        captions = treatment.apply(style.captions) if treatment else style.captions
        if captions.position is not CaptionPosition.BOTTOM:
            continue
        top_style = captions
        if plan.short_export:
            from voxframe.config.short_export import safe_caption_style

            export = plan.short_export
            captions = safe_caption_style(captions, export.safe_area, progress=export.progress)
            top_style = safe_caption_style(captions, export.safe_area,
                                           progress=export.progress, top=True)
        margin = captions.margin_vertical_px(width, height)
        block = captions.max_lines * captions.font_size_px(height) * 1.3
        bottom_band = height - margin - block
        top_band = top_style.margin_vertical_px(width, height) + block
        extent = face_extent(
            plan.footage, width, height, scene.footage_start, scene.duration_frames / plan.fps,
            zoom=scene.visual_beat.zoom if scene.visual_beat else 1,
        )
        if extent is None:
            continue
        face_top, face_bottom = extent
        if face_bottom > bottom_band and face_top > top_band:
            moved.add(scene.index)
    if moved:
        log.info("render.captions.above_face", scenes=len(moved))
    return frozenset(moved)


def _evenly_spaced(
    tokens: list[str], start: float, duration: float
) -> tuple[Word, ...]:
    """Spread words evenly across a span, for scenes with no real timings."""
    per_word = duration / len(tokens)
    return tuple(
        Word(
            text=token,
            start=start + index * per_word,
            # A small gap keeps consecutive highlights distinct rather than
            # abutting exactly, which reads as smoother.
            end=start + (index + 1) * per_word * 0.95,
        )
        for index, token in enumerate(tokens)
    )


def render_from_plan(
    plan: ScenePlan,
    audio_path: Path,
    style: StyleTemplate,
    caps: FFmpegCapabilities,
    output_path: Path,
    *,
    quality: QualityPreset = QualityPreset.STANDARD,
    music: MusicSettings | None = None,
    cache_dir: Path | None = None,
    height: int = 1080,
    background: str = "0x141824",
    write_sidecars: bool = True,
    keep_segments: bool = False,
) -> RenderResult:
    """Render a video from a scene plan.

    Args:
        plan: The edit decision list. The only source of decisions.
        audio_path: Source audio. Laid in unmodified.
        style: Style template, supplying caption appearance and motion
            intensity.
        caps: Probed FFmpeg capabilities.
        output_path: Where to write the video.
        quality: Quality preset for the final encode.
        height: Output height. Width follows from the plan's aspect ratio.
        background: Colour for scenes with no asset.
        write_sidecars: Also write ``.srt`` and ``.vtt``.
        keep_segments: Leave the intermediate segments on disk, for debugging.

    Returns:
        Paths written and timing measurements.

    Raises:
        RenderError: If the audio is missing, libass is unavailable, or the
            rendered frame count disagrees with the plan.
    """
    if plan.short_export and (plan.aspect.value != "9:16" or
            not 3 <= plan.total_frames / plan.fps <= 60 + 1e-7):
        raise RenderError("This export preset needs a 3-60 second portrait edit. "
                          "Choose it in Shorts.")
    if not audio_path.is_file():
        raise RenderError(f"Audio file not found: {audio_path}")
    if not caps.has_libass:
        raise RenderError(
            "This FFmpeg build has no libass, so captions cannot be rendered.\n"
            "Install a full build (Windows: winget install Gyan.FFmpeg)."
        )

    started = time.monotonic()

    width, out_height = _dimensions(plan.aspect, height)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output_path.parent / f".{output_path.stem}_segments"

    log.info(
        "render.plan.start",
        scenes=len(plan.scenes),
        matched=plan.matched_scenes,
        frames=plan.total_frames,
        size=f"{width}x{out_height}",
    )

    from voxframe.render.compose.transitions import has_saved_transitions, resolved_transitions

    saved_joins = has_saved_transitions(plan)
    # --- 1. transitions, decided before rendering ---
    #
    # A crossfade overlaps its two segments, so the outgoing one must be
    # rendered longer by the blend length or the timeline loses those frames
    # (D-097). That has to be known before the segments exist.
    transitions = plan_transitions(
        plan,
        default_seconds=style.motion.transition_seconds,
        # The template's own choice: a style that asks for hard cuts gets them.
        enabled=style.motion.transition is not TransitionKind.CUT,
    )

    scene_frames = [scene.duration_frames for scene in plan.scenes]
    if saved_joins:
        transitions = resolved_transitions(plan)
    render_frames = scene_frames if saved_joins else padded_durations(scene_frames, transitions)

    # --- 2. scene segments ---
    segments = render_scene_segments(
        plan,
        caps,
        work_dir,
        width=width,
        height=out_height,
        motion=style.motion,
        quality=quality,
        background=background,
        frame_overrides=render_frames,
        cache=SegmentCache(cache_dir) if cache_dir else None,
    )

    # The grid check is against the *scene* durations, not the padded ones:
    # padding exists to be consumed by the overlaps, and the output must still
    # equal what the plan declares (D-025).
    produced = (sum(s.frames for s in segments) if saved_joins
                else total_frames_after([s.frames for s in segments], transitions))
    if produced != plan.total_frames:
        raise RenderError(
            f"After transitions the video would be {produced} frames but the "
            f"plan declares {plan.total_frames}. Transition padding and "
            f"overlaps disagree."
        )

    # --- 3. captions ---
    ass_path = output_path.with_suffix(".ass")
    caption_scenes = _scenes_for_captions(plan)
    write_ass(
        ass_path, caption_scenes, style.captions, width, out_height, plan.fps,
        top_scenes=_captions_above_face(plan, style, width, out_height),
        safe_area=plan.short_export.safe_area if plan.short_export else None,
        progress=bool(plan.short_export and plan.short_export.progress),
        scene_treatments={s.index: s.caption_treatment or plan.caption_treatment
                          for s in plan.scenes if s.caption_treatment or plan.caption_treatment},
    )

    if any(s.visual_beat and s.visual_beat.text for s in plan.scenes):
        from voxframe.render.captions.visual_beats import append_visual_beats

        append_visual_beats(ass_path, plan, style, width, out_height)

    if plan.short_export:
        from voxframe.render.captions.short_progress import append_short_progress

        append_short_progress(ass_path, plan, width, out_height)

    srt_path = vtt_path = None
    if write_sidecars:
        srt_path = write_srt(output_path.with_suffix(".srt"), caption_scenes, plan.fps)
        vtt_path = write_vtt(output_path.with_suffix(".vtt"), caption_scenes, plan.fps)

    # --- 4. the pictures: joined and captioned, kept whole ---
    #
    # A change to the sound alone -- the person's mix settings -- then costs no
    # picture work at all: the whole captioned video comes back from the cache
    # and only the sound is made again (D-171).
    pictures = _pictures(
        plan, segments, transitions, ass_path, caps, work_dir, output_path,
        quality=quality, cache_root=cache_dir.parent if cache_dir is not None else None,
    )

    # --- 5. the sound: stems, the person's mix, the loudness target, checks ---
    sound_root = (cache_dir.parent if cache_dir is not None else work_dir) / "sound"
    stems, music_note = _stems(plan, audio_path, music, caps, work_dir, sound_root)
    stems.save(stems_path(output_path))
    mixed = work_dir / "mix.wav"
    report = mixdown(stems, plan.audio_mix, mixed, caps)

    # --- 6. together: the pictures copied, the sound encoded ---
    video_seconds = plan.total_frames / plan.fps
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-i", str(pictures.resolve()),
            "-i", str(mixed.resolve()),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{video_seconds:.6f}",
            "-movflags", "+faststart",
            "-y", str(output_path.resolve()),
        ],
    )

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RenderError(
            f"FFmpeg reported success but {output_path.name} is missing or empty."
        )

    _verify_frame_count(caps, output_path, plan.total_frames)

    if not keep_segments:
        cleanup_segments(work_dir)

    elapsed = time.monotonic() - started

    result = RenderResult(
        video_path=output_path,
        srt_path=srt_path,
        vtt_path=vtt_path,
        ass_path=ass_path,
        width=width,
        height=out_height,
        fps=plan.fps,
        frame_count=plan.total_frames,
        audio_duration=plan.audio_duration,
        elapsed_seconds=elapsed,
        music_note=music_note,
        sound=report.as_dict(),
    )

    log.info(
        "render.plan.done",
        output=output_path.name,
        size_kb=output_path.stat().st_size // 1024,
        elapsed=round(elapsed, 2),
        realtime_factor=round(result.realtime_factor, 2),
    )

    return result



def stems_path(video: Path) -> Path:
    """Where a video's sound stems are recorded, so its mix can change later."""
    return video.parent / f".{video.stem}.stems.json"


#: Bumped whenever the pictures' cache key changes meaning.
#: 2: keyed by each segment's content, not its name (D-188).
PICTURES_VERSION = 2


def _content_digest(path: Path) -> str:
    """A file's SHA-256, read in blocks: segments can be large."""
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _pictures(
    plan: ScenePlan,
    segments: list[SegmentResult],
    transitions: list[TransitionPlan],
    ass_path: Path,
    caps: FFmpegCapabilities,
    work_dir: Path,
    output_path: Path,
    *,
    quality: QualityPreset,
    cache_root: Path | None,
) -> Path:
    """The whole video's pictures with captions burned in, and no sound.

    Keyed by everything that shapes them: each segment's content, the
    transitions, the captions and the quality. Kept in the cache, so a render
    whose pictures have not changed reuses them whole.

    Segments are named by position (``scene_00001.mp4``), so their names say
    nothing about what is in them. Keyed by names, a new picture, card text or
    camera movement left the captions and timing alone and reused the old
    pictures: the update changed nothing (D-188).
    """
    import hashlib
    import shutil

    key_material = json.dumps(
        {
            "segments": [_content_digest(segment.path) for segment in segments],
            "transitions": [repr(t) for t in transitions],
            "join_layout": bool(plan.transition_treatment
                                or any(s.transition_after for s in plan.scenes)),
            "captions": hashlib.sha256(ass_path.read_bytes()).hexdigest(),
            "quality": quality.value,
            "frames": plan.total_frames,
            "fps": plan.fps,
            "version": PICTURES_VERSION,
        },
        sort_keys=True,
    )
    key = hashlib.sha256(key_material.encode()).hexdigest()[:24]
    folder = (cache_root / "pictures") if cache_root is not None else work_dir
    cached = folder / f"{key}.mp4"
    if cached.is_file():
        log.info("render.pictures.reused", key=key)
        return cached

    concatenated = work_dir / "concatenated.mp4"
    from voxframe.render.compose.transitions import has_saved_transitions

    if has_saved_transitions(plan):
        from voxframe.render.compose.joins import concat_saved_transitions

        join_cache = cache_root / "joins" if cache_root else work_dir / "joins"
        concat_saved_transitions(segments, transitions, plan.fps, caps, concatenated, join_cache)
    else:
        concat_segments(segments, caps, concatenated, work_dir, transitions, plan.fps)

    try:
        font_directory: Path | None = fonts_dir()
    except FontsMissing as exc:
        log.warning("render.fonts.missing", error=str(exc))
        font_directory = None

    staging: Path | None = None
    if font_directory is not None and needs_staging(ass_path, font_directory):
        staging = output_path.parent / ".voxframe_staging"
        ass_for_filter = stage_for_filters({"captions": ass_path}, staging)["captions"]
    else:
        ass_for_filter = ass_path
    caption_filter, cwd = ass_filter(ass_for_filter, fontsdir=font_directory)

    folder.mkdir(parents=True, exist_ok=True)
    partial = cached.with_suffix(".partial.mp4")
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-i", str(concatenated.resolve()),
            "-vf", f"{caption_filter},format=yuv420p",
            "-an",
            "-frames:v", str(plan.total_frames),
            "-c:v", "libx264",
            "-crf", str(_CRF[quality]),
            "-preset", _SPEED[quality],
            "-y", str(partial.resolve()),
        ],
        cwd=cwd,
    )
    partial.replace(cached)
    if staging is not None:
        shutil.rmtree(staging, ignore_errors=True)
    return cached


def _stems(
    plan: ScenePlan,
    audio_path: Path,
    music: MusicSettings | None,
    caps: FFmpegCapabilities,
    work_dir: Path,
    sound_root: Path,
) -> tuple[Stems, str]:
    """The voice and the music on the video's timeline, and their levels (D-171).

    Both are kept in the cache by what shapes them, so a change to the mix
    alone finds them there.
    """
    import hashlib

    import soundfile as sf

    from voxframe.music.director import plan_words, speech_spans

    sound_root.mkdir(parents=True, exist_ok=True)
    video_seconds = plan.total_frames / plan.fps
    narration, is_graph = _narration_graph(plan)
    graph = narration if is_graph else f"[1:a]{narration}[narr]"

    voice_key = hashlib.sha256(
        json.dumps(
            [plan.audio_sha256, list(plan.card_pauses()), plan.total_frames, plan.fps,
             [(s.audio_start, s.duration_frames, s.is_card) for s in plan.scenes], 2]
        ).encode()
    ).hexdigest()[:24]
    voice = sound_root / f"voice_{voice_key}.wav"
    if not voice.is_file():
        partial = voice.with_suffix(".partial.wav")
        # The narration graph reads its recording as input 1; a silent input 0
        # keeps its labels as they are everywhere else.
        final_graph = f"{graph};[narr]aresample={RATE},aformat=channel_layouts=mono[v]"
        graph_args = ["-filter_complex", final_graph]
        if any(scene.audio_start is not None for scene in plan.scenes):
            import re

            graph_path = work_dir / "narration.ffgraph"
            graph_path.write_text(final_graph, encoding="utf-8")
            version = re.match(r"(?:n)?(\d+)", caps.version)
            # FFmpeg 7 introduced file-valued options; 9 removed the old alias.
            option = ("-/filter_complex" if version and int(version[1]) >= 7
                      else "-filter_complex_script")
            graph_args = [option, str(graph_path.resolve())]
        run_ffmpeg(
            caps.ffmpeg_path,
            [
                "-loglevel", "error",
                "-f", "lavfi", "-t", f"{video_seconds:.6f}", "-i", f"anullsrc=r={RATE}:cl=mono",
                "-i", str(audio_path.resolve()),
                *graph_args,
                "-map", "[v]", "-t", f"{video_seconds:.6f}",
                "-c:a", "pcm_f32le", "-y", str(partial.resolve()),
            ],
        )
        partial.replace(voice)

    video_words, _ = plan_words(plan)
    landing = video_seconds
    music_path: Path | None = None
    joins: tuple[float, ...] = ()
    note = ""
    if music is not None:
        if not music.path.is_file():
            raise RenderError(f"Music track not found: {music.path}")
        directed, note = _directed_bed(plan, music, audio_path, caps, work_dir, sound_root.parent)
        if directed is not None:
            from voxframe.music.director import joins_of

            music_path, landing = directed.path, directed.plan.landing
            joins = joins_of(directed.plan)
        else:
            music_path = _simple_bed(music, video_seconds, caps, sound_root)
            if video_words:
                landing = max(end for _, end in video_words)
    score_extra: dict[str, Any] = {}
    if music is None and plan.score is not None and video_words:
        music_path, groups, note = _score_bed(
            plan, voice, video_words, video_seconds, sound_root, voice_key, work_dir
        )
        landing = max(end for _, end in video_words)
        if music_path is not None and groups:
            # The mix's group levels (D-179): the groups summed at them, kept.
            group_levels = plan.audio_mix.score_levels
            if not group_levels.is_default:
                digest = hashlib.sha256(json.dumps(group_levels.as_db()).encode()).hexdigest()
                at_levels = music_path.with_name(f"{music_path.stem}.levels_{digest[:12]}.wav")
                if not at_levels.is_file():
                    sum_groups(groups, dict(group_levels.as_db()), at_levels)
                music_path = at_levels
            score_extra = {
                "music_groups": groups,
                "music_levels": group_levels.as_db(),
                "group_power": group_powers(
                    [(s.start, s.end) for s in speech_spans(video_words)], groups, sf
                ),
            }

    spans = speech_spans(video_words)
    timings = [(s.start, s.end) for s in spans]
    levels = speech_levels(timings, voice, music_path, sf)
    polished, polish = _polished_voice(voice, video_words, caps)
    polished_extra: dict[str, Any] = {}
    if polished is not None:
        polished_extra = {
            "voice_polished": polished,
            "spans_polished": speech_levels(timings, polished, music_path, sf),
            "voice_lufs_polished": voice_loudness(polished, caps),
            "polish": polish,
        }
    stems = Stems(
        voice=voice,
        music=music_path,
        spans=levels,
        landing=landing,
        video_end=video_seconds,
        fps=plan.fps,
        joins=joins,
        voice_lufs=voice_loudness(voice, caps),
        **polished_extra,
        **score_extra,
    )
    return stems, note


def _score_bed(
    plan: ScenePlan,
    voice: Path,
    words: list[tuple[float, float]],
    video_seconds: float,
    sound_root: Path,
    voice_key: str,
    work_dir: Path,
) -> tuple[Path | None, tuple[tuple[str, Path], ...], str]:
    """The generated score (D-176), made once per speech, style, seed and intensity, and kept.

    Returns its sum at the designed levels, and its group stems (D-179).

    A score that cannot be made leaves the video without music, said plainly:
    music is never the reason a video fails.
    """
    from voxframe.music.score import (
        ScoreUnavailable,
        group_paths,
        make_score,
        score_key,
        styles,
    )

    assert plan.score is not None
    style = styles().get(plan.score.style)
    if style is None:
        return None, (), (
            f"There is no music score style called {plan.score.style!r} any more, so the "
            "video has no music. Choose a style and update the video."
        )
    key = score_key(
        words, video_seconds, style, plan.score.seed, voice_key, plan.score.intensity
    )
    path = sound_root / f"score_{key}.flac"
    groups = tuple(group_paths(path).items())
    if path.is_file() and all(p.is_file() for _, p in groups):
        log.info("render.score.reused", key=key)
        return path, groups, ""
    try:
        make_score(
            words=words, voice=voice, duration=video_seconds, style_name=style.name,
            seed=plan.score.seed, output=path, work_dir=work_dir / "score",
            intensity=plan.score.intensity,
        )  # fmt: skip
    except ScoreUnavailable as exc:
        log.info("render.score.unavailable", reason=str(exc))
        return None, (), f"The video has no music: the music score could not be made ({exc})."
    except Exception as exc:
        log.warning("render.score.failed", error=f"{type(exc).__name__}: {exc}")
        return None, (), (
            "The music score could not be made this time, so the video has no music."
        )
    return path, groups, ""


#: A gap between words at least this long is a pause, where the noise is heard.
PAUSE_SECONDS = 0.4


def _polished_voice(
    voice: Path, video_words: list[tuple[float, float]], caps: FFmpegCapabilities
) -> tuple[Path | None, dict[str, Any] | None]:
    """The voice stem polished (D-173), kept beside it with what polishing did.

    Made once per voice stem and polish version, so a change to the mix never
    polishes again. A failure leaves the original voice in use, never the video
    unmade.
    """
    import itertools

    from voxframe.render.audio.voice import POLISH_VERSION, polish_voice

    polished = voice.with_name(f"{voice.stem}_polish{POLISH_VERSION}.wav")
    record = polished.with_suffix(".json")
    if polished.is_file() and record.is_file():
        return polished, json.loads(record.read_text(encoding="utf-8"))
    pauses = [
        (end, start)
        for (_, end), (start, _) in itertools.pairwise(video_words)
        if start - end >= PAUSE_SECONDS
    ]
    try:
        report = polish_voice(voice, polished, caps, pauses).as_dict()
    except Exception as exc:  # polishing is an improvement, never a reason to fail
        log.warning("render.voice_polish.failed", error=str(exc))
        polished.unlink(missing_ok=True)
        return None, None
    record.write_text(json.dumps(report), encoding="utf-8")
    return polished, report


def _simple_bed(
    music: MusicSettings, video_seconds: float, caps: FFmpegCapabilities, sound_root: Path
) -> Path:
    """The track looped or trimmed to the video, faded, at full level (D-100)."""
    import hashlib

    digest = hashlib.sha256()
    with music.path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    digest.update(f"|{video_seconds:.4f}|{music.fade_in}|{music.fade_out}|1".encode())
    bed = sound_root / f"loop_{digest.hexdigest()[:24]}.wav"
    if bed.is_file():
        return bed
    partial = bed.with_suffix(".partial.wav")
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-i", str(music.path.resolve()),
            "-filter_complex",
            simple_bed_chain(music, video_seconds, _music_duration(music.path, caps), rate=RATE),
            "-map", "[bed]", "-t", f"{video_seconds:.6f}",
            "-c:a", "pcm_f32le", "-y", str(partial.resolve()),
        ],
    )
    partial.replace(bed)
    return bed


def _directed_bed(
    plan: ScenePlan,
    music: MusicSettings,
    narration: Path,
    caps: FFmpegCapabilities,
    work_dir: Path,
    cache_dir: Path,
) -> tuple[DirectedBed | None, str]:
    """The music edited to the speaker, or ``None`` and why not (D-170).

    Anything that stops the director leaves the plain looped bed, which is
    what every render had before: music is never the reason a video fails.
    """
    from voxframe.config.settings import get_settings

    if not music.directed or get_settings().music_mode == "simple":
        return None, ""
    from voxframe.music.director import NotDirectable, direct_music

    try:
        bed = direct_music(
            plan, music, narration, caps, work_dir, cache_dir, automate=False, rate=RATE
        )
    except NotDirectable as exc:
        log.info("music.not_directed", reason=str(exc))
        return None, f"The music plays as a simple loop under the speech: {exc}."
    except Exception as exc:
        log.warning("music.director_failed", error=f"{type(exc).__name__}: {exc}")
        return None, (
            "The music could not be fitted to the speech this time, so it plays "
            "as a simple loop under it."
        )
    return bed, bed.note


def _music_duration(path: Path, caps: FFmpegCapabilities) -> float:
    """Length of a music track, or 0.0 when it cannot be read.

    Zero means "unknown", which makes the length chain trim and pad — correct
    whether the track is long or short, just not optimal.
    """
    from voxframe.render.encode.probe import probe_media

    try:
        return probe_media(path, caps).duration
    except (OSError, FFmpegNotFound) as exc:
        log.warning("render.music.duration_unknown", error=str(exc))
        return 0.0
