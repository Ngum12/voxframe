"""Choose an asset for each scene.

Semantic similarity alone produces a watchable-but-wrong result: the same
strongest-matching image returns every time its topic recurs, portrait images
appear in landscape videos letterboxed, and consecutive scenes jump between
unrelated colour palettes. Matching therefore balances four signals:

1. **Semantic similarity** — does the image depict what is being said.
2. **Repetition** — an image used recently is penalised, so a five-minute video
   does not show the same photograph six times.
3. **Orientation** — an image matching the output shape crops better.
4. **Visual continuity** — neighbouring scenes that share a palette read as one
   piece rather than a slideshow.

Weights are deliberately tunable per style template: a documentary wants
continuity, bold social content wants variety.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import structlog

from voxframe.config.settings import AspectRatio
from voxframe.library.db import AssetLibrary
from voxframe.library.embeddings import Embedder
from voxframe.match.metaphors import metaphor_queries
from voxframe.match.queries import VisualQuery, extract_queries, searchable_text
from voxframe.match.text_detection import TextDetector
from voxframe.models.asset import Asset, Orientation
from voxframe.models.scene import Scene

__all__ = ["MatchError", "MatchWeights", "Matcher", "SceneMatch"]

log = structlog.get_logger(__name__)


class MatchError(RuntimeError):
    """Raised when matching cannot proceed."""


@dataclass(frozen=True, slots=True)
class MatchWeights:
    """How much each signal counts.

    Semantic similarity dominates by design: the other signals break ties and
    avoid obvious faults, but an image that does not depict the subject is
    wrong regardless of how well it crops.
    """

    semantic: float = 1.0
    orientation: float = 0.12
    continuity: float = 0.08

    #: Penalty applied to an asset used within the repetition window, scaled by
    #: how recently. Large enough to lose most ties, small enough that a
    #: genuinely better image still wins.
    repetition: float = 0.35

    #: Scenes to look back over when penalising repeats.
    repetition_window: int = 6

    #: Scenes within which an asset may not repeat **at all**, regardless of
    #: score (D-087).
    #:
    #: The graded penalty above is a tie-breaker: it loses most ties but a
    #: strong enough candidate still wins. In a small pool that is not enough —
    #: the same clip was chosen twice in a 3-asset library, the second time at
    #: 0.085, because nothing else cleared the threshold. A viewer reads a
    #: repeat as a bug, so this is a rule rather than a preference.
    no_repeat_window: int = 8

    #: Videos with at most this many scenes forbid *any* repeat, not merely one
    #: inside the window. A short video has nowhere to hide a repeated image:
    #: the viewer sees both instances within a minute.
    no_repeat_below_scenes: int = 12

    #: The most times one asset may appear in a video, however long (D-140).
    #: A hard cap, not a penalty: a third appearance is never chosen.
    max_uses: int = 2

    #: How many scenes apart two uses of one asset must be, at least: scene 4
    #: and scene 7 may share an image, scene 4 and scene 6 may not. An image
    #: that returns one or two scenes later reads as a mistake (D-140). Counted
    #: in scenes, plain ones included, whatever ``no_repeat_window`` is set to.
    min_repeat_gap: int = 3

    #: How far below the threshold a candidate may fall and still be offered to
    #: the user as a near miss (D-127).
    #:
    #: From the D-081 calibration: apt matches went as low as 0.126 while the
    #: threshold is 0.17, and below the threshold apt and irrelevant images
    #: overlap. That band is exactly where the matcher cannot tell and a person
    #: glancing at a thumbnail can -- so it is shown rather than discarded,
    #: though never chosen automatically. 0.05 reaches down to 0.12, covering
    #: the lowest apt match measured.
    near_miss_margin: float = 0.05

    #: How many near misses to record per unmatched scene.
    near_misses: int = 3

    #: Bonus for an asset large enough to stay sharp through the Ken Burns
    #: zoom. Small, because a sharper image that depicts the wrong thing is
    #: still the wrong image — but real, because Pixabay's standard accounts
    #: cap downloads at 1280px while Pexels serves originals, and at 1080p
    #: that difference is visible (D-080).
    resolution: float = 0.10

    #: Penalty for an image whose subject is printed text (D-082).
    #:
    #: Captions are burned over the image, so a photograph of a page produces
    #: two competing blocks of words and the caption becomes unreadable. Seen
    #: in a real render: the LibriVox credit line matched a page of scripture,
    #: and the caption was drawn on top of it.
    #:
    #: Large enough to lose to almost anything else, and — combined with the
    #: rule that a candidate scoring at or below zero is refused — large enough
    #: that a text image which is the *only* candidate loses to a gradient.
    #:
    #: Raised from 0.30 after a second render: "ACHIEVE" carved in stone and
    #: "EARTH" in Scrabble tiles were both detected and penalised, but survived
    #: at 0.133 and 0.157 because nothing else cleared the threshold for those
    #: scenes. 0.30 only re-ranked them; it did not reject them.
    #:
    #: Not an outright ban: a single sign in a street scene is incidental, and
    #: a genuinely strong match can still outscore the penalty.
    text_subject: float = 0.55

    #: Below this similarity, no asset is considered a match at all.
    #:
    #: **0.17, measured on real stock photographs** (D-082). The previous 0.22
    #: came from the 400-image eval set, which D-043 records as *generated*
    #: images — and generated images, synthesised from text-like concepts,
    #: score higher against text than photographs do. Against real Pexels and
    #: Pixabay results, 0.22 rejected **10 of 10** matches that were apt by
    #: inspection.
    #:
    #: The distributions overlap, so no threshold is clean. Measured: apt
    #: matches span 0.126-0.208 (n=10, median 0.187), irrelevant ones
    #: 0.135-0.162 (n=7, median 0.151). Sweeping the range:
    #:
    #: ==========  ==========  ====================
    #: threshold   apt kept    irrelevant admitted
    #: ==========  ==========  ====================
    #: 0.14        90%         57%
    #: 0.16        70%         14%
    #: **0.17**    **70%**     **0%**
    #: 0.19        40%         0%
    #: 0.22        0%          0%
    #: ==========  ==========  ====================
    #:
    #: 0.17 is where false positives reach zero without giving up recall: below
    #: it they jump to 57%, above it recall falls for nothing. The 30% of
    #: scenes it still rejects render as gradients, which is the intended
    #: behaviour (D-077) rather than a shortfall.
    #:
    #: Raise it for a large, well-matched library; lower it for a small one
    #: where some coverage beats none.
    minimum_similarity: float = 0.17


@dataclass(slots=True)
class SceneMatch:
    """The asset chosen for a scene, and why.

    Stored in the scene plan so a user can see the reasoning and override it.
    """

    scene_index: int
    asset: Asset | None
    score: float
    semantic_score: float
    queries: tuple[VisualQuery, ...]
    alternatives: tuple[tuple[Asset, float], ...] = field(default=())
    reason: str = ""

    #: For a scene left as a plain background: the candidates that came
    #: closest, with their similarity. Offered to the user as "use this image
    #: anyway"; never chosen automatically (D-127).
    near_misses: tuple[tuple[Asset, float], ...] = field(default=())

    @property
    def matched(self) -> bool:
        return self.asset is not None


#: Words that, in a source's own description, usually mean printed text is the
#: subject rather than incidental. Screening on the description is free; OCR
#: would be a heavy dependency for a question this cheap to approximate.
_TEXT_SUBJECT_WORDS = frozenset(
    {
        # Scripture and quotations, the case this was found on.
        "bible", "biblical", "scripture", "scriptures", "verse", "psalm",
        "quote", "quotation", "proverb",
        # Pages and print.
        "text", "page", "pages", "book", "books", "print", "printed",
        "newspaper", "magazine", "dictionary", "notebook", "document",
        "manuscript", "paragraph", "chapter",
        # Lettering as a subject in its own right.
        "typography", "handwriting", "calligraphy", "lettering", "font",
        "letters", "words", "alphabet", "scrabble",
        # Signage.
        "poster", "banner", "signage", "placard", "billboard",
        # Where the word itself is the subject: Scrabble tiles spelling
        # "EARTH", letters carved to read "ACHIEVE". Both reached a real
        # render before this was added.
        "spelling", "spelled", "spells", "tiles", "inscription", "engraved",
    }
)


def _is_text_subject(asset: Asset) -> bool:
    """Whether an asset's own description says it depicts printed text."""
    return bool(_TEXT_SUBJECT_WORDS & {tag.lower() for tag in asset.tags})


