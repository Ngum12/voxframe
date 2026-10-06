"""Inspectable finishing cues from the saved edit, without changing decisions.

Thresholds are editorial review guides, not guarantees of quality. This reads
metadata and timed words; it does not claim to inspect video pixels or meaning.
"""

from voxframe.config.short_export import safe_caption_style
from voxframe.config.style import CaptionPosition, get_template
from voxframe.plan.scene_plan import ScenePlan
from voxframe.plan.shorts import revision
from voxframe.render.captions.ass import _TextWidth, _usable_width
from voxframe.render.compose.captioned import _dimensions


def review(plan: ScenePlan, height: int = 1080) -> dict:
    if plan.short_export:
        height = plan.short_export.height
    # Match the export renderer's even dimensions.
    width, height = _dimensions(plan.aspect, height)
    issues = []

    def add(code, category, title, detail, action, scene=None):
        issues.append(
            {
                "id": f"{code}:{scene.index if scene else 'project'}",
                "category": category,
                "title": title,
                "detail": detail,
                "action": action,
                "scene": scene.index if scene else None,
                "at": scene.start_frame / plan.fps if scene else 0,
            }
        )

    if plan.short_export and not 3 <= plan.total_frames / plan.fps <= 60 + 1e-7:
        add(
            "export-duration",
            "timing",
            "Short export duration needs attention",
            "This preset needs a 3-60 second edit.",
            "shorts",
        )
    if plan.audio_mix.too_close and (plan.music_path or plan.score):
        add(
            "voice-margin",
            "sound",
            "Music may compete with the voice",
            f"Music protection is {plan.audio_mix.speech_margin_db:g} dB; "
            "12 dB or more is the existing comfortable-margin guide.",
            "sound",
        )
    missing_measure = False
    template = get_template(plan.style)
    for scene in plan.scenes:
        if scene.is_card:
            continue
        seconds = scene.duration_frames / plan.fps
        words = scene.caption_words()
        if scene.display_text.strip() and not scene.words:
            add(
                "untimed",
                "captions",
                "Caption highlights use estimated timing",
                "This scene has text but no original word timings. Listen and review alignment.",
                "captions",
                scene,
            )
        elif scene.is_corrected:
            add(
                "corrected",
                "captions",
                "Check the corrected caption alignment",
                "Corrections may use estimated timings for changed words. Compare with the voice.",
                "captions",
                scene,
            )
        if len(words) >= 8:
            span = max(1 / plan.fps, words[-1].end - words[0].start)
            pace = len(words) / span
            if pace > 4:
                add(
                    "pace",
                    "captions",
                    "Fast caption pace",
                    f"{pace:.1f} words per second across {len(words)} timed words. "
                    "Above 4 is a review cue; short caption pages may still work well.",
                    "captions",
                    scene,
                )
        treatment = scene.caption_treatment or plan.caption_treatment
        captions = treatment.apply(template.captions) if treatment else template.captions
        if plan.short_export:
            captions = safe_caption_style(
                captions, plan.short_export.safe_area, progress=plan.short_export.progress
            )
        measure = _TextWidth.for_frame(captions, height)
        if words and measure is None and not missing_measure:
            missing_measure = True
            add("font-measure", "captions", "Caption width check is unavailable",
                "The bundled caption font could not be measured. Restore it before "
                "relying on caption fit checks.", "captions")
        if measure:
            usable = _usable_width(captions, width, measure)
            wide = next((w.text for w in words if measure(w.text) > usable), None)
            if wide:
                add(
                    "wide-word",
                    "captions",
                    "A long word needs a picture check",
                    f"“{wide}” exceeds the caption box at this size. "
                    "Review the rendered preview or reduce the caption size.",
                    "captions",
                    scene,
                )
        if words and seconds < 0.25:
            add(
                "brief-shot",
                "timing",
                "A spoken shot lasts less than a quarter-second",
                f"This shot lasts {seconds:.2f} seconds. Check the jump cut and its caption.",
                "director",
                scene,
            )
        beat = scene.visual_beat
        if (
            beat
            and beat.text
            and (
                (beat.position == "center" and captions.position == CaptionPosition.CENTER)
                or (beat.position == "top" and captions.position == CaptionPosition.TOP)
            )
        ):
            add(
                "text-placement",
                "framing",
                "Two text layers share a placement",
                "The manually positioned text beat and captions use the same area. "
                "Try Auto text placement and inspect the preview.",
                "director",
                scene,
            )
        speaker = plan.shows_speaker(scene)
        media = plan.footage if speaker else scene.asset
        if media:
            zoom = beat.zoom if speaker and beat else 1
            scale = max(width / media.width, height / media.height) * zoom
            if scale > 1.5:
                add(
                    "resolution",
                    "framing",
                    "The source is enlarged substantially",
                    f"{media.width} x {media.height} source enlarged about {scale:.1f}x "
                    f"to cover {width} x {height}. Inspect sharpness or choose a smaller output.",
                    "director" if speaker else "scenes",
                    scene,
                )
        if speaker and plan.footage and plan.footage.duration > 0:
            source = scene.footage_start
            if (
                source is not None
                and source + plan.footage.audio_offset + seconds
                > plan.footage.duration + 1 / plan.fps
            ):
                add(
                    "source-tail",
                    "timing",
                    "The recording ends before this shot",
                    "The planned source interval extends beyond the recording duration. "
                    "The renderer may hold the last frame; inspect the ending.",
                    "shorts",
                    scene,
                )
        if scene.asset and not speaker and scene.asset.prints_text:
            add(
                "printed-image",
                "framing",
                "Picture text may compete with captions",
                "This selected image is marked as printed text. "
                "Review legibility or choose another image.",
                "scenes",
                scene,
            )
    categories = ("captions", "framing", "timing", "sound")
    return {
        "revision": revision(plan),
        "width": width,
        "height": height,
        "seconds": plan.total_frames / plan.fps,
        "issues": issues,
        "counts": {
            category: sum(i["category"] == category for i in issues) for category in categories
        },
        "note": "Review cues from saved timings and source metadata, "
        "not a visual or semantic inspection. "
        "A clean report still needs a watch and listen.",
    }
