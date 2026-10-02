"""Voxframe command-line interface."""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

if TYPE_CHECKING:
    from typing import TextIO

from voxframe import __version__
from voxframe.config.settings import AspectRatio, QualityPreset, get_settings
from voxframe.config.style import BUILTIN_TEMPLATES, get_template
from voxframe.library import (
    AssetLibrary,
    Embedder,
    EmbeddingError,
    EmbeddingModelMismatch,
    IngestError,
    LibraryError,
    ingest_directory,
    reembed_library,
)
from voxframe.logging import configure_logging
from voxframe.match import Matcher, MatchError
from voxframe.models import LicenseInfo
from voxframe.plan import PlanError, ScenePlan, build_plan
from voxframe.render.audio import MusicSettings
from voxframe.render.compose import (
    RenderError,
    render_captioned_video,
    render_from_plan,
)
from voxframe.render.encode.probe import (
    FFmpegNotFound,
    probe_capabilities,
)
from voxframe.segment import segment_transcript
from voxframe.timeline import FrameGrid
from voxframe.transcribe import (
    LANGUAGE_CONFIDENCE_THRESHOLD,
    Transcriber,
    TranscriptionError,
)


def _safe_stdout() -> TextIO:
    """stdout that degrades unencodable characters instead of raising.

    Reconfigured in place where possible: replacing the stream would break
    output capture in tests and in callers that redirect it.
    """
    stream = sys.stdout
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            # A stream that cannot be reconfigured (a pipe under test, say) is
            # left alone; the encoding it already has is the one to use.
            pass
    return stream


app = typer.Typer(
    name="voxframe",
    help="Turn audio into captioned, visually dynamic video. Offline and free.",
    add_completion=False,
    no_args_is_help=True,
)
#: Windows consoles default to a legacy codepage (cp1252 here), which cannot
#: encode a contributor name like "徐" — and a Pixabay credit line containing
#: one crashed a render outright (D-094). Rich is told to replace what the
#: terminal cannot show rather than raise: a mangled character in a credit is
#: survivable, a failed render is not. The credits *file* is UTF-8 regardless,
#: so nothing is lost from the record itself.
console = Console(
    legacy_windows=False,
    safe_box=True,
    # errors="replace" on the underlying stream is what actually prevents the
    # UnicodeEncodeError; Rich passes it through to the writer.
    file=_safe_stdout(),
)

OK = "[green]OK[/green]"
MISSING = "[red]MISSING[/red]"
WARN = "[yellow]WARN[/yellow]"


@app.command()
def version() -> None:
    """Show the Voxframe version."""
    console.print(f"voxframe {__version__}")


@app.command()
def doctor() -> None:
    """Check that this machine can run Voxframe.

    Reports what is available, what is missing, and what to do about it.
    Exits non-zero if anything required is absent, so CI and setup scripts can
    depend on it.
    """
    console.print(Panel.fit(f"Voxframe {__version__} - environment check", style="bold"))

    problems: list[str] = []
    warnings: list[str] = []

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Component")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")

    # --- Python ---
    py = platform.python_version()
    py_ok = sys.version_info >= (3, 11)
    table.add_row("Python", OK if py_ok else MISSING, f"{py} ({platform.machine()})")
    if not py_ok:
        problems.append(f"Python 3.11+ required, found {py}")

    table.add_row("Platform", OK, f"{platform.system()} {platform.release()}")

    # --- FFmpeg ---
    settings = get_settings()
    caps = None
    try:
        caps = probe_capabilities(settings.ffmpeg_path)
        table.add_row("FFmpeg", OK, f"{caps.version} at {caps.ffmpeg_path}")

        if caps.ffprobe_path:
            table.add_row("ffprobe", OK, caps.ffprobe_path)
        else:
            table.add_row("ffprobe", WARN, "not found; duration detection will be limited")
            warnings.append("ffprobe not found — usually ships alongside ffmpeg")

        # libass is non-negotiable: without it there are no captions at all.
        if caps.has_libass:
            table.add_row("libass (captions)", OK, "ass + subtitles filters available")
        else:
            table.add_row("libass (captions)", MISSING, "no ass/subtitles filter")
            problems.append(
                "This FFmpeg build lacks libass, so captions cannot be rendered. "
                "Install a full build (Windows: winget install Gyan.FFmpeg)."
            )

        for label, present, note in (
            ("zoompan (Ken Burns)", caps.has_zoompan, "motion effects"),
            ("xfade (transitions)", caps.has_xfade, "crossfades"),
        ):
            if present:
                table.add_row(label, OK, note)
            else:
                table.add_row(label, WARN, f"{note} will fall back")
                warnings.append(f"{label} unavailable — {note} degraded")

        # --- Encoders ---
        try:
            best = caps.best_encoder()
            missing = caps.missing_from_chain()
            detail = f"using {best}"
            if missing:
                detail += f"  (absent: {', '.join(missing)})"
            table.add_row("Video encoder", OK, detail)
        except FFmpegNotFound as exc:
            table.add_row("Video encoder", MISSING, str(exc).splitlines()[0])
            problems.append("No usable video encoder")

        for rf in ("libvpx-vp9", "libaom-av1", "libsvtav1"):
            if rf in caps.encoders:
                table.add_row("Royalty-free output", OK, f"{rf} available")
                break
        else:
            table.add_row("Royalty-free output", WARN, "no VP9/AV1 encoder")
            warnings.append(
                "No VP9 or AV1 encoder — only H.264/H.265 output will be available"
            )

    except FFmpegNotFound as exc:
        table.add_row("FFmpeg", MISSING, "not found on PATH")
        problems.append(str(exc))

    # --- Optional: GPU ---
    try:
        import torch

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            table.add_row("GPU", OK, f"{name} ({vram:.1f} GB)")
        else:
            table.add_row(
                "GPU", WARN, f"torch {torch.__version__} has no CUDA; using CPU"
            )
    except ImportError:
        table.add_row("GPU", WARN, "torch not installed (needed from Phase 3)")

    # --- Sourcing adapters (Phase 4) ---
    from voxframe.sourcing import build_adapters
    from voxframe.sourcing.secrets import mask

    active = [adapter.name for adapter in build_adapters(settings)]
    keyed = {
        "pexels": settings.pexels_api_key or "",
        "pixabay": settings.pixabay_api_key or "",
    }

    for name, secret in keyed.items():
        if name in active:
            table.add_row(f"Source: {name}", OK, f"key {mask(secret)}")
        else:
            table.add_row(
                f"Source: {name}", WARN, "no key set; this source is skipped"
            )

    table.add_row(
        "Source: openverse",
        OK if "openverse" in active else WARN,
        "no key needed",
    )

    console.print(table)
    console.print()

    if warnings:
        console.print("[yellow]Warnings[/yellow] — Voxframe will run, with limits:")
        for w in warnings:
            console.print(f"  - {w}")
        console.print()

    if problems:
        console.print("[red]Problems[/red] — these must be fixed:")
        for p in problems:
            console.print(f"  - {p}")
        raise typer.Exit(code=1)

    console.print("[green]Ready.[/green] Run [bold]voxframe make <audio>[/bold] to start.")


