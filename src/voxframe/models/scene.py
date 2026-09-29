"""Domain model for a scene: one visual held on screen for a span of speech.

A scene is the unit the renderer works in. It owns a span of the frame grid, the
words spoken during that span, and (from Phase 3) the asset shown and how the
camera moves over it.

Timing is stored in **frames**, not seconds. Seconds are derived for display and
for FFmpeg arguments. This is what keeps scene boundaries on the single global
grid and stops rounding error accumulating (DECISIONS.md D-013).
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, Field, model_validator

from voxframe.models.transcript import Word

__all__ = ["Scene"]


class Scene(BaseModel):
    """One visual unit of the video.

    Attributes:
        index: Position in the sequence, from zero.
        start_frame: First frame, inclusive, on the global grid.
        end_frame: Last frame, exclusive. Equals the next scene's start.
        words: Words spoken during this scene.
        emphasis: Indices into ``words`` marked for visual emphasis.
        pause_after: Silence in seconds following this scene, used to choose
            transitions that land in natural gaps rather than mid-word.
    """

    model_config = {"frozen": True}

    index: int = Field(ge=0)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    words: tuple[Word, ...] = Field(default=())
    emphasis: tuple[int, ...] = Field(default=())
    pause_after: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _validate_span_and_emphasis(self) -> Self:
        if self.end_frame <= self.start_frame:
            raise ValueError(
                f"scene {self.index} has end_frame {self.end_frame} "
                f"not after start_frame {self.start_frame}"
            )

        for position in self.emphasis:
            if not 0 <= position < len(self.words):
                raise ValueError(
                    f"scene {self.index} emphasis index {position} is outside "
                    f"its {len(self.words)} words"
                )
        return self

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame

    def start_seconds(self, fps: float) -> float:
        """Start time in seconds. For display and FFmpeg only."""
        return self.start_frame / fps

    def end_seconds(self, fps: float) -> float:
        """End time in seconds. For display and FFmpeg only."""
        return self.end_frame / fps

    def duration_seconds(self, fps: float) -> float:
        return self.duration_frames / fps

    @property
    def text(self) -> str:
        """The spoken text of this scene."""
        return " ".join(word.text.strip() for word in self.words if word.text.strip())

    @property
    def is_silent(self) -> bool:
        """Whether this scene contains no speech.

        Silent scenes are legitimate — an intro, an outro, a pause for effect —
        but they get no captions and may want different motion.
        """
        return not self.words
