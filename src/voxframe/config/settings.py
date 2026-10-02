"""Configuration, loaded from environment, ``.env``, and CLI overrides.

Every setting has a working default. Voxframe must run correctly with no
configuration at all and no ``.env`` file present — optional API keys enable
extra sourcing adapters but are never required by the core pipeline.

Secrets are read from the environment only and are never written back to disk
or included in logs.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from voxframe.config.paths import (
    default_cache_path,
    default_library_path,
    default_output_path,
)

__all__ = [
    "AspectRatio",
    "IntermediateFormat",
    "MediaMix",
    "ModelProfile",
    "QualityPreset",
    "Settings",
    "get_settings",
]


class AspectRatio(StrEnum):
    """Output aspect ratios."""

    VERTICAL = "9:16"
    HORIZONTAL = "16:9"
    SQUARE = "1:1"

    @property
    def dimensions_1080(self) -> tuple[int, int]:
        """Pixel dimensions at 1080p-class resolution."""
        match self:
            case AspectRatio.VERTICAL:
                return (1080, 1920)
            case AspectRatio.HORIZONTAL:
                return (1920, 1080)
            case AspectRatio.SQUARE:
                return (1080, 1080)


class QualityPreset(StrEnum):
    """Render quality presets, trading speed against output quality."""

    DRAFT = "draft"
    STANDARD = "standard"
    HIGH = "high"
    ULTRA = "ultra"


class MediaMix(StrEnum):
    """Whether scenes are filled with stills, video clips, or both."""

    STILLS = "stills"
    CLIPS = "clips"
    MIXED = "mixed"


class ModelProfile(StrEnum):
    """Download size versus accuracy, across every model at once (D-067).

    One setting for users on limited bandwidth or disk, rather than asking them
    to discover and match a transcription model and an embedding model
    separately. ``lite`` is roughly 750 MB against roughly 3 GB.

    Deliberately **not** tied to :class:`QualityPreset`. Render quality is a
    per-render choice; which model transcribed the audio is a property of the
    plan. Tying them would mean a draft-then-final workflow re-transcribes on
    the final render, discarding any caption corrections the user made in
    between (D-067).
    """

    #: `large-v3-turbo` + the multilingual embedder. Best accuracy.
    STANDARD = "standard"

    #: `base` + the laion2b embedder. Roughly a quarter of the download, with
    #: materially weaker French retrieval (68% vs 80% top-1, D-044).
    LITE = "lite"

    @property
    def transcribe_model(self) -> str:
        return "base" if self is ModelProfile.LITE else "large-v3-turbo"

    @property
    def embed_model(self) -> str:
        return "lite" if self is ModelProfile.LITE else "default"

    @property
    def approximate_download_mb(self) -> int:
        """Total download for a first run, both models."""
        return 750 if self is ModelProfile.LITE else 3085

    @property
    def default_media_mix(self) -> MediaMix:
        """Whether this profile fetches video clips by default.

        ``lite`` is for users on limited bandwidth, and clips are 93% of a
        render's download for 30% of its assets (D-085). Defaulting them off
        is the single largest saving the profile can make — far larger than
        the model sizes it was created for (D-088).
        """
        return MediaMix.STILLS if self is ModelProfile.LITE else MediaMix.MIXED

    @property
    def default_max_clip_mb(self) -> int:
        """Per-clip size cap for this profile.

        Lower on ``lite`` so a user who opts clips back on still gets the
        small renditions rather than 30 MB of 1080p.
        """
        return 8 if self is ModelProfile.LITE else 25


class IntermediateFormat(StrEnum):
    """Intermediate segment format (D-016).

    ``X264_CRF16`` is the default: measured at roughly 40% of FFV1's disk cost
    with no visible generation loss through a single caption-burn pass.
    ``FFV1`` is available for a mathematically lossless path.
    """

    X264_CRF16 = "x264-crf16"
    FFV1 = "ffv1"


class Settings(BaseSettings):
    """Voxframe runtime settings.

    Values resolve in order: CLI arguments, environment variables, ``.env``,
    then these defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="VOXFRAME_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Paths ---
    # Local folders in a source checkout; the user's own folders when
    # installed (D-156).
    library_path: Path = Field(
        default_factory=default_library_path,
        description="Image and video library root.",
    )
    cache_path: Path = Field(
        default_factory=default_cache_path,
        description="Transcripts, embeddings, depth maps, and render segments.",
    )
    output_path: Path = Field(
        default_factory=default_output_path,
        description="Rendered output. Gitignored in a checkout (D-019).",
    )
    ffmpeg_path: str | None = Field(
        default=None,
        description="Explicit FFmpeg path. Falls back to PATH lookup.",
    )

    # --- Render defaults ---
    fps: float = Field(default=30.0, gt=0, le=120, description="Output frame rate.")
    aspect: AspectRatio = Field(default=AspectRatio.HORIZONTAL)
    quality: QualityPreset = Field(default=QualityPreset.STANDARD)
    intermediate: IntermediateFormat = Field(default=IntermediateFormat.X264_CRF16)

    # --- Pacing (pipeline step 2) ---
    scene_min_seconds: float = Field(default=4.0, gt=0)
    scene_max_seconds: float = Field(default=8.0, gt=0)

    # --- Motion (D-007) ---
    parallax_enabled: bool = Field(default=True)
    parallax_max_displacement: float = Field(
        default=0.04,
        gt=0,
        le=0.25,
        description=(
            "Max parallax offset as a fraction of image width. Caps the size of "
            "disocclusions so inpainting stays invisible."
        ),
    )
    depth_quality_threshold: float = Field(
        default=0.45,
        ge=0,
        le=1,
        description=(
            "Minimum depth separation score for parallax. Below this, the scene "
            "falls back to Ken Burns and records the reason in the plan."
        ),
    )

    # --- Models (D-066, D-067) ---
    profile: ModelProfile = Field(
        default=ModelProfile.STANDARD,
        description=(
            "Download size versus accuracy across every model at once: "
            "'standard' (~3 GB) or 'lite' (~750 MB). Individual model settings "
            "below override it."
        ),
    )
    transcribe_model: str | None = Field(
        default=None,
        description=(
            "Whisper size. Unset follows the profile. Recorded in the plan and "
            "never changed on re-render (D-067)."
        ),
    )
    embed_model: str | None = Field(
        default=None,
        description=(
            "Embedding model: 'default' (1.5 GB, best French) or 'lite' "
            "(605 MB, weaker French). Unset follows the profile. Switching "
            "requires 'voxframe reembed'."
        ),
    )

    # --- Captions ---
    caption_backing: str | None = Field(
        default=None,
        description=(
            "Override the style template's caption backing: box, band or "
            "outline. Unset follows the template (D-060)."
        ),
    )

    # --- Language (D-068) ---
    language: str | None = Field(
        default=None,
        description=(
            "Force a language code, e.g. 'en'. Unset detects. Set this when "
            "detection is unreliable on your audio, so it need not be passed "
            "every time."
        ),
    )
    languages: str | None = Field(
        # English and French by default (D-169): unrestricted detection heard
        # an accented English talk as Yoruba and garbled its captions (D-068),
        # and English and French are the languages Voxframe is tested in.
        default="en,fr",
        description=(
            "Restrict detection to a comma-separated candidate set; the most "
            "probable allowed language wins. 'en,fr' by default. 'any' lets "
            "the model choose from every language it knows."
        ),
    )

    # --- Music ---
    score_samples_path: Path | None = Field(
        default=None,
        description=(
            "Where the generated score's samples are (D-176). Unset, the "
            "downloaded sample pack in the data folder."
        ),
    )
    music_mode: Literal["directed", "simple"] = Field(
        default="directed",
        description=(
            "'directed' edits a music track to the speaker: cut on bars, landing "
            "on the last word, ducked by measurement, swelling into pauses "
            "(D-170). 'simple' loops it under the speech as before."
        ),
    )

    # --- Hardware ---
    use_gpu: bool = Field(
        default=True,
        description="Use CUDA when available. Never required; CPU is the tested default.",
    )
    threads: int = Field(default=0, ge=0, description="Worker threads. 0 = auto-detect.")

    # --- Sourcing (Phase 4) ---
    source_order: str = Field(
        default="local,pexels,pixabay,openverse",
        description=(
            "Comma-separated source priority. The local library is always "
            "searched; adapters without a key are skipped silently. Scenes "
            "matching nothing render as a gradient."
        ),
    )
    media_mix: MediaMix | None = Field(
        default=None,
        description=(
            "Fill scenes with stills, video clips, or both. Unset follows the "
            "profile: 'mixed' on standard, 'stills' on lite (D-088)."
        ),
    )
    clip_ratio: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description=(
            "Target fraction of scenes using video clips when media_mix is "
            "'mixed'. Clips are far larger than stills, so this trades "
            "bandwidth for motion."
        ),
    )
    max_clip_mb: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Refuse a single video clip larger than this, in megabytes. Unset "
            "follows the profile: 25 on standard, 8 on lite."
        ),
    )
    max_download_mb: int = Field(
        default=400,
        gt=0,
        description="Stop sourcing once a render has downloaded this much.",
    )
    similarity_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum CLIP similarity for a match. Unset follows the embedding "
            "model, because the two score on different scales (D-089). Below "
            "the threshold a scene renders as a gradient, which is better "
            "than a visibly unrelated image (D-077)."
        ),
    )

    # --- Optional sourcing API keys (all off unless set) ---
    pexels_api_key: str | None = Field(default=None, repr=False)
    pixabay_api_key: str | None = Field(default=None, repr=False)
    unsplash_access_key: str | None = Field(default=None, repr=False)
    anthropic_api_key: str | None = Field(default=None, repr=False)
    ollama_host: str | None = Field(default=None)

    @field_validator("scene_max_seconds")
    @classmethod
    def _max_exceeds_min(cls, v: float, info: object) -> float:
        """Reject inverted pacing bounds early, where the message is clear."""
        data = getattr(info, "data", {})
        minimum = data.get("scene_min_seconds")
        if minimum is not None and v <= minimum:
            raise ValueError(
                f"scene_max_seconds ({v}) must exceed scene_min_seconds ({minimum})"
            )
        return v

    @property
    def resolved_transcribe_model(self) -> str:
        """The Whisper size to use, explicit setting winning over the profile."""
        return self.transcribe_model or self.profile.transcribe_model

    @property
    def resolved_embed_model(self) -> str:
        """The embedding model to use, explicit setting winning over profile."""
        return self.embed_model or self.profile.embed_model

    @property
    def resolved_similarity_threshold(self) -> float:
        """The match threshold for the embedding model in use.

        The two embedders score on different scales: measured on identical
        scenes and images, ``lite`` runs about 0.06 higher than ``default``
        (median 0.259 against 0.197). One threshold across both would be
        strict for one and lenient for the other, which is how 0.22 came to
        reject every real photograph in D-081 (D-089).
        """
        if self.similarity_threshold is not None:
            return self.similarity_threshold
        return 0.23 if self.resolved_embed_model == "lite" else 0.17

    @property
    def resolved_media_mix(self) -> MediaMix:
        """Stills, clips or both, with an explicit setting winning."""
        return self.media_mix or self.profile.default_media_mix

    @property
    def resolved_max_clip_mb(self) -> int:
        """Per-clip cap, with an explicit setting winning."""
        return self.max_clip_mb or self.profile.default_max_clip_mb

    @property
    def source_priority(self) -> tuple[str, ...]:
        """Sources in the configured order, normalised."""
        return tuple(
            name.strip().lower()
            for name in self.source_order.split(",")
            if name.strip()
        )

    @property
    def allowed_languages(self) -> tuple[str, ...]:
        """The candidate language set, empty when detection is unrestricted."""
        if not self.languages or self.languages.strip().lower() in {"any", "all", "*"}:
            return ()
        return tuple(
            code.strip().lower() for code in self.languages.split(",") if code.strip()
        )

    @property
    def has_any_sourcing_key(self) -> bool:
        """Whether any external image source is configured."""
        return any(
            (self.pexels_api_key, self.pixabay_api_key, self.unsplash_access_key)
        )


def get_settings(**overrides: object) -> Settings:
    """Build settings, applying CLI overrides over environment and ``.env``.

    Args:
        **overrides: Explicit values, typically from CLI flags. ``None`` values
            are dropped so an unset flag does not override the environment.
    """
    cleaned = {k: v for k, v in overrides.items() if v is not None}
    return Settings(**cleaned)  # type: ignore[arg-type]
