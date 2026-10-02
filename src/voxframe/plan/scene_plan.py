"""The scene plan: an editable edit-decision list.

This is the product's centre of gravity. The renderer consumes **only** the
plan, so hand-editing the JSON, editing it in the web UI (Phase 8), and
regenerating it from scratch are the same operation as far as rendering is
concerned. Nothing upstream — transcript, library, matcher — is consulted
during a render.

That constraint is what makes the plan genuinely editable rather than a debug
dump. A user who swaps an image, retimes a scene or rewrites a query gets
exactly what they asked for, because there is no second source of truth to
disagree with them.

Timing is in **frames** (D-013). Seconds appear only as derived display fields,
marked as such, so an editor showing seconds cannot introduce rounding error
back into the timeline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self

import structlog
from pydantic import BaseModel, Field, model_validator

from voxframe.config.settings import AspectRatio
from voxframe.models.asset import Asset, AssetKind
from voxframe.models.transcript import Word
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.score_choice import ScoreChoice
from voxframe.render.version import RENDERER_VERSION

__all__ = [
    "PLAN_VERSION",
    "Footage",
    "MotionKind",
    "PlanAsset",
    "PlanError",
    "PlanWord",
    "PlannedScene",
    "QuerySource",
    "ScenePlan",
    "Shot",
]

#: Bumped when the plan format changes incompatibly, so an old plan is refused
#: with an explanation rather than misread.
PLAN_VERSION = 2

log = structlog.get_logger(__name__)


class PlanError(ValueError):
    """Raised when a plan cannot be loaded or is inconsistent."""


class QuerySource(StrEnum):
    """Where a scene's search queries came from (D-052).

    The distinction is load-bearing: a user who edits a query in the plan has
    made a decision, and regeneration must not silently overwrite it.
    """

    #: Extracted automatically from the scene text.
    AUTOMATIC = "automatic"

    #: Edited by a person. The edited queries drive the search, and
    #: regeneration preserves them.
    USER = "user"


class MotionKind(StrEnum):
    """How the camera moves over a still."""

    NONE = "none"
    KEN_BURNS = "ken_burns"
    PARALLAX = "parallax"


class Shot(StrEnum):
    """What a scene shows when the recording has a picture of its own."""

    #: The matched picture, or the background when there is none: what every
    #: scene showed before a recording's own picture could be used.
    PICTURE = "picture"

    #: The recording's own picture, the speaker, cut to the frame the scene's
    #: words were spoken over.
    SPEAKER = "speaker"


class Footage(BaseModel):
    """The recording's own picture, when it has one and the person chose it.

    Recorded in the plan so the renderer still reads nothing else (D-011).
    """

    model_config = {"frozen": True}

    path: str = Field(min_length=1)
    #: Displayed size, after any rotation the camera recorded.
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    duration: float = Field(ge=0)
    #: Seconds from the file's start to its first sound. A scene's
    #: ``footage_start`` is on the sound's clock; adding this finds the frame.
    audio_offset: float = Field(default=0.0, ge=0)
    #: Where the speaker is across the frame, 0 (left) to 1 (right). Used when
    #: the video is narrower than the footage, so the crop keeps them in.
    subject_x: float = Field(default=0.5, ge=0, le=1)
    #: Where it came from: ``motion``, ``centre`` or ``user``.
    subject_source: str = Field(default="centre")


class PlanAsset(BaseModel):
    """An asset as referenced by the plan.

    Denormalised deliberately: the plan records enough to render and to credit
    without consulting the library. A plan therefore stays meaningful if the
    library is rebuilt, and the credits file can be produced from the plan
    alone.
    """

    model_config = {"frozen": True}

    id: str
    path: str = Field(description="Path to the media file.")
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    #: Image or video. The renderer needs this to choose a path: a clip
    #: supplies its own motion and must not also get Ken Burns (D-085).
    kind: AssetKind = Field(default=AssetKind.IMAGE)

    #: Clip length in seconds; ``None`` for a still. Recorded so the renderer
    #: can tell a clip shorter than its scene without probing the file.
    duration: float | None = Field(default=None, gt=0)

    license_name: str = Field(min_length=1)
    license_author: str = Field(min_length=1)
    license_source: str = Field(min_length=1)
    license_url: str = Field(default="")

    #: How closely this asset matched the scene, when it was recorded as a
    #: candidate rather than chosen. Shown under "Details" so a person can see
    #: how near a near miss was (D-127). ``None`` for the chosen asset, whose
    #: scores live on the scene.
    similarity: float | None = Field(default=None)

    #: Whether the image's subject is printed text, which competes with the
    #: burned-in captions (D-082, D-133). Judged from the image itself, so the
    #: scene plan can say so before a person chooses it.
    prints_text: bool = Field(default=False)

    @classmethod
    def from_asset(cls, asset: Asset, similarity: float | None = None) -> PlanAsset:
        return cls(
            similarity=similarity,
            id=asset.id,
            path=str(asset.path),
            width=asset.width,
            height=asset.height,
            kind=asset.kind,
            duration=asset.duration,
            license_name=asset.license.name,
            license_author=asset.license.author,
            license_source=asset.license.source,
            license_url=asset.license.source_url,
        )

    @property
    def is_video(self) -> bool:
        return self.kind is AssetKind.VIDEO

    def attribution(self) -> str:
        """One-line credit, as it appears in the credits file."""
        parts = [self.license_author]
        if self.license_source and self.license_source != "local":
            parts.append(f"via {self.license_source}")
        parts.append(f"({self.license_name})")
        return " ".join(parts)


class PlanWord(BaseModel):
    """One displayed word with its timing, stored in the plan.

    The plan used to keep only scene text, and re-rendering redistributed words
    evenly across the scene. That was wrong: it discarded the transcript's real
    timings on every re-render, so a plan rendered twice gave worse highlighting
    the second time. Storing timings makes the plan genuinely the only input
    (D-011) without losing precision.

    Times are seconds, matching the transcript. They are converted to frames
    only by the caption writer, and the scene's frame bounds remain
    authoritative for the timeline (D-013).
    """

    model_config = {"frozen": True}

    text: str
    start: float = Field(ge=0, description="Start time in seconds.")
    end: float = Field(ge=0, description="End time in seconds.")

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Self:
        if self.end < self.start:
            raise PlanError(
                f"word {self.text!r} ends ({self.end}) before it starts "
                f"({self.start})"
            )
        return self

    @classmethod
    def from_word(cls, word: Word) -> PlanWord:
        return cls(text=word.text, start=word.start, end=word.end)

    def to_word(self) -> Word:
        return Word(text=self.text, start=self.start, end=self.end)


class PlannedScene(BaseModel):
    """One scene, fully specified for rendering.

    Attributes:
        index: Position in the sequence.
        start_frame: First frame, inclusive.
        end_frame: Last frame, exclusive.
        text: Spoken text as transcribed. Left alone by caption editing so
            the original remains visible for comparison.
        caption_text: What captions should display, when it differs from
            ``text``. Set this to correct a misheard word; word timings are
            re-derived from ``words`` on the next render, including when a
            correction splits one word into two or merges two into one.
        words: Displayed words with their timings. Written from the transcript
            and rewritten when ``caption_text`` is applied.
        queries: Visual queries for this scene. Editing these and setting
            ``query_source`` to ``user`` is the main way to steer image choice
            without an LLM.
        query_source: Whether the queries were extracted automatically or
            edited by a person. User-edited scenes search on their queries
            instead of the scene text, and are never regenerated over.
        asset: The chosen asset, or ``None`` for a plain background.
        alternatives: Runners-up, so a user can swap without re-searching.
        motion: Camera move for this scene.
        motion_reason: Why this motion was chosen, especially when parallax
            was rejected (D-007).
        match_score: Final ranked score, for inspection.
        semantic_score: Raw similarity before re-ranking.
    """

    model_config = {"frozen": True}

    index: int = Field(ge=0)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)

    text: str = Field(default="")

    #: Corrected caption text. Empty means "display ``text``". Kept separate
    #: rather than overwriting ``text`` so a user can see what was actually
    #: heard next to what they corrected it to, and so re-applying a correction
    #: is idempotent.
    caption_text: str = Field(default="")

    #: Displayed words with real timings, so a re-render highlights as
    #: precisely as the first render did.
    words: tuple[PlanWord, ...] = Field(default=())

    #: ``title`` or ``chapter`` when this scene is a card rather than a
    #: matched image. A card carries its own text and takes no asset, so the
    #: renderer draws it instead of searching for one (D-099).
    card_kind: str = Field(default="")

    #: What a card displays. Empty for an ordinary scene.
    card_text: str = Field(default="")

    queries: tuple[str, ...] = Field(default=())

    #: Set to ``user`` when a person has edited this scene's queries. An edited
    #: scene searches on its queries rather than its full text (D-051, D-052),
    #: and regeneration leaves it alone.
    query_source: QuerySource = Field(default=QuerySource.AUTOMATIC)

    asset: PlanAsset | None = Field(default=None)
    alternatives: tuple[PlanAsset, ...] = Field(default=())

    #: For a scene left as a plain background: the candidates that came
    #: closest, just under the threshold. Offered as "use this image anyway",
    #: never chosen automatically (D-127).
    near_misses: tuple[PlanAsset, ...] = Field(default=())

    #: ``user`` once a person has chosen, swapped or removed this scene's image.
    #: A user's choice is theirs: nothing automatic may replace it (D-128).
    asset_source: str = Field(default="automatic")

    motion: MotionKind = Field(default=MotionKind.KEN_BURNS)
    motion_reason: str = Field(default="")

    #: What this scene shows when the plan has footage. Ignored without it.
    shot: Shot = Field(default=Shot.PICTURE)
    #: ``user`` once a person has chosen the shot; nothing automatic changes it.
    shot_source: str = Field(default="automatic")
    shot_reason: str = Field(default="")
    #: Where this scene starts in the recording, in seconds on the sound's
    #: clock. Kept per scene rather than derived, so highlights and cuts that
    #: move scenes on the video's clock still show the frames that were
    #: spoken over. ``None`` for a card, or a plan without footage.
    footage_start: float | None = Field(default=None, ge=0)

    match_score: float = Field(default=0.0)
    semantic_score: float = Field(default=0.0)
    match_reason: str = Field(default="")

    @property
    def is_card(self) -> bool:
        """Whether this scene is a title or chapter card."""
        return bool(self.card_kind)

    @property
    def display_text(self) -> str:
        """The text captions should show.

        The correction when there is one, otherwise the transcript.
        """
        return self.caption_text.strip() or self.text

    @property
    def is_corrected(self) -> bool:
        """Whether a person has corrected this scene's caption text."""
        corrected = self.caption_text.strip()
        return bool(corrected) and corrected != self.text.strip()

    def caption_words(self) -> tuple[Word, ...]:
        """Displayed words with timings, applying any correction.

        This is what the caption writer consumes. The correction is applied
        here rather than being baked into ``words`` at edit time so that editing
        the JSON by hand is enough — a user does not have to recompute timings,
        which is the whole point of the field.

        Returns:
            Words in order. Empty when the scene has no text at all.
        """
        from voxframe.plan.caption_edit import apply_caption_correction

        stored = tuple(word.to_word() for word in self.words)

        if not self.is_corrected:
            return stored

        if not stored:
            # A hand-written scene with a correction but no timings. There is
            # nothing to preserve, so the caller falls back to even spacing.
            return ()

        return apply_caption_correction(stored, self.caption_text).words

    @model_validator(mode="after")
    def _frames_are_ordered(self) -> Self:
        if self.end_frame <= self.start_frame:
            raise PlanError(
                f"scene {self.index}: end_frame {self.end_frame} "
                f"is not after start_frame {self.start_frame}"
            )
        return self

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame

    def start_seconds(self, fps: float) -> float:
        """Derived for display only. Frames remain authoritative (D-013)."""
        return self.start_frame / fps

    def duration_seconds(self, fps: float) -> float:
        """Derived for display only."""
        return self.duration_frames / fps


