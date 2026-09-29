"""Domain model for a library asset: an image or video clip.

Provenance is mandatory (D-012). An asset cannot exist without ``source``,
``author``, ``license`` and ``source_url``, so the credits file is a projection
of recorded facts rather than separate bookkeeping that can drift from what a
render actually used.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, model_validator

__all__ = ["Asset", "AssetKind", "LicenseInfo", "Orientation"]


class AssetKind(StrEnum):
    """What kind of media an asset holds."""

    IMAGE = "image"
    VIDEO = "video"


class Orientation(StrEnum):
    """Shape of an asset, for matching against the output aspect ratio."""

    LANDSCAPE = "landscape"
    PORTRAIT = "portrait"
    SQUARE = "square"

    @classmethod
    def from_size(cls, width: int, height: int) -> Orientation:
        """Classify by aspect ratio.

        A 10% tolerance counts as square: a 1080x1000 image behaves like a
        square for framing purposes, and treating it as landscape would pick
        the wrong crop.
        """
        if height == 0:
            return cls.LANDSCAPE
        ratio = width / height
        if 0.9 <= ratio <= 1.1:
            return cls.SQUARE
        return cls.LANDSCAPE if ratio > 1 else cls.PORTRAIT


class LicenseInfo(BaseModel):
    """How an asset may be used, and who must be credited.

    Separate from :class:`Asset` because credits, filtering and the
    commercial-use guarantee all need these fields together, and because an
    adapter fetching from an external source populates exactly this.
    """

    model_config = {"frozen": True}

    #: min_length=1 catches the empty string; the validator below catches
    #: whitespace-only values, which would otherwise pass every check and leave
    #: a blank line in the credits file (D-035).
    name: str = Field(
        min_length=1, description="License identifier, e.g. 'CC0-1.0', 'Pexels'."
    )
    author: str = Field(
        min_length=1, description="Who to credit. 'Unknown' if genuinely so."
    )
    source: str = Field(
        min_length=1, description="Where it came from: 'local', 'pexels', ..."
    )

    #: Deliberately allowed to be empty: a photograph a user took themselves
    #: has no canonical URL, and requiring a fabricated one would make
    #: provenance less trustworthy, not more.
    source_url: str = Field(default="", description="Canonical page for the asset.")

    requires_attribution: bool = Field(default=True)

    #: Whether the license permits commercial use. Assets failing this are
    #: still usable, but every render containing one records the restriction
    #: in its credits file so a user cannot unknowingly publish commercially.
    allows_commercial: bool = Field(default=True)

    @model_validator(mode="after")
    def _provenance_is_substantive(self) -> Self:
        """Reject whitespace-only provenance.

        ``min_length=1`` accepts ``"   "``, which satisfies the schema while
        producing a credits entry that names nobody. Anyone can write
        ``author="Unknown"`` if the author is genuinely unknown; that is an
        honest record. A blank is not.
        """
        for field_name in ("name", "author", "source"):
            value = getattr(self, field_name)
            if not value.strip():
                raise ValueError(
                    f"LicenseInfo.{field_name} cannot be empty or whitespace. "
                    f"Every asset must be attributable (D-012). Use 'Unknown' "
                    f"if the {field_name} is genuinely not known."
                )
        return self

    def attribution(self) -> str:
        """A one-line credit for the credits file."""
        parts = [self.author]
        if self.source and self.source != "local":
            parts.append(f"via {self.source}")
        parts.append(f"({self.name})")
        return " ".join(parts)


class Asset(BaseModel):
    """One image or video clip in the library.

    Attributes:
        id: Stable identifier, derived from the content hash.
        path: Location on disk, relative to the library root.
        kind: Image or video.
        sha256: Content hash. Identifies exact duplicates on re-ingest.
        width: Pixel width.
        height: Pixel height.
        duration: Length in seconds for video; ``None`` for stills.
        license: Provenance and usage terms. Mandatory.
        tags: Free-form labels, from filename, folder, or an adapter.
        dominant_colors: Up to five hex colours, for visual consistency
            between neighbouring scenes.
        phash: Perceptual hash, for near-duplicate detection.
        added_at: When this entered the library.
    """

    model_config = {"frozen": True}

    id: str
    path: Path
    kind: AssetKind
    sha256: str = Field(min_length=64, max_length=64)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    duration: float | None = Field(default=None, gt=0)

    license: LicenseInfo

    tags: tuple[str, ...] = Field(default=())
    dominant_colors: tuple[str, ...] = Field(default=())
    phash: str = Field(default="")
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _video_has_duration(self) -> Self:
        """A video without a duration cannot be scheduled into a scene."""
        if self.kind is AssetKind.VIDEO and self.duration is None:
            raise ValueError(f"video asset {self.id} has no duration")
        return self

    @property
    def orientation(self) -> Orientation:
        return Orientation.from_size(self.width, self.height)

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height

    @property
    def megapixels(self) -> float:
        return (self.width * self.height) / 1_000_000

    def fits(self, target_width: int, target_height: int) -> bool:
        """Whether this asset covers the target frame without upscaling.

        Upscaling a small image to fill a 1080p frame looks soft, which reads
        as low production value even when everything else is right.
        """
        return self.width >= target_width and self.height >= target_height
