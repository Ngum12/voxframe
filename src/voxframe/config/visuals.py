"""Editable text and speaker framing for a visual beat."""
from typing import Literal

from pydantic import BaseModel, Field


class VisualBeat(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

    text: str = Field(default="", max_length=96)
    kind: Literal["opening", "keypoint", "number", "closing"] = "keypoint"
    look: Literal["authority", "energy", "cinema"] = "authority"
    position: Literal["auto", "top", "center"] = "auto"
    zoom: float = Field(default=1, ge=1, le=1.25)
    source: Literal["director", "user"] = "user"


LOOKS = {
    "authority": {"label": "Clean authority", "seconds": 5, "zoom": 1.10, "caption": "classic"},
    "energy": {"label": "High energy", "seconds": 3, "zoom": 1.18, "caption": "electric"},
    "cinema": {"label": "Cinematic story", "seconds": 6, "zoom": 1.08, "caption": "cinema"},
}
