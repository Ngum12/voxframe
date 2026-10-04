"""The pipeline, headless.

Phase 8 needs a second front end, and the pipeline it drives has to be the same
one. Until now the sequence — transcribe, segment, match, cards, highlights,
render — lived inside the ``make`` CLI command, tangled with Rich progress bars
and ``typer.Exit``. A web API could not call it without importing Typer and
catching exit exceptions.

So the sequence moves here, with two changes and no others:

- **Progress is a callback**, not a console. The CLI passes one that updates its
  spinner; the API passes one that feeds server-sent events.
- **Failures raise**, rather than printing and exiting. The caller decides
  whether that becomes red text or a 4xx.

Deliberately *not* changed: the order of operations, and which decisions are
recorded in the plan. The plan remains the renderer's only input (D-011), so
both front ends produce byte-identical plans from the same options — which is
asserted in the tests, because two front ends that drift are worse than one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from voxframe.config.settings import AspectRatio, QualityPreset, Settings
from voxframe.config.style import StyleTemplate
from voxframe.plan.scene_plan import PlanAsset, PlannedScene, ScenePlan
from voxframe.plan.score_choice import ScoreChoice
from voxframe.render.audio.music import MusicSettings
from voxframe.render.compose.captioned import RenderResult
from voxframe.render.encode.probe import FFmpegCapabilities, FootageInfo

if TYPE_CHECKING:
    # Only for annotations: importing it at runtime would load the embedding
    # stack for every caller, including ones that never match anything.
    from voxframe.library.embeddings import Embedder
    from voxframe.plan.scene_plan import TrackPoint
    from voxframe.sourcing.registry import SourcingPlan

__all__ = [
    "JobOptions",
    "PipelineOutcome",
    "Stage",
    "attach_recording_footage",
    "render_plan",
    "run_pipeline",
]

log = structlog.get_logger(__name__)


class Stage(StrEnum):
    """The pipeline's stages, as a user sees them.

    Separate stages rather than one percentage because their durations differ by
    an order of magnitude: transcription was 88% of wall time before chunking
    (D-104). A single bar would spend most of its life at the same number and
    then jump, which reads as a hang.
    """

    TRANSCRIBING = "transcribing"
    SEGMENTING = "segmenting"
    MATCHING = "matching"
    PLANNING = "planning"
    RENDERING = "rendering"
    DONE = "done"


#: Called as ``progress(stage, message, fraction)``. ``fraction`` is ``None``
#: when a stage cannot report progress within itself, which is most of them.
ProgressCallback = Callable[[Stage, str, float | None], None]


def _ignore(stage: Stage, message: str, fraction: float | None) -> None:
    """Default progress sink, so callers may omit one."""


@dataclass(frozen=True, slots=True)
class JobOptions:
    """Everything a render needs, independent of how it was asked for.

    This is the shared vocabulary of the CLI and the API. A new option is added
    here once rather than in each front end, which is what keeps them from
    drifting apart.
    """

    audio: Path
    output: Path
    aspect: AspectRatio = AspectRatio.HORIZONTAL
    quality: QualityPreset = QualityPreset.STANDARD
    height: int = 1080
    library: Path | None = None
    title: str = ""
    chapters: bool = True
    highlights_seconds: float | None = None
    music: MusicSettings | None = None
    #: Music generated for the video instead of a track (D-176).
    score: ScoreChoice | None = None
    model: str | None = None
    language: str | None = None
    languages: tuple[str, ...] = ()
    plan_out: Path | None = None
    no_cache: bool = False
    want_plan: bool = True
    #: Search online for imagery for scenes the library could not fill. Set by
    #: the server from the person's saved consent and configured keys, never
    #: from a request (D-116, D-132).
    source_imagery: bool = False
    #: Show the recording's own picture, when it has one, with the matched
    #: pictures as cutaways (D-192). Off: pictures only, as before.
    footage: bool = False


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    """What a completed run produced.

    Carries the warnings rather than printing them, because the API has to
    deliver them to a browser and the CLI to a terminal. The honesty about
    low-confidence detection and unfilled scenes is part of the result, not
    part of the presentation (D-102).
    """

    result: RenderResult
    plan: ScenePlan | None
    plan_path: Path | None
    language: str
    language_probability: float
    word_count: int
    scene_count: int
    warnings: tuple[str, ...] = field(default=())

    @property
    def matched_scenes(self) -> int:
        return self.plan.matched_scenes if self.plan else 0

    @property
    def fill_rate(self) -> float:
        """Fraction of scenes showing imagery rather than a gradient.

        The measure Phase 4 was judged on (D-069), surfaced here so the web app
        can tell a first-run user why their video is mostly gradients instead of
        leaving them to guess (first-run experience, owner's decision 6).
        """
        illustratable = self.illustratable_scenes
        if not illustratable:
            return 0.0
        if self.plan is not None and self.plan.footage is not None:
            # A scene on the speaker shows the person, not a plain background.
            shown = sum(
                1
                for scene in self.plan.scenes
                if not scene.is_card
                and (self.plan.shows_speaker(scene) or scene.asset is not None)
            )
            return shown / illustratable
        return self.matched_scenes / illustratable

    @property
    def illustratable_scenes(self) -> int:
        """Scenes that could carry imagery: everything except cards.

        A title or chapter card is drawn text and never takes a photograph, so
        counting it made a first-run video report "all 4 scenes show a plain
        background" when one of the four was a title that looked exactly as
        intended -- and disagree with the filmstrip, which counted 3.
        """
        return _illustratable(self.plan) if self.plan else 0


#: Below this, a detected language is reported as uncertain. Mirrors the CLI's
#: threshold, which exists because a wrong language silently truncates the
#: transcript rather than failing (D-061).
LANGUAGE_CONFIDENCE_THRESHOLD = 0.6


#: A model directory smaller than this holds metadata but no weights, which is
#: what an interrupted or abandoned download leaves behind. The smallest real
#: model (`tiny`) is about 75 MB, so this threshold separates the two without
#: needing to know which model was asked for.
def _model_is_cached(settings: Settings, options: JobOptions) -> bool:
    """Whether the transcription model is already on disk, in full.

    Presence of the directory is not enough: an abandoned download leaves one
    behind holding metadata and no weights, and reporting that as cached is
    precisely the wrong answer -- the user then waits through a silent
    multi-minute download under a message saying nothing is downloading.

    Measured the same way the Getting-ready screen measures it (D-157): in the
    cache the model libraries actually use, which an installed app moves to its
    own folder (D-156), each real file counted once.

    Best-effort: a wrong answer changes only a progress message, so this
    tolerates an unreadable cache rather than failing a render over it.
    """
    from voxframe.model_downloads import is_ready, model_needs

    name = options.model or settings.resolved_transcribe_model
    try:
        whisper = model_needs(settings.model_copy(update={"transcribe_model": name}))[0]
        return is_ready(whisper)
    except (OSError, ImportError, KeyError):
        return False


def run_pipeline(
    options: JobOptions,
    settings: Settings,
    template: StyleTemplate,
    capabilities: FFmpegCapabilities,
    *,
    progress: ProgressCallback = _ignore,
) -> PipelineOutcome:
    """Run the whole pipeline and return what it produced.

    Args:
        options: What to render.
        settings: Resolved settings.
        template: The style template.
        capabilities: Probed FFmpeg capabilities, from ``probe_capabilities``.
        progress: Called as each stage begins.

    Returns:
        The outcome, including any warnings the user should see.

    Raises:
        TranscriptionError, MatchError, LibraryError, RenderError: Unchanged
            from the underlying stages. The caller decides how to present them.
    """
    from voxframe.library.db import AssetLibrary
    from voxframe.library.embeddings import Embedder, EmbedderUnavailable
    from voxframe.match.matcher import Matcher
    from voxframe.plan.builder import build_plan, insert_cards
    from voxframe.render.compose.captioned import render_captioned_video
    from voxframe.render.compose.from_plan import render_from_plan
    from voxframe.segment.scenes import segment_transcript
    from voxframe.timeline.grid import FrameGrid
    from voxframe.transcribe.whisper import Transcriber

    warnings: list[str] = []

    # With sourcing on, the library is where downloads go, so there is always
    # one -- the configured library, even if it starts empty.
    library = options.library
    if library is None and options.source_imagery:
        library = settings.library_path

    embedder: Embedder | None = None
    if library is not None:
        embedder = Embedder(
            use_gpu=settings.use_gpu, model_key=settings.resolved_embed_model
        )
        # Before transcription, not after: a model that cannot load fails every
        # image alike, and finding that out after the whole recording has been
        # transcribed, searched and downloaded wasted all of it -- and then
        # rendered a video of plain backgrounds (D-163).
        progress(Stage.TRANSCRIBING, "Preparing the picture model", None)
        try:
            embedder.load()
        except EmbedderUnavailable as exc:
            raise EmbedderUnavailable(
                f"{exc}. No video was made, so no time was spent on one. "
                "Reinstalling Voxframe usually fixes this. To make a video with "
                "captions only in the meantime, turn off pictures from your "
                "library and from online search."
            ) from exc

    # The message distinguishes the two cases, because a first run sits here for
    # minutes downloading weights and a later one does not -- and a user shown
    # "downloading" when nothing is downloading learns to distrust the status.
    progress(
        Stage.TRANSCRIBING,
        "Transcribing"
        if _model_is_cached(settings, options)
        else "Transcribing (downloading the model, this happens once)",
        None,
    )
    transcriber = Transcriber(
        options.model or settings.resolved_transcribe_model,
        cache_dir=settings.cache_path,
        use_gpu=settings.use_gpu,
        language=options.language or settings.language,
        allowed_languages=options.languages or settings.allowed_languages,
    )
    transcript = transcriber.transcribe(options.audio, force=options.no_cache)

    progress(Stage.SEGMENTING, "Segmenting into scenes", None)
    grid = FrameGrid(fps=settings.fps, audio_duration=transcript.duration)
    scenes = segment_transcript(transcript, grid, template.pacing)

    plan: ScenePlan | None = None

    if library is not None and embedder is not None:

        def match() -> ScenePlan:
            asset_library = AssetLibrary(library)
            if asset_library.all_assets():
                progress(Stage.MATCHING, "Matching scenes to imagery", None)
                matches = Matcher(asset_library, embedder).match_scenes(
                    scenes, transcript.language, aspect=options.aspect
                )
            else:
                # An empty library is not an error here: sourcing is about to
                # fill it, and the matcher refuses an empty one outright.
                matches = None
            return build_plan(
                options.audio,
                transcript,
                scenes,
                matches,
                grid,
                template,
                aspect=options.aspect,
                embed_model=embedder.model_id if matches else "",
                unmatched_reason="no image in the library yet",
            )

        plan = match()

        if options.source_imagery:
            sourced = _source_missing_imagery(
                plan, library, settings, embedder, progress
            )
            warnings.extend(sourced.warnings)
            if sourced.added:
                # Re-match the whole plan against the enlarged library. Nothing
                # a person chose exists yet on a first render, so there is
                # nothing to protect (D-128), and a downloaded image may suit a
                # scene better than what it matched before.
                plan = match()
        plan = _add_atmosphere(
            plan, library, embedder, settings, options, progress, warnings
        )
        plan = _flag_printed_text(
            plan, library, embedder, settings.resolved_embed_model
        )
    elif options.want_plan or options.footage:
        # A plan without imagery still carries caption text and word timings,
        # which is what caption correction needs.
        plan = build_plan(
            options.audio, transcript, scenes, None, grid, template,
            aspect=options.aspect,
        )

    audio = options.audio

    if plan is not None and options.footage:
        plan = _attach_footage(
            plan, options, capabilities, progress, warnings, cutaways=library is not None
        )

    if plan is not None:
        progress(Stage.PLANNING, "Placing cards", None)
        if options.title or (options.chapters and len(scenes) > 1):
            plan = insert_cards(
                plan, title=options.title, chapters=options.chapters
            )

        if options.highlights_seconds:
            plan, audio, highlight_warnings = _apply_highlights(
                plan,
                audio,
                options,
                capabilities,
                progress,
            )
            warnings.extend(highlight_warnings)
            if plan.footage is not None:
                # The kept scenes are in a new order: the video must still
                # open and close on the speaker, and cutaways not touch.
                from voxframe.plan.shots import choose_shots

                plan = choose_shots(plan, cutaways=library is not None)

        if options.music is not None:
            # Recorded in the plan so credits describe what was rendered and a
            # re-render reproduces the mix (D-012).
            plan = plan.model_copy(
                update={
                    "music_path": str(options.music.path),
                    "music_credit": options.music.credit,
                }
            )
        elif options.score is not None:
            plan = plan.model_copy(update={"score": options.score})

    scene_total = len(plan.scenes) if plan is not None else len(scenes)
    warnings.extend(_prepare_music(options.music, progress, score=options.score is not None))
    progress(Stage.RENDERING, f"Rendering {scene_total} scenes", None)

    if plan is not None:
        result = render_from_plan(
            plan,
            audio,
            template,
            capabilities,
            options.output,
            quality=options.quality,
            height=options.height,
            music=options.music,
            cache_dir=settings.cache_path / "segments",
        )
    else:
        result = render_captioned_video(
            audio,
            scenes,
            grid,
            template,
            capabilities,
            options.output,
            aspect=options.aspect,
            quality=options.quality,
            height=options.height,
        )

    if result.music_note:
        warnings.append(result.music_note)
    warnings.extend(sound_warnings(result))

    plan_path: Path | None = None
    if plan is not None:
        plan_path = options.plan_out or result.video_path.with_suffix(".plan.json")
        plan.save(plan_path)

    warnings.extend(
        _language_warnings(
            transcript.language,
            transcript.language_probability,
            forced=options.language is not None,
            restricted=bool(options.languages or settings.allowed_languages),
        )
    )
    if plan is not None:
        # The library actually used: with sourcing on it is the configured one
        # even when none was passed, and describing it as missing would be
        # wrong about a video whose scenes were just filled from the web.
        warnings.extend(
            _imagery_warnings(
                plan, library=library, sourcing=options.source_imagery
            )
        )

    progress(Stage.DONE, "Done", 1.0)

    log.info(
        "pipeline.done",
        scenes=scene_total,
        matched=plan.matched_scenes if plan else 0,
        seconds=round(result.elapsed_seconds, 1),
        realtime=round(result.realtime_factor, 2),
    )

    return PipelineOutcome(
        result=result,
        plan=plan,
        plan_path=plan_path,
        language=transcript.language,
        language_probability=transcript.language_probability,
        word_count=transcript.word_count,
        scene_count=scene_total,
        warnings=tuple(warnings),
    )


def render_plan(
    plan_path: Path,
    output: Path,
    settings: Settings,
    capabilities: FFmpegCapabilities,
    *,
    quality: QualityPreset = QualityPreset.STANDARD,
    height: int = 1080,
    progress: ProgressCallback = _ignore,
) -> PipelineOutcome:
    """Render an existing plan exactly as it stands.

    The re-render path for an edited plan. It deliberately does **not**
    transcribe, segment or match: re-matching would replace the choices the
    person just made (D-128). The plan says what to render, and this renders
    it, reusing every segment whose inputs did not change (D-101) -- so
    swapping one image in a long video re-renders one scene.

    Music is taken from the plan, which records it for exactly this reason
    (D-012), so a re-render reproduces the original mix.

    Raises:
        PlanError, RenderError: Unchanged from the underlying stages.
    """
    from voxframe.config.style import get_template
    from voxframe.render.compose.from_plan import render_from_plan

    plan = ScenePlan.load(plan_path)
    audio = Path(plan.audio_path)

    music = None
    if plan.music_path:
        music = MusicSettings(path=Path(plan.music_path), credit=plan.music_credit)

    component_warnings = _prepare_music(music, progress, score=plan.score is not None)
    progress(Stage.RENDERING, f"Rendering {len(plan.scenes)} scenes", None)
    result = render_from_plan(
        plan,
        audio,
        get_template(plan.style or None),
        capabilities,
        output,
        quality=quality,
        height=plan.short_export.height if plan.short_export else height,
        music=music,
        cache_dir=settings.cache_path / "segments",
    )
    progress(Stage.DONE, "Done", 1.0)

    log.info(
        "pipeline.rerender.done",
        scenes=len(plan.scenes),
        matched=plan.matched_scenes,
        seconds=round(result.elapsed_seconds, 1),
    )

    return PipelineOutcome(
        result=result,
        plan=plan,
        plan_path=plan_path,
        language=plan.language,
        language_probability=plan.language_probability,
        word_count=sum(len(scene.words) for scene in plan.scenes),
        scene_count=len(plan.scenes),
        warnings=(
            *_plan_age_warnings(plan),
            *_imagery_warnings(plan, library=Path()),
            *component_warnings,
            *((result.music_note,) if result.music_note else ()),
            *sound_warnings(result),
        ),
    )


#: Said when a plan from before the first release is rendered (D-147).
PRE_RELEASE_PLAN_WARNING = (
    "This scene plan was made by a pre-release version of Voxframe, which is "
    "not supported. Its captions may be out of step after a title or chapter "
    "card. To be sure, make the video again from the recording."
)


def _plan_age_warnings(plan: ScenePlan) -> list[str]:
    """Warn about a plan made by an older renderer (D-147).

    Only pre-release plans are said to be unsupported. A plan from an older
    released renderer is logged; when a future renderer changes what plans
    mean, this is where it says so.
    """
    from voxframe.render.version import RENDERER_VERSION

    if plan.is_pre_release:
        return [PRE_RELEASE_PLAN_WARNING]
    if plan.renderer_version < RENDERER_VERSION:
        log.info(
            "plan.older_renderer",
            made_by=plan.renderer_version,
            rendering_with=RENDERER_VERSION,
        )
    return []


def _add_atmosphere(
    plan: ScenePlan,
    library: Path,
    embedder: Embedder,
    settings: Settings,
    options: JobOptions,
    progress: ProgressCallback,
    warnings: list[str],
) -> ScenePlan:
    """Give still-plain scenes a calm image on the recording's theme (D-137).

    The theme is searched online once, when that is allowed, so the images
    exist; then each plain scene gets its own, never one already in the video,
    never an image of printed text, and always labelled atmospheric. Failures
    leave the scenes plain -- this is a nicety, not something to fail a render
    over.
    """
    from voxframe.library.db import AssetLibrary
    from voxframe.library.embeddings import EmbedderUnavailable
    from voxframe.library.ingest import ingest_directory
    from voxframe.match.atmosphere import apply_atmosphere, theme_queries
    from voxframe.match.text_detection import TextDetector

    plain = [
        scene for scene in plan.scenes
        if scene.asset is None and not scene.is_card and scene.asset_source != "user"
    ]
    if not plain:
        return plan

    queries = theme_queries(
        [scene.text for scene in plan.scenes if not scene.is_card], plan.language
    )
    if not queries:
        return plan

    progress(
        Stage.MATCHING,
        f"Choosing calm images on the recording's theme for {len(plain)} scenes",
        None,
    )

    if options.source_imagery:
        try:
            from voxframe.sourcing.registry import source_queries

            sourced = source_queries(
                queries,
                library / "sourced",
                settings,
                language=plan.language,
                per_query=len(plain) + 2,
            )
            if sourced.fetch.downloaded:
                ingest_directory(library / "sourced", AssetLibrary(library), embedder)
        except EmbedderUnavailable:
            raise
        except Exception as exc:
            log.warning(
                "pipeline.atmosphere_sourcing_failed",
                error=f"{type(exc).__name__}: {exc}",
            )

    asset_library = AssetLibrary(library)
    if not asset_library.all_assets():
        return plan

    detector = TextDetector(embedder, settings.resolved_embed_model)

    def shows_text(asset_id: str) -> bool:
        vector = asset_library.vectors([asset_id]).get(asset_id)
        return vector is not None and detector.shows_text(vector)

    updated = apply_atmosphere(
        plan,
        asset_library,
        embedder,
        queries,
        threshold=settings.resolved_similarity_threshold,
        shows_text=shows_text,
    )
    added = sum(
        1 for scene in updated.scenes if scene.asset_source == "atmospheric"
    )
    if added:
        one = added == 1
        warnings.append(
            f"{added} scene{'' if one else 's'} with no match of "
            f"{'its' if one else 'their'} own "
            f"{'shows a calm image' if one else 'show calm images'} on the "
            f"recording's theme, labelled \"atmospheric\" in the scene plan. "
            f"You can switch {'it' if one else 'any of them'} to a plain background."
        )
    return updated


def _flag_printed_text(
    plan: ScenePlan, library: Path, embedder: Embedder, model_key: str
) -> ScenePlan:
    """Mark every image the plan records whose subject is printed text.

    The chosen image and every candidate, so the scene plan can warn *before* a
    person picks one: a close match of "100%" in large type is otherwise a
    one-click way to put words under the captions (D-133).

    Uses the vectors stored at ingest, so no image is loaded again. A model
    without a measured threshold flags nothing rather than guessing.
    """
    from voxframe.library.db import AssetLibrary
    from voxframe.match.text_detection import TextDetector

    detector = TextDetector(embedder, model_key)
    if not detector.available:
        return plan

    def assets_of(scene: PlannedScene) -> list[PlanAsset]:
        return [
            *([scene.asset] if scene.asset else []),
            *scene.alternatives,
            *scene.near_misses,
        ]

    ids = [asset.id for scene in plan.scenes for asset in assets_of(scene)]
    if not ids:
        return plan

    vectors = AssetLibrary(library).vectors(ids)
    flagged = {
        asset_id for asset_id, vector in vectors.items() if detector.shows_text(vector)
    }
    if not flagged:
        return plan

    def mark(asset: PlanAsset) -> PlanAsset:
        return asset.model_copy(update={"prints_text": asset.id in flagged})

    scenes = tuple(
        scene.model_copy(
            update={
                "asset": mark(scene.asset) if scene.asset else None,
                "alternatives": tuple(mark(a) for a in scene.alternatives),
                "near_misses": tuple(mark(a) for a in scene.near_misses),
            }
        )
        for scene in plan.scenes
    )
    log.info("pipeline.printed_text", flagged=len(flagged), checked=len(set(ids)))
    return plan.model_copy(update={"scenes": scenes})


@dataclass(frozen=True, slots=True)
class _SourcingOutcome:
    """What a render's online search added, and anything worth telling."""

    added: int
    warnings: tuple[str, ...]


def _prepare_music(
    music: MusicSettings | None, progress: ProgressCallback, *, score: bool = False
) -> list[str]:
    """Fetch the music tools the first time a video needs them.

    They are a download on demand (D-172): about 91 MB nobody pays for unless
    they use their own music, or a generated score (whose filters and sample
    reading come from the same libraries, D-176). A failed download is said
    plainly: own music then plays as the plain loop, and a score is left out.
    """
    from voxframe.components import ComponentError, install_music, music_manifest, music_ready

    if music_ready():
        return []
    directed = music is not None and music.directed
    if not score and not directed:
        return []
    from voxframe.config.settings import get_settings

    if not score and get_settings().music_mode == "simple":
        return []
    manifest = music_manifest()
    if manifest is None:
        return []  # a pip install without the music extra: the director says so
    doing = "Preparing the music score" if score else "Preparing to fit your music to the speech"
    progress(
        Stage.RENDERING,
        f"{doing} (a one-time download of about {manifest.megabytes:.0f} MB)",
        0.0,
    )
    try:
        install_music(
            lambda done, total: progress(
                Stage.RENDERING,
                f"{doing} ({done:.0f} of {total:.0f} MB)",
                done / total if total else None,
            )
        )
    except ComponentError as exc:
        log.warning("components.music_failed", error=str(exc))
        if score:
            return [
                f"The tools that make the music score could not be downloaded this time "
                f"({exc}), so the video has no music. It will try again next time."
            ]
        return [
            "The tools that fit music to the speech could not be downloaded this "
            f"time ({exc}), so the music plays as a simple loop under it. It will "
            "try again next time."
        ]
    return []


def sound_warnings(result: RenderResult) -> list[str]:
    """A failed sound check, said on the video rather than shipped silently (D-171)."""
    sound = result.sound or {}
    problems = sound.get("problems")
    warnings = [f"Sound check: {p}" for p in problems] if isinstance(problems, list) else []
    polish = sound.get("polish")
    added_music = sound.get("min_speech_margin_db") is not None
    if isinstance(polish, dict) and polish.get("music_in_recording") and added_music:
        warnings.append(MUSIC_CLASH_WARNING)
    return warnings


#: Said when a music track is added to a recording that already has music.
MUSIC_CLASH_WARNING = (
    "Your recording already has music in it, so the added music plays on top: "
    "two layers of music may clash. Choosing \"No music\" may sound better."
)


def _limit_warnings(sourced: SourcingPlan) -> list[str]:
    """Say plainly when a limit stopped the search, and what to do (D-166).

    No fixed cap on scenes any more: the searches are shared and spread, and
    each source stops only when its own allowance is nearly used. When that
    happens, or the download cap is reached, the person is told which, and
    that making the video again continues from where this one stopped.
    """
    warnings: list[str] = []
    if sourced.unsearched:
        names = sorted({name.capitalize() for name, _, _ in sourced.resting})
        waits = [seconds for _, _, seconds in sourced.resting if seconds]
        when = f"in about {max(1, round(min(waits) / 60))} minutes" if waits else "in an hour"
        if not names:
            because = "no image service could be used"
        elif len(names) == 1:
            because = f"{names[0]} reached its request limit"
        else:
            because = f"{', '.join(names)} reached their request limits"
        count = len(sourced.unsearched)
        them = "it" if count == 1 else "them"
        warnings.append(
            f"{count} scene{'' if count == 1 else 's'} could not be searched online "
            f"because {because}. Make the video again {when} to search {them}: "
            f"everything found so far is kept, so only what is left is searched."
        )
    capped = sum(1 for _, reason in sourced.fetch.skipped if "download cap" in reason)
    if capped:
        warnings.append(
            f"Downloads stopped at the size limit for one video, so {capped} more "
            f"image{' was' if capped == 1 else 's were'} not fetched. Making the "
            f"video again fetches {'it' if capped == 1 else 'them'}."
        )
    return warnings


def _source_missing_imagery(
    plan: ScenePlan,
    library: Path,
    settings: Settings,
    embedder: Embedder,
    progress: ProgressCallback,
) -> _SourcingOutcome:
    """Search online for scenes the library could not fill, and add the results.

    The same steps as ``voxframe source`` followed by ``make`` (D-073..D-086),
    in one go: search with the scene's keywords, download into
    ``<library>/sourced`` where the files stay (D-074), add them to the library
    with their per-file licences (D-072), and let the caller re-match.

    Failures are reported, never raised. A person offline, or with a revoked
    key, should get their video with plain backgrounds and a sentence saying
    why -- not a failed render.
    """
    from voxframe.library.db import AssetLibrary
    from voxframe.library.ingest import ingest_directory
    from voxframe.sourcing import source_for_plan

    empty = [
        scene.index for scene in plan.scenes
        if scene.asset is None and not scene.is_card
    ]
    if not empty:
        return _SourcingOutcome(0, ())

    warnings: list[str] = []

    progress(
        Stage.MATCHING,
        f"Searching online for images for {len(empty)} scenes",
        None,
    )

    try:
        sourced = source_for_plan(plan, library / "sourced", settings)
    except Exception as exc:
        log.warning("pipeline.sourcing_failed", error=exc.__class__.__name__)
        return _SourcingOutcome(
            0,
            (
                "Searching online for images did not work this time, so scenes "
                "without a match show a plain background. Check your "
                "connection, or your keys in Settings.",
            ),
        )

    for name, _error in sourced.adapter_failures[:3]:
        # The error text can quote a request URL; only the source's name is
        # shown, which is what a person can act on (D-075).
        warnings.append(
            f"{name.capitalize()} could not be reached, so it was skipped. "
            f"Check your connection, or that key in Settings."
        )

    warnings.extend(_limit_warnings(sourced))

    if not sourced.fetch.downloaded and not sourced.fetch.reused:
        if not sourced.adapter_failures and not sourced.unsearched:
            warnings.append(
                "Searching online found nothing suitable for the scenes "
                "without a match."
            )
        return _SourcingOutcome(0, tuple(warnings))

    progress(
        Stage.MATCHING,
        f"Adding {sourced.fetch.count + len(sourced.fetch.reused)} images to your library",
        None,
    )
    ingested = ingest_directory(
        library / "sourced", AssetLibrary(library), embedder
    )

    log.info(
        "pipeline.sourced",
        scenes=len(empty),
        searched=sourced.searched_scenes,
        downloaded=sourced.fetch.count,
        reused=len(sourced.fetch.reused),
        added=len(ingested.added),
    )
    # Images an earlier render downloaded are already in the library; they
    # count, because the plan is matched afresh against all of it.
    return _SourcingOutcome(
        len(ingested.added) + len(sourced.fetch.reused), tuple(warnings)
    )


def attach_recording_footage(
    plan: ScenePlan,
    recording: Path,
    aspect: AspectRatio,
    height: int,
    capabilities: FFmpegCapabilities,
    *,
    cutaways: bool,
) -> tuple[ScenePlan, str]:
    """Put the recording's own picture in the plan, when it has one (D-192).

    Shared by both front ends, so a video made from the command line and from
    the app show the speaker the same way.

    Returns:
        ``(plan, warning)``. A recording with no picture, or one that cannot
        be read, keeps the pictures-only plan, and the warning says so: the
        person asked for their video, and must not be left wondering why it
        is not there. The warning is empty otherwise.
    """
    import numpy as np

    from voxframe.plan.scene_plan import Footage
    from voxframe.plan.shots import attach_footage
    from voxframe.render.compose.captioned import _dimensions
    from voxframe.render.encode.probe import FFmpegNotFound, probe_footage
    from voxframe.render.motion.speaker import find_speaker_x

    try:
        info = probe_footage(recording, capabilities)
    except (OSError, FFmpegNotFound) as exc:
        log.warning("footage.unreadable", error=str(exc))
        info = None
    if info is None:
        return plan, "This file has no picture to show, so the video uses pictures only."

    width, out_height = _dimensions(aspect, height)
    subject_x, source = 0.5, "centre"
    track: tuple[TrackPoint, ...] = ()
    # Only a crop smaller than the footage, one way or the other, can leave
    # the speaker out; the same shape shows the whole frame.
    output_shape, footage_shape = width / out_height, info.width / info.height
    if abs(output_shape - footage_shape) > 0.01:
        track = _follow_faces(recording, info, capabilities, output_shape, footage_shape)
        if track:
            subject_x, source = float(np.median([point.x for point in track])), "faces"
        elif output_shape < footage_shape:
            subject_x, source = find_speaker_x(recording, info, capabilities)
    footage = Footage(
        path=str(recording),
        width=info.width,
        height=info.height,
        fps=info.fps,
        duration=info.duration,
        audio_offset=info.audio_offset,
        subject_x=round(subject_x, 4),
        subject_source=source,
        track=track,
    )
    return attach_footage(plan, footage, cutaways=cutaways), ""


def _follow_faces(
    recording: Path,
    info: FootageInfo,
    capabilities: FFmpegCapabilities,
    output_shape: float,
    footage_shape: float,
) -> tuple[TrackPoint, ...]:
    """The camera's path following the speaker's face (D-193), or ``()``.

    Empty when faces cannot be looked for here or are found too rarely; the
    caller then falls back to where the picture moves. Never a reason for a
    video to fail.
    """
    from voxframe.plan.scene_plan import TrackPoint
    from voxframe.render.motion.faces import (
        detector_available,
        find_faces,
        plan_camera,
        primary_faces,
    )

    if not detector_available():
        log.info("faces.unavailable")
        return ()
    try:
        keyframes = plan_camera(
            primary_faces(find_faces(recording, info, capabilities)),
            crop_width=min(1.0, output_shape / footage_shape),
            crop_height=min(1.0, footage_shape / output_shape),
        )
    except Exception as exc:  # following faces improves a crop; it never fails a video
        log.warning("faces.failed", error=f"{type(exc).__name__}: {exc}")
        return ()
    if not keyframes:
        log.info("faces.too_rare")
        return ()
    log.info("faces.followed", keyframes=len(keyframes))
    return tuple(TrackPoint(t=k.t, x=k.x, y=k.y, h=k.h) for k in keyframes)


def _attach_footage(
    plan: ScenePlan,
    options: JobOptions,
    capabilities: FFmpegCapabilities,
    progress: ProgressCallback,
    warnings: list[str],
    *,
    cutaways: bool,
) -> ScenePlan:
    progress(Stage.PLANNING, "Placing you on screen", None)
    plan, warning = attach_recording_footage(
        plan, options.audio, options.aspect, options.height, capabilities,
        cutaways=cutaways,
    )
    if warning:
        warnings.append(warning)
    return plan


def _apply_highlights(
    plan: ScenePlan,
    audio: Path,
    options: JobOptions,
    capabilities: FFmpegCapabilities,
    progress: ProgressCallback,
) -> tuple[ScenePlan, Path, list[str]]:
    """Reduce a long plan to its highlights, cutting the audio to match.

    Returns:
        ``(plan, audio, warnings)``. Unchanged when the source is too short.
    """
    from voxframe.plan.highlights import (
        audio_ranges,
        build_highlights_plan,
        extract_highlight_audio,
        select_highlights,
    )

    assert options.highlights_seconds is not None
    selection = select_highlights(plan, target_seconds=options.highlights_seconds)

    if not selection.count:
        return plan, audio, ["Recording too short for highlights; rendering it all."]

    progress(
        Stage.PLANNING,
        f"Highlights: {selection.count} of {len(plan.scenes)} scenes",
        None,
    )

    # The audio must be cut to match, or the video plays the original's first N
    # seconds against scenes from throughout it (D-111).
    cut = extract_highlight_audio(
        audio,
        audio_ranges(plan, selection),
        options.output.parent / f"{options.output.stem}.highlights.wav",
        capabilities.ffmpeg_path,
    )

    return (
        build_highlights_plan(plan, selection),
        cut,
        [
            f"Highlights chose {selection.count} of {len(plan.scenes)} scenes "
            f"({selection.total_seconds:.0f}s) by speech density, imagery and "
            f"position — not by understanding the content."
        ],
    )


def _language_warnings(
    language: str, probability: float, *, forced: bool, restricted: bool
) -> list[str]:
    """Warn about a low-confidence detection.

    A misdetected language drops audio instead of erroring (D-061), so this is
    surfaced rather than left in the log.
    """
    if forced or probability >= LANGUAGE_CONFIDENCE_THRESHOLD:
        return []

    # Under a candidate set the probability is the model's own for the chosen
    # language, not renormalised (D-068), so it stays low even when the
    # restriction picked correctly. Advise the next step, not the taken one.
    advice = (
        f"re-run forcing {language} to skip detection"
        if restricted
        else "restrict detection to a candidate set, or force one language"
    )
    return [
        f"Language detected as {language} with low confidence "
        f"({probability:.0%}). If that is wrong, {advice}. A misdetected "
        f"language drops audio instead of erroring."
    ]


def _illustratable(plan: ScenePlan) -> int:
    """Scenes that could carry imagery, i.e. every scene that is not a card."""
    return sum(1 for scene in plan.scenes if not getattr(scene, "card_kind", ""))


def _on_speaker(plan: ScenePlan, scene: PlannedScene) -> bool:
    """Whether a scene shows the speaker (D-192), for any plan-like object."""
    return getattr(plan, "footage", None) is not None and getattr(scene, "shot", None) == "speaker"


def _imagery_warnings(
    plan: ScenePlan, *, library: Path | None, sourcing: bool = False
) -> list[str]:
    """Explain a mostly-gradient video rather than letting it look broken.

    A new user with an empty library and no API keys still gets a video, and it
    must say why it looks plain instead of leaving them to conclude the tool
    does not work (owner's decision 6).
    """
    # Cards are drawn text, not failed matches, so they are not counted. Nor is
    # a scene the person cleared on purpose: describing their own choice as
    # "found no suitable image" would be wrong (D-128). Nor a scene showing
    # the speaker, which is not a plain background (D-192).
    intentional = sum(
        1
        for scene in plan.scenes
        if not getattr(scene, "card_kind", "")
        and getattr(scene, "asset", None) is None
        and (getattr(scene, "asset_source", "") == "user" or _on_speaker(plan, scene))
    )
    total = _illustratable(plan) - intentional
    if not total:
        return []

    # A scene on the speaker is not counted as filled by its picture either:
    # it was left out of the total above.
    filled = plan.matched_scenes - sum(
        1
        for scene in plan.scenes
        if getattr(scene, "asset", None) is not None and _on_speaker(plan, scene)
    )
    if filled == total:
        return []

    # Advice limited to what works (D-131), restored in full now that both
    # halves do (D-146). Searching online is suggested only when it is off;
    # telling someone to switch on what is already on reads as the tool not
    # listening.
    library_advice = " Photos you add to your Library are used in future videos."
    also = (
        ""
        if sourcing
        else " Or turn on \"Search online for images\" in Settings."
    )

    if library is None:
        return [
            f"Your image library is empty, so all {total} scenes show a plain "
            f"background. You can add your own photo to any scene from the "
            f"scene plan.{also}{library_advice}"
        ]

    if filled == 0:
        return [
            f"None of the {total} scenes found an image, so all of them show a "
            f"plain background. You can add your own photo to any scene from "
            f"the scene plan.{also}{library_advice}"
        ]

    return [
        f"{total - filled} of {total} scenes found no suitable image and show a "
        f"plain background. You can add your own photo to any of them from the "
        f"scene plan.{also}"
    ]