def _resolution_bonus(asset: Asset, render_height: int) -> float:
    """How well an asset's pixels survive being zoomed into.

    Ken Burns ends the move cropped into part of the frame, so an image only
    just wide enough at the start is soft by the end. The bonus is full for an
    asset with the zoom headroom, tapering to zero for one at exactly the
    output size.

    Returns:
        0.0 to 1.0. Zero when the asset's size is unknown, which is neutral
        rather than penalising.
    """
    if not asset.width or not asset.height:
        return 0.0

    # Width needed to stay sharp at the tightest point of the default move.
    required = render_height * 16 / 9 * 1.25
    if required <= 0:
        return 0.0

    return min(1.0, asset.width / required)


def _orientation_bonus(asset: Asset, aspect: AspectRatio) -> float:
    """How well an asset's shape suits the output.

    A mismatched image is not unusable — Ken Burns can crop into it — but it
    loses framing freedom, so it is a tie-breaker rather than a filter.
    """
    target = {
        AspectRatio.HORIZONTAL: Orientation.LANDSCAPE,
        AspectRatio.VERTICAL: Orientation.PORTRAIT,
        AspectRatio.SQUARE: Orientation.SQUARE,
    }[aspect]

    if asset.orientation is target:
        return 1.0
    if asset.orientation is Orientation.SQUARE or target is Orientation.SQUARE:
        return 0.5  # square crops acceptably either way
    return 0.0  # landscape into portrait, or the reverse