class ScenePlan(BaseModel):
    """A complete, editable edit-decision list.

    The only input to rendering.
    """

    model_config = {"frozen": True}

    version: int = Field(default=PLAN_VERSION)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    #: The renderer that made this plan (D-147). Recorded so a later format
    #: change can be detected and warned about rather than misread. Zero for a
    #: plan written before it was recorded: those came from before the first
    #: release and are not supported -- their word timings may sit on the
    #: recording's clock rather than the video's (D-144).
    renderer_version: int = Field(default=RENDERER_VERSION, ge=0)

    audio_path: str
    audio_sha256: str = Field(min_length=64, max_length=64)
    audio_duration: float = Field(gt=0)
    language: str = Field(default="en")

    fps: float = Field(gt=0)
    total_frames: int = Field(gt=0)
    aspect: AspectRatio = Field(default=AspectRatio.HORIZONTAL)
    style: str = Field(default="clean-educational")

    scenes: tuple[PlannedScene, ...]

    #: Which models produced this plan. A plan made with different models is
    #: still renderable — the renderer only reads decisions — but recording
    #: them makes a surprising result diagnosable.
    #:
    #: The transcription model is chosen once, when the plan is created, and is
    #: **never** changed by a re-render (D-067). Rendering does not transcribe,
    #: so this is structural rather than a rule to remember; changing it
    #: requires ``voxframe retranscribe``, which says what it will discard.
    transcribe_model: str = Field(default="")
    embed_model: str = Field(default="")

    #: The music bed, when one was supplied. Recorded so credits describe what
    #: was actually rendered (D-012) and a re-render reproduces the same mix.
    #: Voxframe never sources music, so this is always the user's own file
    #: (D-091).
    music_path: str = Field(default="")
    music_credit: str = Field(default="")

    #: Music generated for this video instead (D-176): its style and seed.
    #: A video has a person's own track or a score, never both.
    score: ScoreChoice | None = Field(default=None)

    #: The recording's own picture (D-192). ``None`` shows pictures only.
    footage: Footage | None = Field(default=None)

    #: The person's sound settings (D-171). Changing them re-renders only the
    #: sound; the pictures come from the cache.
    audio_mix: AudioMix = Field(default_factory=AudioMix)

    #: Whether the language was detected or forced, and how confidently. Kept
    #: so a plan with a suspect transcript can be diagnosed later without
    #: re-running detection.
    language_probability: float = Field(default=1.0, ge=0, le=1)
    language_forced: bool = Field(default=False)

    @property
    def is_pre_release(self) -> bool:
        """Made before plans recorded their renderer: unsupported (D-147)."""
        return self.renderer_version == 0

    @model_validator(mode="after")
    def _one_kind_of_music(self) -> Self:
        if self.score is not None and self.music_path:
            raise PlanError("a plan has either a music track or a generated score, not both")
        return self

    @model_validator(mode="after")
    def _speaker_shots_have_footage(self) -> Self:
        for scene in self.scenes:
            if scene.shot is not Shot.SPEAKER:
                continue
            if self.footage is None:
                raise PlanError(
                    f"scene {scene.index} shows the speaker, but the plan has no footage"
                )
            if scene.is_card or scene.footage_start is None:
                raise PlanError(
                    f"scene {scene.index} shows the speaker but has no footage_start"
                )
        return self

    def shows_speaker(self, scene: PlannedScene) -> bool:
        """Whether ``scene`` renders from the footage."""
        return self.footage is not None and scene.shot is Shot.SPEAKER

    @model_validator(mode="after")
    def _scenes_tile_the_timeline(self) -> Self:
        """Scenes must cover the timeline with no gaps or overlaps.

        This is the frame-grid invariant (D-013) restated at the plan level. A
        hand-edited plan is the most likely place for it to be violated, and
        catching it here gives a clear message instead of a corrupt render.
        """
        if not self.scenes:
            raise PlanError("a plan must contain at least one scene")

        if self.scenes[0].start_frame != 0:
            raise PlanError(
                f"the first scene must start at frame 0, "
                f"got {self.scenes[0].start_frame}"
            )

        for previous, following in zip(self.scenes, self.scenes[1:], strict=False):
            if previous.end_frame != following.start_frame:
                raise PlanError(
                    f"gap or overlap between scenes {previous.index} and "
                    f"{following.index}: {previous.end_frame} != "
                    f"{following.start_frame}"
                )

        if self.scenes[-1].end_frame != self.total_frames:
            raise PlanError(
                f"the last scene must end at frame {self.total_frames} "
                f"(the audio's length), got {self.scenes[-1].end_frame}"
            )

        return self

    @property
    def user_edited_scenes(self) -> tuple[int, ...]:
        """Indices of scenes whose queries a person has edited.

        Regeneration must preserve these, so a caller rebuilding a plan needs
        to know which scenes to leave alone.
        """
        return tuple(
            scene.index
            for scene in self.scenes
            if scene.query_source is QuerySource.USER
        )

    @property
    def matched_scenes(self) -> int:
        return sum(1 for scene in self.scenes if scene.asset is not None)

    @property
    def speaker_scenes(self) -> int:
        """Scenes showing the recording's own picture (D-192)."""
        return sum(1 for scene in self.scenes if self.shows_speaker(scene))

    @property
    def unique_assets(self) -> int:
        return len({scene.asset.id for scene in self.scenes if scene.asset})

    def card_seconds_before(self, position: int) -> float:
        """Seconds of card before the scene at ``position`` in :attr:`scenes`.

        The plan's frames and word timings are on the video's clock, where
        every card adds time; the recording has none of it. Subtracting this
        converts a video time into a time in the recording (D-144).
        """
        frames = sum(
            scene.duration_frames for scene in self.scenes[:position] if scene.is_card
        )
        return frames / self.fps

    def card_pauses(self) -> tuple[tuple[float, float], ...]:
        """Where the narration pauses for cards: ``(recording time, seconds)``.

        Each card holds the picture while the speech waits, so the recording
        is paused at the point the card was inserted, for as long as the card
        lasts (D-144). Cards at the same point are merged.
        """
        pauses: list[tuple[float, float]] = []
        for position, scene in enumerate(self.scenes):
            if not scene.is_card:
                continue
            at = scene.start_frame / self.fps - self.card_seconds_before(position)
            seconds = scene.duration_frames / self.fps
            if pauses and abs(pauses[-1][0] - at) < 1e-6:
                pauses[-1] = (pauses[-1][0], pauses[-1][1] + seconds)
            else:
                pauses.append((round(at, 6), seconds))
        return tuple(pauses)

    def credits(self) -> tuple[str, ...]:
        """Attribution lines for every asset used, deduplicated and sorted.

        A projection of the plan, so credits always describe what was actually
        rendered (D-012).
        """
        lines = {
            scene.asset.attribution() for scene in self.scenes if scene.asset is not None
        }
        ordered = sorted(lines)

        if self.music_path:
            # Last, and phrased so an unattributed track is recorded honestly
            # rather than given an invented credit (D-091).
            name = Path(self.music_path).name
            ordered.append(
                f"Music: {self.music_credit.strip()}"
                if self.music_credit.strip()
                else f"Music: {name} (supplied by the user)"
            )
        elif self.score is not None:
            ordered.append(
                "Music: generated by Voxframe, with samples from Versilian Studios' "
                "VSCO 2 and VCSL libraries (CC0)"
            )

        return tuple(ordered)

    def to_json(self, *, indent: int = 2) -> str:
        """Serialise for editing.

        Indented by default: this file is meant to be read and edited by hand.
        """
        return self.model_dump_json(indent=indent)

    def save(self, path: Path) -> Path:
        """Write the plan to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8", newline="\n")
        return path

    @classmethod
    def load(cls, path: Path) -> ScenePlan:
        """Read a plan, with errors that point at the problem.

        Raises:
            PlanError: If the file is missing, malformed, or from an
                incompatible version.
        """
        if not path.is_file():
            raise PlanError(f"Scene plan not found: {path}")

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PlanError(
                f"{path} is not valid JSON: {exc.msg} at line {exc.lineno}, "
                f"column {exc.colno}"
            ) from exc

        version = raw.get("version")
        if version != PLAN_VERSION:
            raise PlanError(
                f"{path} uses plan version {version}, but this Voxframe "
                f"expects {PLAN_VERSION}. Regenerate the plan."
            )

        # A saved plan without a renderer version predates recording it: from
        # before the first release, and unsupported (D-147). Marked rather
        # than refused, so it can still be opened and the warning shown.
        if isinstance(raw, dict) and "renderer_version" not in raw:
            raw["renderer_version"] = 0
            log.warning("plan.pre_release", path=str(path))

        try:
            return cls.model_validate(raw)
        except PlanError:
            raise
        except ValueError as exc:
            raise PlanError(f"{path} is not a valid scene plan:\n{exc}") from exc
