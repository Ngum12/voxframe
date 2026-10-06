"""How a video's sound is mixed: the person's settings, kept in the plan (D-171).

Voice level, music level, how far the music stays under the speech, and the
loudness the finished video is made to. Stored in the scene plan like every
other decision (D-011), so a re-render reproduces the mix and a change to it
re-renders only the sound.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from voxframe.music.story_arc import MusicArc

__all__ = [
    "LOUDNESS_TARGETS",
    "MIN_COMFORTABLE_MARGIN_DB",
    "SCORE_GROUPS",
    "AudioMix",
    "Destination",
    "LoudnessTarget",
    "ScoreLevels",
]


class Destination(StrEnum):
    """Where the video is going, which sets how loud it is made."""

    YOUTUBE = "youtube"
    SOCIAL = "social"
    WHATSAPP = "whatsapp"
    PODCAST = "podcast"


class LoudnessTarget(BaseModel):
    """Integrated loudness and the true-peak ceiling for one destination."""

    model_config = ConfigDict(frozen=True)

    label: str
    lufs: float
    true_peak: float


#: Approved by the owner (the plan, section 8). YouTube and the social
#: platforms normalise playback to about -14 LUFS, so a louder video is turned
#: down and a quieter one left quiet; phones and messaging re-encode heavily,
#: so they get more headroom; podcasts are mastered to -16.
LOUDNESS_TARGETS: dict[Destination, LoudnessTarget] = {
    Destination.YOUTUBE: LoudnessTarget(label="YouTube", lufs=-14.0, true_peak=-1.0),
    Destination.SOCIAL: LoudnessTarget(
        label="Instagram, TikTok, Facebook", lufs=-14.0, true_peak=-1.0
    ),
    Destination.WHATSAPP: LoudnessTarget(label="WhatsApp and phones", lufs=-15.0, true_peak=-1.5),
    Destination.PODCAST: LoudnessTarget(label="Podcast", lufs=-16.0, true_peak=-1.0),
}

#: Closer than this and the music starts to compete with the voice: the app
#: says so, and leaves the choice to the person (the plan, section 8).
MIN_COMFORTABLE_MARGIN_DB = 12.0


#: A generated score's instrument groups, as the editor shows them (D-179).
SCORE_GROUPS = ("piano", "strings", "percussion", "bass", "pads")


class ScoreLevels(BaseModel):
    """Each instrument group's level in a generated score, dB (0 as composed).

    Applied to the score's kept group stems, so a change re-mixes the sound
    without composing again. At -30 dB a group is as good as gone.
    """

    model_config = ConfigDict(frozen=True)

    piano: float = Field(default=0.0, ge=-30.0, le=6.0)
    strings: float = Field(default=0.0, ge=-30.0, le=6.0)
    percussion: float = Field(default=0.0, ge=-30.0, le=6.0)
    bass: float = Field(default=0.0, ge=-30.0, le=6.0)
    pads: float = Field(default=0.0, ge=-30.0, le=6.0)

    def as_db(self) -> tuple[tuple[str, float], ...]:
        return tuple((group, float(getattr(self, group))) for group in SCORE_GROUPS)

    @property
    def is_default(self) -> bool:
        return all(db == 0.0 for _, db in self.as_db())


class AudioMix(BaseModel):
    """The person's mix settings. The defaults are what every video had before."""

    model_config = ConfigDict(frozen=True)

    #: Voice level change, dB. Applied before the loudness target, so it sets
    #: the voice against the music, not how loud the video ends up.
    voice_db: float = Field(default=0.0, ge=-12.0, le=12.0)
    #: Music level change, dB, against the default bed level (D-100).
    music_db: float = Field(default=0.0, ge=-30.0, le=12.0)
    #: How far under the speech the music sits while someone is talking, dB
    #: (D-170). Larger is gentler on the voice.
    speech_margin_db: float = Field(default=15.0, ge=3.0, le=30.0)
    destination: Destination = Destination.YOUTUBE
    #: Voice polish (D-173): gentle noise reduction, tone and level. Off, the
    #: voice is exactly the recording, untouched.
    voice_polish: bool = True
    #: Story dynamics; steady preserves the original mix of older plans.
    music_arc: MusicArc = "steady"
    #: A generated score's group levels (D-179); ignored for other music.
    score_levels: ScoreLevels = Field(default_factory=ScoreLevels)

    @property
    def target(self) -> LoudnessTarget:
        return LOUDNESS_TARGETS[self.destination]

    @property
    def too_close(self) -> bool:
        """Whether the music will sit close enough to the voice to compete."""
        return self.speech_margin_db < MIN_COMFORTABLE_MARGIN_DB