def _color_distance(first: tuple[str, ...], second: tuple[str, ...]) -> float:
    """Rough palette distance between two assets, in ``[0, 1]``.

    Compares only the dominant colour of each. Cheap and good enough for a
    tie-breaker; a full palette comparison would cost more than the signal is
    worth at this weight.
    """
    if not first or not second:
        return 0.5

    def to_rgb(hex_color: str) -> tuple[int, int, int]:
        value = hex_color.lstrip("#")
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))

    try:
        a = to_rgb(first[0])
        b = to_rgb(second[0])
    except (ValueError, IndexError):
        return 0.5

    # Normalised by the maximum possible RGB distance.
    squared = sum((x - y) ** 2 for x, y in zip(a, b, strict=True))
    distance: float = min(1.0, (squared**0.5) / 441.67)
    return distance


def _combine(vectors: list[list[float]], weights: list[float]) -> list[float]:
    """Weighted mean of several vectors, re-normalised.

    Used when a user supplies several queries for one scene: each contributes
    in proportion to its weight, and the result is normalised so similarity
    stays comparable with single-vector searches.
    """
    if not vectors:
        return []

    total = sum(weights) or 1.0
    combined = [0.0] * len(vectors[0])

    for vector, weight in zip(vectors, weights, strict=True):
        share = weight / total
        for index, value in enumerate(vector):
            combined[index] += value * share

    magnitude = sum(value**2 for value in combined) ** 0.5
    return [value / magnitude for value in combined] if magnitude > 0 else combined


