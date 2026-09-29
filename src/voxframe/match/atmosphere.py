"""The atmospheric fallback: a calm image on the recording's theme (D-137).

When a scene still finds nothing -- no literal match, no metaphor -- it used to
show a plain background. The owner's decision: show a calm, neutral image on
the *whole recording's* theme instead, clearly labelled so it is never taken
for a match, and switchable back to plain with one click.

**The theme** comes from the whole transcript, in order of preference:

1. Metaphor themes that recur across the recording (D-136) -- a talk about
   purpose gets a summit or a sunrise.
2. Otherwise its most frequent concrete words -- a winter poem gets "snow".
   Names are excluded: a capitalised name taken as a subject is how a reader
   called Fox became a photograph of foxes.

**The image** must still clear the usual similarity threshold against the
theme, and is never one already shown elsewhere in the video: an atmospheric
image that repeated a real match would read as a mistake (D-087). If nothing
qualifies, the scene stays plain -- an honest gap beats a filler that fails
the rules.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

import structlog

from voxframe.match.metaphors import load_metaphors, themes_in
from voxframe.match.queries import _normalise, extract_queries
from voxframe.plan.scene_plan import MotionKind, PlanAsset, ScenePlan

if TYPE_CHECKING:
    from voxframe.library.db import AssetLibrary
    from voxframe.library.embeddings import Embedder
    from voxframe.models.asset import Asset

__all__ = ["ATMOSPHERIC", "apply_atmosphere", "theme_queries"]

log = structlog.get_logger(__name__)

#: ``asset_source`` for an atmospheric image. Never counted as a match.
ATMOSPHERIC = "atmospheric"

#: Styles the theme query toward the "calm, neutral" image the owner asked for:
#: an establishing shot rather than a busy or dramatic one.
CALM_STYLE = "calm quiet photograph of {theme}"

#: A word or theme must recur in this many scenes to count as the recording's
#: theme. One mention is a passing reference, not what it is about.
MIN_THEME_SCENES = 2

#: Search terms read from each scene when looking for its recurring words.
THEME_TERMS_PER_SCENE = 12

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def theme_queries(
    texts: Sequence[str], language: str, *, limit: int = 2
) -> list[str]:
    """One or two queries naming what a whole recording is about.

    Args:
        texts: What each scene says, in order.
        language: The recording's language.
        limit: How many theme queries to return.
    """
    language = language if language in ("en", "fr") else "en"
    metaphors = load_metaphors()

    # 1. Recurring metaphor themes: counted per scene, so one long sentence
    #    repeating "goal" does not outvote a theme present throughout.
    theme_count: Counter[str] = Counter()
    for text in texts:
        for theme in themes_in(text, language, metaphors):
            theme_count[theme.name] += 1
    by_name = {theme.name: theme for theme in metaphors.themes}
    recurring = [
        name for name, count in theme_count.most_common() if count >= MIN_THEME_SCENES
    ]
    queries = [by_name[name].queries[0] for name in recurring[:limit]]
    if len(queries) >= limit:
        return queries

    # 2. Concrete words the recording keeps returning to, from each scene's
    #    own search terms (so stopwords, modals and verbs are already gone --
    #    D-135). Counted by how many *scenes* mention a word, not how often:
    #    a LibriVox preamble saying "recording" twice in one sentence is not
    #    what a fable is about, and made "enregistrement" its theme before
    #    this. A word must recur in at least two scenes; if none does, the
    #    scenes stay plain rather than get a guess. Names are skipped too.
    vocabulary = metaphors.vocabulary(language)
    scenes_with: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    position = 0
    for text in texts:
        seen_here: set[str] = set()
        # All of a scene's terms, not its top three: "fire" and "fourmi" each
        # recur across scenes without ever ranking first within one.
        for query in extract_queries(text, language, max_queries=THEME_TERMS_PER_SCENE):
            for word in _WORD.findall(query.text):
                position += 1
                if word[:1].isupper():
                    continue
                key = _normalise(word)
                if len(key) < 3 or key in vocabulary:
                    continue
                # Fold a simple plural onto its singular: "fires" and "fire".
                if key.endswith("s") and key[:-1] in first_seen:
                    key = key[:-1]
                seen_here.add(key)
                first_seen.setdefault(key, position)
        scenes_with.update(seen_here)

    recurring_words = sorted(
        (word for word, count in scenes_with.items() if count >= MIN_THEME_SCENES),
        key=lambda word: (-scenes_with[word], first_seen[word]),
    )
    for word in recurring_words:
        if len(queries) >= limit:
            break
        queries.append(word)
    return queries


def apply_atmosphere(
    plan: ScenePlan,
    library: AssetLibrary,
    embedder: Embedder,
    queries: Sequence[str],
    *,
    threshold: float,
    shows_text: Callable[[str], bool] | None = None,
) -> ScenePlan:
    """Give each still-plain scene a calm image on the recording's theme.

    Only scenes with no image, that no person has touched and that are not
    cards. Their close matches are kept, so a person can still pick one.

    Args:
        plan: The matched plan.
        library: Where to look.
        embedder: Embeds the theme queries (``embed_texts``).
        queries: From :func:`theme_queries`.
        threshold: The matcher's similarity threshold, applied here too: an
            atmospheric image must still actually be *of* the theme.
        shows_text: Says whether an asset's subject is printed text. Such an
            image is skipped outright -- a filler has no business putting words
            under the captions (D-082, D-133).

    Returns:
        A new plan; the original is unchanged.
    """
    plain = [
        scene.index
        for scene in plan.scenes
        if scene.asset is None and not scene.card_kind and scene.asset_source != "user"
    ]
    if not plain or not queries:
        return plan

    used = {scene.asset.id for scene in plan.scenes if scene.asset is not None}
    styled = [CALM_STYLE.format(theme=query) for query in queries]
    vectors = embedder.embed_texts(styled)

    # Best candidates for each theme, strongest first, keeping only images not
    # already in the video and above the threshold.
    pool: list[tuple[float, str, Asset]] = []
    for query, vector in zip(queries, vectors, strict=True):
        for asset, similarity in library.search(
            vector, limit=len(plain) + len(used) + 4,
            embed_model=embedder.model_id,
        ):
            if asset.id in used or similarity < threshold:
                continue
            if shows_text is not None and shows_text(asset.id):
                continue
            pool.append((similarity, query, asset))
    pool.sort(key=lambda item: -item[0])

    chosen: dict[int, tuple[str, Asset, float]] = {}
    taken: set[str] = set()
    # Take themes in turn, so two plain scenes do not both get the first theme
    # when a second one is on offer.
    for index in plain:
        for similarity, query, asset in pool:
            if asset.id in taken:
                continue
            chosen[index] = (query, asset, similarity)
            taken.add(asset.id)
            break

    if not chosen:
        log.info("atmosphere.none_qualified", scenes=len(plain), queries=list(queries))
        return plan

    scenes = []
    for scene in plan.scenes:
        if scene.index not in chosen:
            scenes.append(scene)
            continue
        query, asset, similarity = chosen[scene.index]
        scenes.append(
            scene.model_copy(
                update={
                    "asset": PlanAsset.from_asset(asset),
                    "asset_source": ATMOSPHERIC,
                    "motion": MotionKind.KEN_BURNS,
                    "motion_reason": "atmospheric image",
                    "match_score": 0.0,
                    "semantic_score": round(similarity, 4),
                    "match_reason": (
                        f"atmospheric: calm image on the recording's theme "
                        f"'{query}', not a match for this scene"
                    ),
                }
            )
        )

    log.info("atmosphere.applied", scenes=len(chosen), of=len(plain), queries=list(queries))
    return plan.model_copy(update={"scenes": tuple(scenes)})