@app.command()
def make(
    audio: Annotated[Path, typer.Argument(help="Audio file to turn into video.")],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Output video path.")
    ] = None,
    aspect: Annotated[
        str, typer.Option("--aspect", help="9:16, 16:9, or 1:1.")
    ] = "16:9",
    quality: Annotated[
        str, typer.Option("--quality", help="draft, standard, high, or ultra.")
    ] = "standard",
    height: Annotated[int, typer.Option("--height", help="Output height in pixels.")] = 1080,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help=(
                "Whisper size: tiny, base, small, medium, large-v3, "
                "large-v3-turbo. Unset follows --profile."
            ),
        ),
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            help=(
                "Model profile: standard (~3 GB, best accuracy) or lite "
                "(~750 MB, weaker French). Sets transcription and embedding "
                "models together."
            ),
        ),
    ] = None,
    language: Annotated[
        str | None,
        typer.Option(
            "--language",
            help=(
                "Force a language code, e.g. en or fr. Set VOXFRAME_LANGUAGE "
                "to avoid passing it every time."
            ),
        ),
    ] = None,
    languages: Annotated[
        str | None,
        typer.Option(
            "--languages",
            help=(
                "Restrict detection to a candidate set, e.g. 'en,fr'. The most "
                "probable allowed language wins. Keeps detection working while "
                "ruling out implausible answers."
            ),
        ),
    ] = None,
    style_name: Annotated[
        str | None, typer.Option("--style", help="Style template name.")
    ] = None,
    title: Annotated[
        str | None,
        typer.Option(
            "--title",
            help=(
                "Show a title card before the video. Never derived from the "
                "filename, so a card appears only when you pass one (D-092)."
            ),
        ),
    ] = None,
    highlights: Annotated[
        float | None,
        typer.Option(
            "--highlights",
            help=(
                "Make a short video of this many seconds from a long "
                "recording. Selects passages by speech density, imagery and "
                "position, not by summarising (D-107)."
            ),
        ),
    ] = None,
    show_video: Annotated[
        bool,
        typer.Option(
            "--video/--no-video",
            help=(
                "Show the recording's own picture, the speaker in sync, with "
                "matched pictures cutting in (D-192). For a video file; an "
                "audio file has no picture and keeps pictures only."
            ),
        ),
    ] = False,
    music_path: Annotated[
        Path | None,
        typer.Option(
            "--music",
            help=(
                "A music bed to mix under the narration. Your own file: "
                "Voxframe does not source music (D-091)."
            ),
        ),
    ] = None,
    music_credit: Annotated[
        str | None,
        typer.Option(
            "--music-credit",
            help="Attribution for the music track, written into the credits.",
        ),
    ] = None,
    library: Annotated[
        Path | None,
        typer.Option("--library", help="Image library to match scenes against."),
    ] = None,
    plan_out: Annotated[
        Path | None,
        typer.Option("--plan", help="Write the editable scene plan here."),
    ] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Re-transcribe.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show progress logs.")] = False,
) -> None:
    """Render a captioned video from an audio file.

    With ``--library``, scenes are matched to images from that library and a
    scene plan is written alongside the video. The plan is editable: change an
    image, a query or a timing and re-render to get exactly that.
    """
    configure_logging("info" if verbose else "warning")

    if not audio.is_file():
        console.print(f"[red]Audio file not found:[/red] {audio}")
        raise typer.Exit(code=1)

    # CLI flags override environment and .env (D-067, D-068).
    settings = get_settings(profile=profile)

    try:
        caps = probe_capabilities(settings.ffmpeg_path)
    except FFmpegNotFound as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        aspect_ratio = AspectRatio(aspect)
    except ValueError:
        options = ", ".join(a.value for a in AspectRatio)
        console.print(f"[red]Unknown aspect {aspect!r}.[/red] Choose from: {options}")
        raise typer.Exit(code=1) from None

    try:
        quality_preset = QualityPreset(quality)
    except ValueError:
        options = ", ".join(q.value for q in QualityPreset)
        console.print(f"[red]Unknown quality {quality!r}.[/red] Choose from: {options}")
        raise typer.Exit(code=1) from None

    try:
        template = get_template(style_name)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    destination = output or settings.output_path / f"{audio.stem}.mp4"

    music_settings = None
    if music_path is not None:
        if not music_path.is_file():
            console.print(f"[red]Music track not found:[/red] {music_path}")
            raise typer.Exit(code=1)
        music_settings = MusicSettings(
            path=music_path, credit=music_credit or ""
        )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Transcribing (first run downloads the model)...", total=None)
        try:
            transcriber = Transcriber(
                model or settings.resolved_transcribe_model,
                cache_dir=settings.cache_path,
                use_gpu=settings.use_gpu,
                language=language or settings.language,
                allowed_languages=(
                    tuple(c.strip() for c in languages.split(",") if c.strip())
                    if languages
                    else settings.allowed_languages
                ),
            )
            transcript = transcriber.transcribe(audio, force=no_cache)
        except TranscriptionError as exc:
            progress.stop()
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc

        progress.update(task, description="Segmenting into scenes...")
        grid = FrameGrid(fps=settings.fps, audio_duration=transcript.duration)
        scenes = segment_transcript(transcript, grid, template.pacing)

        # --- matching, when a library is supplied ---
        plan = None
        if library is not None:
            progress.update(task, description="Matching scenes to images...")
            try:
                asset_library = AssetLibrary(library)
                embedder = Embedder(
                    use_gpu=settings.use_gpu, model_key=settings.resolved_embed_model
                )
                matcher = Matcher(asset_library, embedder)
                matches = matcher.match_scenes(
                    scenes, transcript.language, aspect=aspect_ratio
                )
                plan = build_plan(
                    audio, transcript, scenes, matches, grid, template,
                    aspect=aspect_ratio, embed_model=embedder.model_id,
                )
            except (MatchError, EmbeddingModelMismatch, LibraryError) as exc:
                progress.stop()
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc

        elif plan_out is not None or show_video:
            # A plan without imagery still carries caption text and word
            # timings, which is what caption correction needs. Asking for a plan
            # should produce one whether or not a library was supplied.
            plan = build_plan(
                audio, transcript, scenes, None, grid, template, aspect=aspect_ratio
            )

        if plan is not None and show_video:
            from voxframe.jobs.pipeline import attach_recording_footage

            progress.update(task, description="Placing you on screen...")
            plan, footage_warning = attach_recording_footage(
                plan, audio, aspect_ratio, height, caps, cutaways=library is not None
            )
            if footage_warning:
                console.print(f"  [yellow]{footage_warning}[/yellow]")

        if plan is not None and (title or len(scenes) > 1):
            from voxframe.plan.builder import insert_cards

            plan = insert_cards(plan, title=title or "")

        if plan is not None and highlights:
            from voxframe.plan import build_highlights_plan, select_highlights

            selection = select_highlights(plan, target_seconds=highlights)
            if selection.count:
                console.print(
                    f"  Highlights: {selection.count} of {len(plan.scenes)} "
                    f"scenes, {selection.total_seconds:.0f}s"
                )
                console.print(
                    "  [yellow]Note:[/yellow] chosen by speech density, "
                    "imagery and position — not by understanding the content."
                )
                # The audio must be cut to match, or the video plays the
                # original's first N seconds against scenes from throughout
                # it and the captions describe words nobody is saying
                # (D-111).
                from voxframe.plan.highlights import (
                    audio_ranges,
                    extract_highlight_audio,
                )

                ranges = audio_ranges(plan, selection)
                audio = extract_highlight_audio(
                    audio,
                    ranges,
                    destination.parent / f"{destination.stem}.highlights.wav",
                    caps.ffmpeg_path,
                )
                plan = build_highlights_plan(plan, selection)
                if plan.footage is not None:
                    from voxframe.plan.shots import choose_shots

                    plan = choose_shots(plan, cutaways=library is not None)
            else:
                console.print(
                    "  [yellow]Recording too short for highlights;[/yellow] "
                    "rendering it all."
                )

        if plan is not None and music_settings is not None:
            # Recorded in the plan so the credits file describes what was
            # rendered and a re-render reproduces the same mix (D-012).
            plan = plan.model_copy(
                update={
                    "music_path": str(music_settings.path),
                    "music_credit": music_settings.credit,
                }
            )

        progress.update(task, description=f"Rendering {len(scenes)} scenes...")
        try:
            if plan is not None:
                # The plan drives the render: motion, imagery and timing all
                # come from it, so editing it and re-rendering gives exactly
                # what was asked for (D-011).
                result = render_from_plan(
                    plan, audio, template, caps, destination,
                    quality=quality_preset, height=height,
                    music=music_settings,
                    cache_dir=settings.cache_path / "segments",
                )
            else:
                result = render_captioned_video(
                    audio, scenes, grid, template, caps, destination,
                    aspect=aspect_ratio, quality=quality_preset, height=height,
                )
        except RenderError as exc:
            progress.stop()
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc

    # The plan is the editable record of every decision (D-011).
    if plan is not None:
        plan_path = plan_out or result.video_path.with_suffix(".plan.json")
        plan.save(plan_path)

    # A wrong language silently truncates the transcript rather than failing,
    # so it is surfaced here as well as in the log (D-061).
    if (language is None
            and transcript.language_probability < LANGUAGE_CONFIDENCE_THRESHOLD):
        # Under a candidate set the probability is the model's own for the
        # chosen language, not renormalised (D-068), so it stays low even when
        # the restriction picked correctly. Advise the next step rather than
        # the one already taken.
        restricted = bool(languages or settings.allowed_languages)
        advice = (
            f"re-run with [bold]--language {transcript.language}[/bold] to "
            f"skip detection"
            if restricted
            else "re-run with [bold]--languages en,fr[/bold] to restrict "
            "detection, or [bold]--language en[/bold] to skip it"
        )
        console.print(
            f"[yellow]Warning:[/yellow] language detected as "
            f"[bold]{transcript.language}[/bold] with low confidence "
            f"({transcript.language_probability:.0%}). If that is wrong, "
            f"{advice}. A misdetected language drops audio instead of erroring."
        )

    console.print(f"[green]Done.[/green] {result.video_path}")
    console.print(
        f"  {result.width}x{result.height} - {len(scenes)} scenes - "
        f"{transcript.word_count} words - {transcript.language}"
    )
    console.print(
        f"  Rendered in {result.elapsed_seconds:.1f}s "
        f"({result.realtime_factor:.2f}x audio length)"
    )
    if result.srt_path and result.vtt_path:
        console.print(f"  Subtitles: {result.srt_path.name}, {result.vtt_path.name}")

    if plan is not None:
        console.print(
            f"  Matched {plan.matched_scenes}/{len(plan.scenes)} scenes to "
            f"{plan.unique_assets} images"
        )
        if plan.footage is not None:
            console.print(
                f"  On the speaker: {plan.speaker_scenes} scenes; "
                f"cutaways: {len(plan.scenes) - plan.speaker_scenes} "
                f"(including cards)"
            )
        console.print(f"  Scene plan: {plan_path.name} (edit and re-render)")
        for line in plan.credits():
            console.print(f"    credit: {line}")