class Matcher:
    """Selects assets for scenes.

    Args:
        library: The asset library to search.
        embedder: Produces query embeddings.
        weights: Signal weights. Defaults suit general use.
        render_height: Output height in pixels, for the resolution preference.
            An image only just wide enough at the start of a Ken Burns move is
            soft by the end of it (D-080).
    """

    def __init__(
        self,
        library: AssetLibrary,
        embedder: Embedder,
        weights: MatchWeights | None = None,
        *,
        render_height: int = 1080,
    ) -> None:
        self.library = library
        self.embedder = embedder
        self.weights = weights or MatchWeights()
        self.render_height = render_height
        # Whether each asset's subject is printed text, judged from its stored
        # vector (D-133). Cached: a candidate recurs across scenes.
        self._text_verdicts: dict[str, bool] = {}
        self._text_detector: TextDetector | None = None
        # Scene indices each asset has been placed at in the current
        # match_scenes call, for the hard cap and the gap (D-140).
        self._placed: dict[str, list[int]] = {}

    def match_scenes(
        self,
        scenes: tuple[Scene, ...],
        language: str = "en",
        *,
        aspect: AspectRatio = AspectRatio.HORIZONTAL,
        commercial_only: bool = False,
        alternatives: int = 3,
        user_queries: dict[int, tuple[str, ...]] | None = None,
    ) -> tuple[SceneMatch, ...]:
        """Choose an asset for every scene.

        Args:
            scenes: Scenes to match, in order. Order matters: repetition and
                continuity both depend on what came before.
            language: Language of the scene text, for query extraction.
            aspect: Output aspect ratio, for the orientation signal.
            commercial_only: Exclude assets whose licenses forbid commercial
                use.
            alternatives: How many runners-up to record per scene, so a user
                editing the plan can swap without re-running the search.
            user_queries: Scene index to edited queries. A scene listed here
                searches on those queries instead of its text, because a user
                who edited them has made a decision the matcher must respect
                (D-052).

        Returns:
            One match per scene, in the same order. A match may be unmatched
            when nothing clears the similarity threshold; that is legitimate
            and the renderer falls back to a plain background.
        """
        if not self.library.supports_search:
            raise MatchError(
                "Semantic search needs the sqlite-vec extension.\n"
                "  pip install sqlite-vec"
            )

        # Fail before embedding anything: a mismatch cannot be worked around
        # mid-run, and the error names the remedy (D-039).
        self.library.check_embedding_model(self.embedder.model_id)

        if self.library.count() == 0:
            raise MatchError(
                "The library is empty. Ingest images first:\n"
                "  voxframe ingest ./images --author 'Name' --license 'CC0-1.0'"
            )

        matches: list[SceneMatch] = []
        recent: list[str] = []
        # Every asset used so far, in order. `recent` is truncated to the
        # penalty window; this is not, because the short-video rule forbids a
        # repeat anywhere in the video (D-087).
        used: list[str] = []
        previous_asset: Asset | None = None
        self._placed = {}

        for scene in scenes:
            match = self._match_one(
                scene,
                language,
                aspect=aspect,
                recent=recent,
                used=used,
                total_scenes=len(scenes),
                previous=previous_asset,
                commercial_only=commercial_only,
                alternatives=alternatives,
                override=(user_queries or {}).get(scene.index),
            )
            matches.append(match)

            if match.asset is not None:
                recent.append(match.asset.id)
                recent = recent[-self.weights.repetition_window :]
                used.append(match.asset.id)
                self._placed.setdefault(match.asset.id, []).append(scene.index)
                previous_asset = match.asset

        matched = sum(1 for m in matches if m.matched)
        log.info(
            "match.done",
            scenes=len(scenes),
            matched=matched,
            unmatched=len(scenes) - matched,
            unique_assets=len({m.asset.id for m in matches if m.asset}),
        )

        return tuple(matches)

    def _near_misses(
        self,
        candidates: Sequence[tuple[Asset, float]],
        barred: set[str],
    ) -> tuple[tuple[Asset, float], ...]:
        """The closest candidates that fell just short of the threshold.

        Barred assets are left out: offering an image the viewer saw a moment
        ago is offering a repeat, which D-087 treats as a fault.
        """
        floor = self.weights.minimum_similarity - self.weights.near_miss_margin
        close = [
            (asset, round(similarity, 4))
            for asset, similarity in candidates
            if floor <= similarity < self.weights.minimum_similarity
            and asset.id not in barred
        ]
        close.sort(key=lambda item: -item[1])
        return tuple(close[: self.weights.near_misses])

    def _shows_text(self, asset_id: str) -> bool:
        """Whether an asset's subject is printed text, from its stored vector.

        The tag-based check alone caught nothing in a real sourced library,
        whose tags are just the source's name (D-133). This asks the image.
        """
        if asset_id in self._text_verdicts:
            return self._text_verdicts[asset_id]

        if self._text_detector is None:
            self._text_detector = TextDetector(
                self.embedder, getattr(self.embedder, "model_key", "")
            )
        verdict = False
        if self._text_detector.available:
            vector = self.library.vectors([asset_id]).get(asset_id)
            verdict = vector is not None and self._text_detector.shows_text(vector)
        self._text_verdicts[asset_id] = verdict
        return verdict

    def _barred_assets(
        self,
        recent: list[str],
        used: list[str],
        total_scenes: int,
        scene_index: int | None = None,
    ) -> set[str]:
        """Assets this scene may not use, whatever they score.

        Two rules, both from D-087:

        **In a short video, nothing repeats at all.** With a dozen scenes or
        fewer the viewer sees both instances within a minute, and a repeat
        reads as a bug rather than a motif.

        **Otherwise, nothing repeats inside the window.** Far enough apart, a
        reused image is not noticeable and forbidding it outright would empty
        scenes in a small library for no gain.

        And whatever the length, **no asset appears more than** ``max_uses``
        **times, nor within** ``min_repeat_gap`` **scenes of itself** (D-140).

        Returns:
            Asset ids barred for this scene. The rule is never waived
            automatically (D-140): a scene whose only match is barred is left
            for sourcing, a metaphor, an atmospheric image or a plain
            background, and the repeat is offered to the person instead.
        """
        if total_scenes <= self.weights.no_repeat_below_scenes:
            return set(used)

        return (
            set(used[-self.weights.no_repeat_window :])
            | self._hard_barred(used, scene_index)
        )

    def _hard_barred(self, used: list[str], scene_index: int | None) -> set[str]:
        """Assets no scene here may show, automatically or by one click (D-140).

        Used ``max_uses`` times already, or placed fewer than
        ``min_repeat_gap`` scenes before this one: scene 4 and scene 7 may
        share an image, scene 4 and scene 6 may not.
        """
        capped = {
            asset for asset in set(used) if used.count(asset) >= self.weights.max_uses
        }
        if scene_index is None:
            return capped
        near = {
            asset
            for asset, positions in self._placed.items()
            if any(0 < scene_index - p < self.weights.min_repeat_gap for p in positions)
        }
        return capped | near

    def _repeat_offers(
        self,
        barred_candidates: Sequence[tuple[Asset, float]],
        used: list[str],
        scene_index: int,
    ) -> list[tuple[Asset, float]]:
        """Barred candidates a person may still choose with one click.

        Only those that would break neither hard rule if chosen (D-140). The
        filmstrip notes where each is already shown.
        """
        hard = self._hard_barred(used, scene_index)
        return [
            (asset, round(similarity, 4))
            for asset, similarity in barred_candidates
            if asset.id not in hard
        ]

    def _match_one(
        self,
        scene: Scene,
        language: str,
        *,
        aspect: AspectRatio,
        recent: list[str],
        used: list[str],
        total_scenes: int,
        previous: Asset | None,
        commercial_only: bool,
        alternatives: int,
        override: tuple[str, ...] | None = None,
    ) -> SceneMatch:
        """Choose one scene's asset: literally first, then by metaphor.

        The scene's own words always come first; a metaphor is reached for only
        when they find nothing and the scene is abstract (D-136). That order is
        what keeps an apt literal image -- a praying silhouette for "when God
        calls a man" -- from being displaced by a generic summit.
        """
        literal = self._match_literal(
            scene, language, aspect=aspect, recent=recent, used=used,
            total_scenes=total_scenes, previous=previous,
            commercial_only=commercial_only, alternatives=alternatives,
            override=override,
        )
        if literal.asset is not None or override:
            # A person's own query says what the scene is about (D-052).
            return literal

        metaphor = self._match_metaphor(
            scene, language, aspect=aspect, recent=recent, used=used,
            total_scenes=total_scenes, previous=previous,
            commercial_only=commercial_only, alternatives=alternatives,
        )
        return metaphor if metaphor is not None else literal

    def _match_metaphor(
        self,
        scene: Scene,
        language: str,
        *,
        aspect: AspectRatio,
        recent: list[str],
        used: list[str],
        total_scenes: int,
        previous: Asset | None,
        commercial_only: bool,
        alternatives: int,
    ) -> SceneMatch | None:
        """Match an abstract scene through its visual metaphors, if any work.

        Each metaphor query is ranked on its own and the best kept: averaging
        "summit", "lighthouse" and "finish line" into one vector would describe
        none of them. The usual threshold, penalties and repetition rules all
        apply -- a metaphor is an easier question, not a lower bar.

        Returns:
            The match, recorded as a metaphor, or ``None`` if none cleared.
        """
        own = [query.text for query in extract_queries(scene.text, language)]
        metaphors = metaphor_queries(scene.text, own, language)
        if not metaphors:
            return None

        best: SceneMatch | None = None
        for text in metaphors:
            query = VisualQuery(text=text, weight=1.0)
            candidate = self._rank(
                scene,
                self.embedder.embed_texts([text])[0],
                (query,),
                aspect=aspect,
                recent=recent,
                used=used,
                total_scenes=total_scenes,
                previous=previous,
                commercial_only=commercial_only,
                alternatives=alternatives,
                reason_prefix="visual metaphor",
            )
            if candidate.asset is not None and (best is None or candidate.score > best.score):
                best = candidate

        if best is not None:
            log.info("match.metaphor", scene=scene.index, reason=best.reason)
        return best

    def _match_literal(
        self,
        scene: Scene,
        language: str,
        *,
        aspect: AspectRatio,
        recent: list[str],
        used: list[str],
        total_scenes: int,
        previous: Asset | None,
        commercial_only: bool,
        alternatives: int,
        override: tuple[str, ...] | None = None,
    ) -> SceneMatch:
        """Choose one scene's asset from its own words."""
        # A user edit wins outright (D-052). Someone who rewrote a query has
        # decided what the scene is about, and the full-sentence default must
        # not quietly override that.
        if override:
            queries = tuple(
                VisualQuery(text=text, weight=1.0) for text in override if text.strip()
            )
            if queries:
                combined = _combine(
                    self.embedder.embed_texts([q.text for q in queries]),
                    [q.weight for q in queries],
                )
                return self._rank(
                    scene, combined, queries, aspect=aspect, recent=recent, used=used,
            total_scenes=total_scenes,
                    previous=previous, commercial_only=commercial_only,
                    alternatives=alternatives, reason_prefix="user query",
                )

        # Otherwise search on the whole sentence (D-051). Extracted queries are
        # still recorded in the plan so a user can see and edit them, but they
        # no longer determine the vector: measured 79% top-1 (n=100) against
        # extraction's 71% on direct phrasing.
        queries = extract_queries(scene.text, language)

        # Web addresses and credited names are left out of the sentence too,
        # not only the extracted queries: the sentence is embedded (D-139, D-141).
        spoken = searchable_text(scene.text, marker="")
        if spoken.strip() and not queries:
            # Nothing worth searching for: only function words, or only what a
            # credit or an address left behind. Embedding "of ." still returns
            # a nearest image, and one cleared the threshold at 0.176 for a
            # credit line with nothing left in it (D-145). The scene falls
            # through to an atmospheric image or a plain background instead.
            return SceneMatch(
                scene_index=scene.index,
                asset=None,
                score=0.0,
                semantic_score=0.0,
                queries=(),
                reason="nothing in this scene can be searched for",
            )
        if not spoken.strip():
            return SceneMatch(
                scene_index=scene.index,
                asset=None,
                score=0.0,
                semantic_score=0.0,
                queries=(),
                reason="scene has no spoken text",
            )

        # Long scenes are embedded as overlapping chunks rather than
        # truncated (D-053). Each is searched and the best hit kept: averaging
        # would blur a scene that changes subject partway through, which is
        # exactly what a long scene tends to do.
        chunks = self.embedder.embed_text_chunks(spoken)

        if len(chunks) == 1:
            combined = chunks[0]
        else:
            best_match: SceneMatch | None = None
            for vector in chunks:
                candidate = self._rank(
                    scene, vector, queries, aspect=aspect, recent=recent, used=used,
                    total_scenes=total_scenes,
                    previous=previous, commercial_only=commercial_only,
                    alternatives=alternatives, reason_prefix="matched",
                )
                if best_match is None or candidate.score > best_match.score:
                    best_match = candidate
            assert best_match is not None
            return best_match

        return self._rank(
            scene, combined, queries, aspect=aspect, recent=recent, used=used,
            total_scenes=total_scenes,
            previous=previous, commercial_only=commercial_only,
            alternatives=alternatives, reason_prefix="matched",
        )

    def _rank(
        self,
        scene: Scene,
        vector: list[float],
        queries: tuple[VisualQuery, ...],
        *,
        aspect: AspectRatio,
        recent: list[str],
        used: list[str],
        total_scenes: int,
        previous: Asset | None,
        commercial_only: bool,
        alternatives: int,
        reason_prefix: str,
    ) -> SceneMatch:
        """Search with a vector and re-rank the candidates.

        Shared by the user-query and full-sentence paths so both apply the same
        repetition, orientation and continuity rules.
        """
        # Over-fetch: the re-ranking below can reorder substantially, so the
        # semantic top result is not necessarily the final choice.
        candidates = self.library.search(
            vector,
            limit=max(10, alternatives * 3),
            commercial_only=commercial_only,
            embed_model=self.embedder.model_id,
        )

        if not candidates:
            return SceneMatch(
                scene_index=scene.index,
                asset=None,
                score=0.0,
                semantic_score=0.0,
                queries=queries,
                reason="no assets returned by search",
            )

        # Assets barred outright for this scene: repeating one is read as a
        # fault, not as a motif (D-087). Applied before scoring so a barred
        # asset cannot win on score alone.
        barred = self._barred_assets(recent, used, total_scenes, scene.index)

        scored: list[tuple[Asset, float, float]] = []
        excluded_by_rule: list[tuple[Asset, float]] = []

        for asset, similarity in candidates:
            if similarity < self.weights.minimum_similarity:
                continue

            if asset.id in barred:
                # Kept aside rather than discarded: if nothing else clears the
                # threshold it is offered to the person, never chosen (D-140).
                excluded_by_rule.append((asset, similarity))
                continue

            score = similarity * self.weights.semantic
            score += _orientation_bonus(asset, aspect) * self.weights.orientation
            score += (
                _resolution_bonus(asset, self.render_height)
                * self.weights.resolution
            )

            if _is_text_subject(asset) or self._shows_text(asset.id):
                # A penalty, not a rejection (owner's decision, D-134): a clean
                # candidate wins, and if a text image is all there is it drops
                # below zero, so the scene falls back to plain with the image
                # still offered as a close match for a person to choose.
                score -= self.weights.text_subject

            if previous is not None:
                closeness = 1.0 - _color_distance(
                    asset.dominant_colors, previous.dominant_colors
                )
                score += closeness * self.weights.continuity

            if asset.id in recent:
                # Penalty decays with distance: reusing the immediately
                # previous image is far worse than reusing one six scenes back.
                position = recent.index(asset.id)
                recency = (len(recent) - position) / len(recent)
                score -= self.weights.repetition * recency

            scored.append((asset, score, similarity))

        if not scored and excluded_by_rule:
            # A forced repeat is the last resort (owner's decision, D-140).
            # The scene is left unmatched so the steps before it get their
            # turn -- more candidates from a search, another metaphor, an
            # atmospheric image, a plain background -- and the repeat is
            # offered to the person as a close match rather than taken.
            offers = self._repeat_offers(excluded_by_rule, used, scene.index)
            below = [
                offer
                for offer in self._near_misses(candidates, barred)
                if offer[0].id not in {asset.id for asset, _ in offers}
            ]
            best_similarity = excluded_by_rule[0][1]
            log.info(
                "match.repetition.avoided",
                scene=scene.index,
                asset=excluded_by_rule[0][0].id,
                similarity=round(best_similarity, 3),
            )
            return SceneMatch(
                scene_index=scene.index,
                asset=None,
                score=0.0,
                semantic_score=best_similarity,
                queries=queries,
                reason=(
                    "its best match is already used in this video; "
                    "left for another image rather than repeat it"
                ),
                near_misses=tuple((offers + below)[: self.weights.near_misses]),
            )

        if not scored:
            best_similarity = candidates[0][1]
            return SceneMatch(
                scene_index=scene.index,
                asset=None,
                score=0.0,
                semantic_score=best_similarity,
                queries=queries,
                reason=(
                    f"best similarity {best_similarity:.2f} below threshold "
                    f"{self.weights.minimum_similarity:.2f}"
                ),
                near_misses=self._near_misses(candidates, barred),
            )

        scored.sort(key=lambda item: -item[1])
        best_asset, best_score, best_similarity = scored[0]

        if best_score <= 0.0:
            # Every candidate was penalised below zero — in practice a
            # text-bearing image that passed the similarity threshold only
            # because the library holds nothing else. A gradient is better
            # than burning captions over a page of print (D-082).
            return SceneMatch(
                scene_index=scene.index,
                asset=None,
                score=0.0,
                semantic_score=best_similarity,
                queries=queries,
                reason=(
                    f"best candidate scored {best_score:.2f} after penalties "
                    f"(similarity {best_similarity:.2f}); a gradient is better"
                ),
                # These cleared the similarity threshold but were penalised --
                # in practice images of printed text. Still worth offering: the
                # person may know the caption will not clash (D-082, D-127).
                near_misses=tuple(
                    (asset, round(similarity, 4))
                    for asset, _, similarity in scored[: self.weights.near_misses]
                ),
            )

        return SceneMatch(
            scene_index=scene.index,
            asset=best_asset,
            score=round(best_score, 4),
            semantic_score=round(best_similarity, 4),
            queries=queries,
            alternatives=tuple((a, round(s, 4)) for a, s, _ in scored[1 : alternatives + 1]),
            reason=f"{reason_prefix} '{queries[0].text}'" if queries else reason_prefix,
        )