@app.command()
def render(
    plan_file: Annotated[
        Path, typer.Argument(help="A scene plan written by `make --plan`.")
    ],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Output video path.")
    ] = None,
    audio: Annotated[
        Path | None,
        typer.Option(
            "--audio",
            help="Override the audio path, if the plan was moved to another machine.",
        ),
    ] = None,
    quality: Annotated[
        str, typer.Option("--quality", help="draft, standard, high, or ultra.")
    ] = "standard",
    height: Annotated[
        int, typer.Option("--height", help="Output height in pixels.")
    ] = 1080,
    style_name: Annotated[
        str | None, typer.Option("--style", help="Style template name.")
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show progress logs.")
    ] = False,
) -> None:
    """Re-render a video from an edited scene plan.

    This is what makes the plan editable rather than a debug dump. Change a
    caption, swap an image, retime a scene, then render again and get exactly
    that — nothing upstream is consulted (D-011).

    To correct a misheard word, set the scene's ``caption_text`` to the right
    text and leave ``words`` alone. Word timings are re-derived, including when
    a correction splits one word into two or merges two into one (D-062).
    """
    configure_logging("info" if verbose else "warning")
    settings = get_settings()

    try:
        plan = ScenePlan.load(plan_file)
    except PlanError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if plan.is_pre_release:
        from voxframe.jobs.pipeline import PRE_RELEASE_PLAN_WARNING

        console.print(f"[yellow]{PRE_RELEASE_PLAN_WARNING}[/yellow]")

    audio_path = audio or Path(plan.audio_path)
    if not audio_path.is_file():
        console.print(
            f"[red]Audio not found:[/red] {audio_path}\n"
            "The plan records where the audio was when it was made. "
            "Pass [bold]--audio[/bold] if it has moved."
        )
        raise typer.Exit(code=1)

    try:
        quality_preset = QualityPreset(quality)
    except ValueError:
        options = ", ".join(q.value for q in QualityPreset)
        console.print(f"[red]Unknown quality {quality!r}.[/red] Choose from: {options}")
        raise typer.Exit(code=1) from None

    try:
        # The plan records which style made it, so re-rendering reproduces the
        # original unless a different one is asked for explicitly.
        template = get_template(style_name or plan.style)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        caps = probe_capabilities()
    except FFmpegNotFound as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    destination = output or plan_file.with_suffix(".mp4")

    corrected = [scene.index for scene in plan.scenes if scene.is_corrected]
    if corrected:
        console.print(
            f"  Applying caption corrections to "
            f"{len(corrected)} scene(s): {', '.join(str(i) for i in corrected)}"
        )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(f"Rendering {len(plan.scenes)} scenes from plan...", total=None)
        try:
            result = render_from_plan(
                plan, audio_path, template, caps, destination,
                quality=quality_preset, height=height,
                # The resume path: re-rendering an edited plan reuses every
                # segment whose inputs are unchanged (D-101).
                cache_dir=settings.cache_path / "segments",
            )
        except RenderError as exc:
            progress.stop()
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc

    console.print(f"[green]Done.[/green] {result.video_path}")
    console.print(
        f"  {result.width}x{result.height} - {len(plan.scenes)} scenes - "
        f"{plan.matched_scenes} matched"
    )
    console.print(
        f"  Rendered in {result.elapsed_seconds:.1f}s "
        f"({result.realtime_factor:.2f}x audio length)"
    )
    for line in plan.credits():
        console.print(f"    credit: {line}")


@app.command()
def retranscribe(
    plan_file: Annotated[
        Path, typer.Argument(help="The plan whose transcript should be rebuilt.")
    ],
    model: Annotated[
        str | None,
        typer.Option("--model", help="Whisper size to use instead of the plan's."),
    ] = None,
    language: Annotated[
        str | None, typer.Option("--language", help="Force a language code.")
    ] = None,
    languages: Annotated[
        str | None,
        typer.Option("--languages", help="Restrict detection, e.g. 'en,fr'."),
    ] = None,
    audio: Annotated[
        Path | None, typer.Option("--audio", help="Override the plan's audio path.")
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the new plan here instead."),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")
    ] = False,
) -> None:
    """Rebuild a plan's transcript with a different model or language.

    Rendering never re-transcribes, so a plan's transcript is stable however
    many times it is rendered (D-067). This is the one command that changes it,
    and it is separate precisely because the change is destructive: new word
    timings mean the caption corrections in the plan no longer align with the
    words they were written against.

    Corrections are **not** carried over. Re-applying a correction to a
    different transcript would silently produce something the user never
    approved — a correction is a statement about specific words, and those
    words may not survive. The affected scenes are listed so they can be
    redone deliberately.
    """
    configure_logging("warning")
    settings = get_settings()

    try:
        plan = ScenePlan.load(plan_file)
    except PlanError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    audio_path = audio or Path(plan.audio_path)
    if not audio_path.is_file():
        console.print(
            f"[red]Audio not found:[/red] {audio_path}\n"
            "Pass [bold]--audio[/bold] if it has moved."
        )
        raise typer.Exit(code=1)

    target_model = model or settings.resolved_transcribe_model
    corrected = [scene.index for scene in plan.scenes if scene.is_corrected]

    console.print(f"Plan:       {plan_file}")
    console.print(f"  transcribed with: {plan.transcribe_model or 'unrecorded'}")
    console.print(f"  will now use:     {target_model}")
    console.print(f"  language:         {plan.language} -> {language or 'detect'}")

    if corrected:
        console.print()
        console.print(
            f"[yellow]Warning:[/yellow] {len(corrected)} scene(s) carry caption "
            f"corrections: {', '.join(str(i) for i in corrected)}"
        )
        console.print(
            "  These will be [bold]lost[/bold]. A correction is a statement "
            "about specific words,\n"
            "  and a new transcript may not contain those words. Re-applying "
            "it blindly would\n"
            "  produce text you never approved."
        )
        for index in corrected[:5]:
            scene = plan.scenes[index]
            console.print(f"    scene {index}: {scene.caption_text[:70]!r}")
        if len(corrected) > 5:
            console.print(f"    ... and {len(corrected) - 5} more")

    if not yes:
        console.print()
        if not typer.confirm("Re-transcribe?", default=not corrected):
            console.print("Nothing changed.")
            raise typer.Exit(code=0)

    allowed = (
        tuple(c.strip() for c in languages.split(",") if c.strip())
        if languages
        else settings.allowed_languages
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(f"Transcribing with {target_model}...", total=None)
        try:
            transcript = Transcriber(
                target_model,
                cache_dir=settings.cache_path,
                use_gpu=settings.use_gpu,
                language=language or settings.language,
                allowed_languages=allowed,
            ).transcribe(audio_path, force=True)
        except TranscriptionError as exc:
            progress.stop()
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc

        progress.add_task("Rebuilding scenes...", total=None)
        template = get_template(plan.style)
        grid = FrameGrid(fps=plan.fps, audio_duration=transcript.duration)
        scenes = segment_transcript(transcript, grid, template.pacing)

        rebuilt = build_plan(
            audio_path, transcript, scenes, None, grid, template, aspect=plan.aspect
        )

    destination = output or plan_file
    rebuilt.save(destination)

    console.print(f"[green]Done.[/green] {destination}")
    console.print(
        f"  {transcript.word_count} words - {transcript.language} "
        f"(p={transcript.language_probability:.2f}) - {len(scenes)} scenes"
    )
    if corrected:
        console.print(
            f"  [yellow]{len(corrected)} caption correction(s) were "
            f"discarded.[/yellow]"
        )
    if plan.matched_scenes:
        console.print(
            f"  [yellow]Image matches were not carried over[/yellow] "
            f"({plan.matched_scenes} scene(s) had one). Scene boundaries moved, "
            f"so re-run [bold]make --library[/bold] to rematch."
        )


@app.command()
def sources() -> None:
    """List the media sources Voxframe can fetch from.

    Shows which are usable right now. A source needing an API key that is not
    configured is listed as unavailable rather than hidden, so it is clear
    what enabling it would add.
    """
    from voxframe.sourcing import DEFAULT_POLICY, build_adapters
    from voxframe.sourcing.openverse import OpenverseAdapter
    from voxframe.sourcing.pexels import PexelsAdapter
    from voxframe.sourcing.pixabay import PixabayAdapter
    from voxframe.sourcing.secrets import mask

    settings = get_settings()
    active = {adapter.name for adapter in build_adapters(settings)}

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Source")
    table.add_column("Key")
    table.add_column("Status")
    table.add_column("Terms", overflow="fold")

    # Constructed with their keys so `available()` is truthful, but only the
    # masked tail is ever displayed.
    catalogue = (
        (PexelsAdapter(settings.pexels_api_key), settings.pexels_api_key or ""),
        (PixabayAdapter(settings.pixabay_api_key), settings.pixabay_api_key or ""),
        (OpenverseAdapter(), ""),
    )

    for adapter, secret in catalogue:
        if not adapter.requires_key:
            key_column = "not needed"
        elif secret:
            key_column = mask(secret)
        else:
            key_column = "not set"

        table.add_row(
            adapter.name,
            key_column,
            OK if adapter.name in active else WARN,
            adapter.terms,
        )

    console.print(table)
    console.print()
    console.print(f"Search order: [bold]{settings.source_order}[/bold]")
    console.print(
        "  'local' is your own library, always searched first. Adapters "
        "without a key are skipped."
    )
    console.print()

    policy = DEFAULT_POLICY
    console.print("[bold]License policy[/bold]")
    console.print(
        f"  attribution   {'accepted' if policy.allow_attribution else 'excluded'}"
        f"   (Voxframe always writes a credits file)"
    )
    console.print(
        f"  ShareAlike    {'accepted' if policy.allow_share_alike else 'excluded'}"
        f"   (would require your video to carry the same license)"
    )
    console.print(
        f"  NonCommercial {'accepted' if policy.allow_non_commercial else 'excluded'}"
        f"   (you could not sell the result)"
    )
    console.print(
        "  NoDerivatives excluded   (always: Ken Burns crops and scales)"
    )


@app.command()
def source(
    plan_file: Annotated[
        Path, typer.Argument(help="A scene plan written by `make --plan`.")
    ],
    library: Annotated[
        Path | None,
        typer.Option("--library", help="Library to add the fetched assets to."),
    ] = None,
    per_scene: Annotated[
        int, typer.Option("--per-scene", help="Candidates to fetch per empty scene.")
    ] = 4,
    all_scenes: Annotated[
        bool,
        typer.Option(
            "--all-scenes",
            help="Source for every scene, not only the ones with no image.",
        ),
    ] = False,
    allow_share_alike: Annotated[
        bool,
        typer.Option(
            "--allow-share-alike",
            help=(
                "Accept ShareAlike licenses. Your video would then have to "
                "carry the same license."
            ),
        ),
    ] = False,
    allow_non_commercial: Annotated[
        bool,
        typer.Option(
            "--allow-non-commercial",
            help="Accept NonCommercial licenses. You could not sell the result.",
        ),
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show progress logs.")
    ] = False,
) -> None:
    """Fetch imagery for a plan's empty scenes.

    Searches open sources for what each unfilled scene is about, downloads the
    best candidates with their provenance, and adds them to the library. Then
    re-run `make --library` to match them in, or `render` if the plan is
    already how you want it.

    Every asset keeps its own license, author and source URL, so the credits
    file describes exactly what was used (D-012, D-072).
    """
    configure_logging("info" if verbose else "warning")
    settings = get_settings()

    try:
        plan = ScenePlan.load(plan_file)
    except PlanError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    from voxframe.sourcing import LicensePolicy, source_for_plan

    policy = LicensePolicy(
        allow_share_alike=allow_share_alike,
        allow_non_commercial=allow_non_commercial,
    )

    if allow_share_alike:
        console.print(
            "[yellow]ShareAlike enabled.[/yellow] A video using these assets "
            "may have to be released under the same license."
        )
    if allow_non_commercial:
        console.print(
            "[yellow]NonCommercial enabled.[/yellow] You will not be able to "
            "sell or monetise the result."
        )

    library_path = library or settings.library_path

    # Downloads live inside the library permanently. The library stores a path
    # per asset, so anything deleted after ingest leaves rows pointing at
    # files that no longer exist (D-074).
    staging = library_path / "sourced"

    empty = sum(1 for scene in plan.scenes if scene.asset is None)
    console.print(
        f"Plan: {len(plan.scenes)} scene(s), {empty} with no image "
        f"({empty / len(plan.scenes):.0%})"
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Searching open sources...", total=None)
        sourced = source_for_plan(
            plan, staging, settings,
            policy=policy, per_scene=per_scene, only_empty=not all_scenes,
        )

    if sourced.adapter_failures:
        for name, error in sourced.adapter_failures[:3]:
            console.print(f"  [yellow]{name}:[/yellow] {error}")

    if not sourced.fetch.downloaded:
        console.print("[yellow]Nothing was downloaded.[/yellow]")
        console.print(f"  {sourced.summary()}")
        raise typer.Exit(code=1)

    console.print(f"  Downloaded {sourced.fetch.count} file(s) to {staging}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Embedding and adding to the library...", total=None)
        try:
            asset_library = AssetLibrary(library_path)
            embedder = Embedder(
                use_gpu=settings.use_gpu, model_key=settings.resolved_embed_model
            )
            # No blanket license: every downloaded file carries its own
            # sidecar, which ingest reads per asset (D-072).
            ingested = ingest_directory(staging, asset_library, embedder)
        except (IngestError, EmbeddingError, LibraryError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc

    console.print(f"[green]Done.[/green] {ingested.summary()}")

    licenses = sorted({asset.license.name for asset in ingested.added})
    if licenses:
        console.print(f"  Licenses: {', '.join(licenses)}")

    console.print()
    console.print("Next: re-match the plan against the enlarged library with")
    console.print(
        f"  [bold]voxframe make <audio> --library {library_path} "
        f"--plan {plan_file}[/bold]"
    )


@app.command()
def cache(
    clear: Annotated[
        bool,
        typer.Option("--clear", help="Delete cached data after showing it."),
    ] = False,
    what: Annotated[
        str,
        typer.Option(
            "--what",
            help="Which cache: all, transcripts, segments, or sourcing.",
        ),
    ] = "all",
) -> None:
    """Show what Voxframe has cached, and optionally clear it.

    Three caches, each for a different reason:

    **transcripts** — transcription dominates the cost of a short render, so
    re-rendering never re-transcribes.

    **segments** — a rendered scene is a pure function of its inputs (D-101),
    so a render that fails partway resumes rather than restarting.

    **sourcing** — API responses, cached 24 hours because Pixabay's terms
    require it (D-080).

    Clearing is safe: everything here can be regenerated, at the cost of time
    and, for sourcing, network requests.
    """
    configure_logging("warning")
    settings = get_settings()

    known = {
        "transcripts": settings.cache_path / "transcripts",
        "segments": settings.cache_path / "segments",
        "sourcing": settings.cache_path / "sourcing",
    }

    if what != "all" and what not in known:
        console.print(
            f"[red]Unknown cache {what!r}.[/red] "
            f"Choose from: all, {', '.join(sorted(known))}"
        )
        raise typer.Exit(code=1)

    selected = known if what == "all" else {what: known[what]}

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Cache")
    table.add_column("Entries", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Location", overflow="fold")

    total_bytes = 0

    for name, directory in sorted(selected.items()):
        if directory.is_dir():
            files = [p for p in directory.rglob("*") if p.is_file()]
            size = sum(p.stat().st_size for p in files)
        else:
            files, size = [], 0

        total_bytes += size
        table.add_row(
            name,
            str(len(files)),
            f"{size / (1024 * 1024):.1f} MB" if size else "-",
            str(directory),
        )

    console.print(table)
    console.print()
    console.print(f"Total: [bold]{total_bytes / (1024 * 1024):.1f} MB[/bold]")

    if not clear:
        return

    removed = 0
    for directory in selected.values():
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
            removed += 1

    console.print(f"[green]Cleared[/green] {removed} cache(s).")


@app.command()
def styles() -> None:
    """List the available style templates."""
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Name")
    table.add_column("Description", overflow="fold")

    for name, template in sorted(BUILTIN_TEMPLATES.items()):
        table.add_row(name, template.description)

    console.print(table)
    console.print()
    console.print(
        "Pass one with [bold]--style[/bold]. They differ in pacing, motion, "
        "transitions and caption style (D-098)."
    )


@app.command()
def ingest(
    directory: Annotated[Path, typer.Argument(help="Folder of images to ingest.")],
    author: Annotated[
        str, typer.Option("--author", help="Who to credit for these images.")
    ],
    license_name: Annotated[
        str, typer.Option("--license", help="License, e.g. CC0-1.0, CC-BY-4.0.")
    ],
    library: Annotated[
        Path | None, typer.Option("--library", help="Library folder.")
    ] = None,
    source: Annotated[
        str, typer.Option("--source", help="Where these came from.")
    ] = "local",
    source_url: Annotated[
        str, typer.Option("--url", help="Canonical URL, if there is one.")
    ] = "",
    non_commercial: Annotated[
        bool,
        typer.Option("--non-commercial", help="Mark as forbidding commercial use."),
    ] = False,
    embed_model: Annotated[
        str | None,
        typer.Option("--embed-model", help="'default' (best) or 'lite' (smaller)."),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Add images to the library.

    Attribution is required, not optional: every asset must be creditable, so
    the credits file always describes what a render actually used.
    """
    configure_logging("info" if verbose else "warning")

    if not directory.is_dir():
        console.print(f"[red]Not a directory:[/red] {directory}")
        raise typer.Exit(code=1)

    settings = get_settings()
    library_path = library or settings.library_path

    try:
        license_info = LicenseInfo(
            name=license_name,
            author=author,
            source=source,
            source_url=source_url,
            allows_commercial=not non_commercial,
        )
    except ValueError as exc:
        console.print(f"[red]Invalid attribution:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    asset_library = AssetLibrary(library_path)
    embedder = Embedder(
        use_gpu=settings.use_gpu, model_key=embed_model or settings.resolved_embed_model
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Ingesting (first run downloads the model)...", total=None)
        try:
            result = ingest_directory(directory, asset_library, embedder, license_info)
        except (IngestError, EmbeddingError) as exc:
            progress.stop()
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc

    console.print(f"[green]{result.summary()}[/green]")
    console.print(f"  Library: {library_path} ({asset_library.count()} assets)")

    if result.near_duplicates:
        console.print(f"  [yellow]{len(result.near_duplicates)} near-duplicates kept[/yellow]")
    for path, error in result.failed[:5]:
        console.print(f"  [red]failed:[/red] {path.name}: {error[:60]}")


@app.command()
def library_info(
    library: Annotated[
        Path | None, typer.Option("--library", help="Library folder.")
    ] = None,
) -> None:
    """Show what the library contains."""
    settings = get_settings()
    asset_library = AssetLibrary(library or settings.library_path)

    count = asset_library.count()
    if count == 0:
        console.print("Library is empty. Add images with [bold]voxframe ingest[/bold].")
        return

    assets = asset_library.all_assets()
    licenses: dict[str, int] = {}
    for asset in assets:
        licenses[asset.license.name] = licenses.get(asset.license.name, 0) + 1

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("License")
    table.add_column("Assets", justify="right")
    for name, number in sorted(licenses.items(), key=lambda pair: -pair[1]):
        table.add_row(name, str(number))

    console.print(f"[bold]{count}[/bold] assets in {asset_library.root}")
    console.print(table)

    non_commercial = sum(1 for a in assets if not a.license.allows_commercial)
    if non_commercial:
        console.print(
            f"  [yellow]{non_commercial} forbid commercial use[/yellow]; "
            "renders using them record the restriction in their credits."
        )

    if not asset_library.supports_search:
        console.print("  [yellow]Search unavailable[/yellow]: pip install sqlite-vec")

    # A mismatch makes search refuse, so it belongs in the summary rather than
    # waiting to surface as an error mid-render (D-039).
    models = asset_library.embedding_models()
    if len(models) > 1:
        console.print(
            f"  [red]Mixed embedding models[/red]: {', '.join(sorted(models))}. "
            "Run [bold]voxframe reembed[/bold]."
        )
    elif models:
        console.print(f"  Embeddings: {next(iter(models))}")


@app.command()
def reembed(
    library: Annotated[
        Path | None, typer.Option("--library", help="Library folder.")
    ] = None,
    embed_model: Annotated[
        str | None,
        typer.Option("--embed-model", help="'default' (best) or 'lite' (smaller)."),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Recompute embeddings that were made by a different model.

    Switching embedding models invalidates existing vectors: different models
    occupy unrelated coordinate systems, so comparing across them returns
    meaningless results. This recomputes them with the current model.
    """
    configure_logging("info" if verbose else "warning")

    settings = get_settings()
    asset_library = AssetLibrary(library or settings.library_path)
    embedder = Embedder(
        use_gpu=settings.use_gpu, model_key=embed_model or settings.resolved_embed_model
    )

    stale = asset_library.assets_needing_reembed(embedder.model_id)
    if not stale:
        console.print(f"[green]Up to date.[/green] All embeddings use {embedder.model_id}.")
        return

    present = asset_library.embedding_models()
    console.print(f"Re-embedding [bold]{len(stale)}[/bold] assets")
    console.print(f"  from: {', '.join(sorted(m for m in present if m)) or '(unrecorded)'}")
    console.print(f"  to:   {embedder.model_id}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(f"Re-embedding {len(stale)} assets...", total=None)
        try:
            result = reembed_library(asset_library, embedder)
        except EmbeddingError as exc:
            progress.stop()
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc

    console.print(f"[green]{result.summary()}[/green]")
    for path, error in result.failed[:5]:
        console.print(f"  [red]failed:[/red] {path.name}: {error[:60]}")


@app.command()
def web(
    port: Annotated[
        int | None,
        typer.Option("--port", help="Port to listen on. Picks a free one if taken."),
    ] = None,
    host: Annotated[
        str,
        typer.Option(
            "--host",
            help=(
                "Interface to bind. Loopback only unless --allow-remote is "
                "passed: the app has no multi-user authentication."
            ),
        ),
    ] = "127.0.0.1",
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open a browser at startup.")
    ] = True,
    allow_remote: Annotated[
        bool,
        typer.Option(
            "--allow-remote",
            help=(
                "Bind a non-loopback interface. Exposes an app with no "
                "authentication; only for use behind your own access control."
            ),
        ),
    ] = False,
) -> None:
    """Start the local web app.

    Binds 127.0.0.1 by default, validates the Host header against DNS
    rebinding, and requires a per-session token printed below. The token is
    generated at launch and never written to disk.
    """
    try:
        from voxframe.api.serve import build_server, serve
    except ImportError as exc:
        console.print(
            "[red]The web app needs the 'app' extra.[/red] "
            'From the source folder: [bold]pip install -e ".[app]"[/bold]'
        )
        raise typer.Exit(code=1) from exc

    from voxframe.api.security import AccessDenied

    # The server previously ran on structlog's defaults, which log to stdout
    # with a traceback renderer that can raise on a Windows console (D-124).
    configure_logging()

    try:
        handle = build_server(host=host, port=port, allow_remote=allow_remote)
    except AccessDenied as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Voxframe[/green] on http://{handle.host}:{handle.port}")
    console.print(f"  Jobs and uploads: {handle.store.root}")
    console.print()
    console.print("  Open this URL - it carries the session token:")
    console.print(f"  [bold]{handle.url}[/bold]")
    console.print()
    console.print(
        "  [dim]Ctrl-C to stop. An interrupted render resumes from cache.[/dim]"
    )

    if open_browser:
        import webbrowser

        webbrowser.open(handle.url)

    try:
        serve(handle)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


if __name__ == "__main__":
    app()
