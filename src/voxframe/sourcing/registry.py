"""Which adapters are enabled, and filling a plan's empty scenes from them.

This is where Phase 4's functional goal lives: a narration should produce a
video with no empty gradient scenes (D-069). Sourcing is driven by the scene
plan rather than by a user's search terms, because the plan already records
what each scene is about.

Order matters. Keyless adapters come first so the common case — a user with no
accounts — gets results, and keyed adapters extend rather than replace them.
The registry skips an unavailable adapter silently: an optional feature that
is not configured is not an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from voxframe.config.settings import MediaMix, Settings
from voxframe.match.queries import extract_queries
from voxframe.models.asset import AssetKind, Orientation
from voxframe.plan.scene_plan import ScenePlan
from voxframe.sourcing.base import Adapter, Candidate, SearchRequest
from voxframe.sourcing.fetcher import FetchResult, download_candidates, search_adapters
from voxframe.sourcing.http import RequestCache
from voxframe.sourcing.licenses import DEFAULT_POLICY, LicensePolicy
from voxframe.sourcing.openverse import OpenverseAdapter
from voxframe.sourcing.pexels import PexelsAdapter
from voxframe.sourcing.pixabay import PixabayAdapter

__all__ = ["SourcingPlan", "build_adapters", "source_for_plan"]

log = structlog.get_logger(__name__)

#: Candidates to fetch per empty scene.
#:
#: More than one because the best search result is not always the best match:
#: the matcher re-ranks by CLIP similarity afterwards, and giving it a few
#: options per scene is what turns "filled" into "filled well" (D-069).
CANDIDATES_PER_SCENE = 4

#: More empty scenes than this makes a recording "long" (D-166): each scene
#: then takes fewer candidates of its own, because every download goes into
#: one pool that the whole recording is matched against afterwards.
LONG_RECORDING_SCENES = 40
LONG_CANDIDATES_PER_SCENE = 2

#: A search phrase in at least this many scenes is a recurring theme: searched
#: once, up front, with a page big enough for all of them.
THEME_MIN_SCENES = 3
MAX_THEMES = 12

#: The largest page asked of any source in one request.
MAX_PAGE = 30

#: Share of a render's download cap that video clips may use. A clip is ten
#: to fifty times the size of a still, so without this a long recording spent
#: its whole cap on its first few clip scenes.
CLIP_SHARE = 0.5


def build_adapters(
    settings: Settings,
    policy: LicensePolicy = DEFAULT_POLICY,
    *,
    output_height: int = 1080,
) -> list[Adapter]:
    """Every adapter that can run, in the configured priority order.

    Order comes from ``VOXFRAME_SOURCE_ORDER`` (default
    ``local,pexels,pixabay,openverse``). ``local`` is not an adapter — the
    library is always searched first by the matcher — so it is skipped here.

    An adapter whose key is absent is dropped silently: an optional feature
    that is not configured is not an error, and the keyless path must keep
    working exactly as before (D-079).

    Returns:
        Adapters in search order. Never empty in practice: Openverse needs no
        key, so unless it is removed from the order there is always a source.
    """
    cache = RequestCache(settings.cache_path / "sourcing")
    minimum_width = _minimum_width(output_height)

    known: dict[str, Adapter] = {
        "pexels": PexelsAdapter(
            settings.pexels_api_key, cache=cache, min_width=minimum_width
        ),
        "pixabay": PixabayAdapter(
            settings.pixabay_api_key,
            cache=cache,
            min_width=minimum_width,
            output_height=output_height,
        ),
        "openverse": OpenverseAdapter(policy, cache=cache),
    }

    ordered: list[Adapter] = []
    unavailable: list[str] = []

    for name in settings.source_priority:
        if name == "local":
            # The library is searched by the matcher, not by an adapter.
            continue

        adapter = known.get(name)
        if adapter is None:
            log.warning("sourcing.adapter.unknown", name=name)
            continue

        if adapter.available():
            ordered.append(adapter)
        else:
            unavailable.append(name)

    if unavailable:
        log.debug("sourcing.adapters.unavailable", adapters=unavailable)

    log.info(
        "sourcing.adapters.active",
        active=[adapter.name for adapter in ordered],
        inactive=unavailable,
    )

    return ordered


def _minimum_width(output_height: int) -> int:
    """The narrowest source image worth using at a given output height.

    Ken Burns zooms in, so an image exactly the output width is already too
    small by the end of the move. The headroom matches the default zoom so a
    chosen still does not soften as the camera pushes in (D-036).
    """
    # 16:9 width, plus 25% for the zoom.
    return int(output_height * 16 / 9 * 1.25)


@dataclass
class SourcingPlan:
    """What sourcing did for one scene plan."""

    scenes_needing_assets: int = 0
    queries: list[str] = field(default_factory=list)
    candidates_found: int = 0
    #: Scenes that wanted a clip, found none, and took a still instead (D-086).
    clip_fallbacks: int = 0
    #: Abstract scenes that found images through a visual metaphor (D-136).
    metaphor_searches: int = 0
    fetch: FetchResult = field(default_factory=FetchResult)
    adapter_failures: list[tuple[str, str]] = field(default_factory=list)
    #: Scenes searched, and those left because every source was resting.
    searched_scenes: int = 0
    unsearched: list[int] = field(default_factory=list)
    #: Recurring themes searched once for many scenes (D-166).
    theme_queries: list[str] = field(default_factory=list)
    #: Searches answered from this run's earlier results, not a request.
    shared_searches: int = 0
    #: Sources that stopped partway: name, why, and seconds until they reset.
    resting: list[tuple[str, str, float | None]] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{self.scenes_needing_assets} scene(s) needed imagery",
            f"{self.searched_scenes} searched",
            f"{self.candidates_found} candidate(s) found",
            self.fetch.summary(),
        ]
        if self.shared_searches:
            parts.append(f"{self.shared_searches} search(es) shared between scenes")
        if self.unsearched:
            parts.append(f"{len(self.unsearched)} scene(s) left for a later pass")
        if self.clip_fallbacks:
            parts.append(f"{self.clip_fallbacks} clip scene(s) fell back to stills")
        if self.adapter_failures:
            parts.append(f"{len(self.adapter_failures)} adapter(s) failed")
        return "; ".join(parts)


def _orientation_for(plan: ScenePlan) -> str:
    """The image shape that suits this plan's output."""
    return {
        "9:16": Orientation.PORTRAIT.value,
        "16:9": Orientation.LANDSCAPE.value,
        "1:1": Orientation.SQUARE.value,
    }.get(plan.aspect.value, Orientation.LANDSCAPE.value)


def _queries_for(plan: ScenePlan, index: int) -> list[str]:
    """Search terms for one scene, broadest last.

    **Keywords, not the full sentence** — the opposite of what the matcher
    does, and deliberately so (D-073). D-051 established that full-sentence
    search beats extracted keywords for CLIP, because that is a semantic
    comparison in embedding space. A stock-media API does literal keyword
    matching, where a 102-character sentence matches nothing: sourcing for a
    real recording returned 1 candidate across 10 scenes before this.

    Several queries rather than one because these APIs **AND** their terms, so
    a longer phrase is a narrower search, not a richer one. Measured against
    Openverse (D-076):

    ==============================  ==========
    query                           candidates
    ==============================  ==========
    ``winter frozen pulse heart``   0
    ``ice June streams``            5
    ``snow``                        5
    ==============================  ==========

    So the caller tries each in turn and stops at the first that returns
    anything: specific enough to be apt when the library has it, broad enough
    to fill the scene when it does not.

    Returns:
        Queries from most to least specific. Empty when the scene has no
        content words at all, which is legitimate for filler-only scenes.
    """
    scene = plan.scenes[index]

    # A user-edited query wins outright (D-052): editing it is how a person
    # steers image choice, and overriding that would make the edit pointless.
    if scene.queries and scene.query_source.value == "user":
        return [" ".join(scene.queries)]

    text = scene.display_text.strip()
    if not text:
        return [" ".join(scene.queries)] if scene.queries else []

    extracted = extract_queries(text, plan.language, max_queries=3)
    if not extracted:
        return [" ".join(scene.queries)] if scene.queries else []

    phrases = [query.text for query in extracted]

    candidates: list[str] = []
    # Two phrases: specific, and apt when the source has it.
    if len(phrases) >= 2:
        candidates.append(f"{phrases[0]} {phrases[1]}")
    # Each phrase alone, highest-weighted first.
    candidates.extend(phrases)

    # Deduplicate while keeping order: a one-phrase scene would otherwise
    # search the same terms twice.
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        normalised = candidate.strip().lower()
        if normalised and normalised not in seen:
            seen.add(normalised)
            ordered.append(candidate.strip())

    return ordered


#: Images fetched per abstract scene from its metaphor search, on top of the
#: literal ones. Two: enough to choose between, few enough that a talk full of
#: abstract speech does not double its downloads.
METAPHOR_CANDIDATES = 2


def _metaphors_for(plan: ScenePlan, index: int) -> list[str]:
    """Visual-metaphor queries for a scene, if its own terms are abstract."""
    from voxframe.match.metaphors import metaphor_queries

    scene = plan.scenes[index]
    if scene.queries and scene.query_source.value == "user":
        # A person's own query says what the scene is about (D-052).
        return []
    text = scene.display_text.strip()
    if not text:
        return []
    own = [query.text for query in extract_queries(text, plan.language, max_queries=3)]
    return metaphor_queries(text, own, plan.language)


def _scenes_wanting_clips(
    targets: list[int], settings: Settings
) -> set[int]:
    """Which scenes should look for a video clip rather than a still.

    Spread evenly rather than taken from the front: clustering every clip at
    the start would make the video change character halfway through. An even
    spread gives motion throughout, which is the point of mixing at all
    (D-085).

    Returns:
        Scene indices to search for clips. Empty for ``stills``; everything
        for ``clips``.
    """
    if settings.resolved_media_mix is MediaMix.STILLS or not targets:
        return set()

    if settings.resolved_media_mix is MediaMix.CLIPS:
        return set(targets)

    wanted = round(len(targets) * settings.clip_ratio)
    if wanted <= 0:
        return set()
    if wanted >= len(targets):
        return set(targets)

    step = len(targets) / wanted
    return {targets[min(len(targets) - 1, int(i * step))] for i in range(wanted)}


class _Pool:
    """Each query's results in this run, handed out a fresh slice at a time.

    Scenes of one recording search the same words again and again: a talk
    about faith says "faith" in a dozen scenes. Searching once and giving each
    scene its own slice of one bigger page costs one request instead of a
    dozen, and gives the scenes different images rather than the same few
    (D-166).
    """

    def __init__(self) -> None:
        self._found: dict[tuple[str, AssetKind], list[Candidate]] = {}
        self._taken: dict[tuple[str, AssetKind], int] = {}

    @staticmethod
    def key(query: str, kind: AssetKind) -> tuple[str, AssetKind]:
        return (" ".join(query.lower().split()), kind)

    def has(self, query: str, kind: AssetKind) -> bool:
        return self.key(query, kind) in self._found

    def put(self, query: str, kind: AssetKind, candidates: list[Candidate]) -> None:
        self._found[self.key(query, kind)] = candidates

    def take(self, query: str, kind: AssetKind, count: int) -> list[Candidate]:
        key = self.key(query, kind)
        start = self._taken.get(key, 0)
        chunk = self._found.get(key, [])[start : start + count]
        self._taken[key] = start + len(chunk)
        return chunk


def spread_order(items: list[int]) -> list[int]:
    """The same items, ordered to cover the whole range early.

    First, middle, quarters, eighths... (the van der Corput sequence). If the
    sources run out partway, the scenes searched are spread through the
    recording rather than bunched at its start, which is what "Searched the
    first 40 of 106 scenes" did.
    """
    count = len(items)

    def radical_inverse(position: int) -> float:
        result, base = 0.0, 0.5
        while position:
            if position & 1:
                result += base
            position >>= 1
            base /= 2
        return result

    order = sorted(range(count), key=radical_inverse)
    return [items[position] for position in order]


def _cap_clips(scenes: set[int], settings: Settings) -> set[int]:
    """At most as many clip scenes as the clip share of the download cap allows.

    Kept evenly spread, like the choice of clip scenes itself (D-085).
    """
    most = int(settings.max_download_mb * CLIP_SHARE // max(1, settings.resolved_max_clip_mb))
    if len(scenes) <= most:
        return scenes
    ordered = sorted(scenes)
    if most <= 0:
        return set()
    step = len(ordered) / most
    return {ordered[int(i * step)] for i in range(most)}


def _search_scene(
    adapters: list[Adapter],
    plan: ScenePlan,
    index: int,
    *,
    orientation: str,
    kind: AssetKind,
    limit: int,
    policy: LicensePolicy,
    result: SourcingPlan,
    queries: list[str] | None = None,
    pool: _Pool | None = None,
    demand: dict[str, int] | None = None,
) -> list[Candidate]:
    """Search one scene, broadening the query until something returns.

    These APIs AND their terms, so a specific query returning nothing is not
    evidence that the scene cannot be illustrated (D-076).

    Args:
        queries: Queries to try in place of the scene's own, e.g. its visual
            metaphors (D-136).
        pool: This run's results so far. A query already searched is answered
            from it, with a slice no other scene has had.
        demand: How many scenes' query lists hold each query, so the first
            search for it asks for enough for all of them.

    Returns:
        Candidates, or an empty list when every query came back empty.
    """
    pool = pool if pool is not None else _Pool()
    for query in queries if queries is not None else _queries_for(plan, index):
        if pool.has(query, kind):
            result.shared_searches += 1
        else:
            wanted = (demand or {}).get(_Pool.key(query, kind)[0], 1)
            found, failures = search_adapters(
                adapters,
                SearchRequest(
                    query=query,
                    orientation=orientation,
                    kind=kind,
                    limit=min(MAX_PAGE, limit * max(1, wanted)),
                    language=plan.language,
                    commercial_only=not policy.allow_non_commercial,
                ),
            )
            result.adapter_failures.extend(failures)
            pool.put(query, kind, list(found))

        mine = pool.take(query, kind, limit)
        if mine:
            result.queries.append(query)
            return mine

    return []


def source_for_plan(
    plan: ScenePlan,
    staging_dir: Path,
    settings: Settings,
    *,
    policy: LicensePolicy = DEFAULT_POLICY,
    per_scene: int = CANDIDATES_PER_SCENE,
    only_empty: bool = True,
) -> SourcingPlan:
    """Fetch imagery for a plan's scenes into a staging directory.

    Args:
        plan: The plan whose scenes need filling.
        staging_dir: Where downloads land, to be ingested afterwards.
        settings: For adapter configuration.
        policy: Which licenses may be sourced.
        per_scene: Candidates to fetch per scene.
        only_empty: Fetch only for scenes with no asset. ``False`` sources for
            every scene, which is what a user wanting more choice asks for.

    Returns:
        What was searched, found and downloaded. Adapter failures are
        collected rather than raised: a user offline should get a clear report
        and whatever was already in the library, not a traceback.
    """
    adapters = build_adapters(settings, policy)
    result = SourcingPlan()

    targets = [
        scene.index
        for scene in plan.scenes
        if (scene.asset is None or not only_empty) and _queries_for(plan, scene.index)
    ]
    result.scenes_needing_assets = len(targets)

    if not targets:
        log.info("sourcing.nothing_to_do", scenes=len(plan.scenes))
        return result

    orientation = _orientation_for(plan)
    long_recording = len(targets) > LONG_RECORDING_SCENES
    if long_recording:
        per_scene = min(per_scene, LONG_CANDIDATES_PER_SCENE)
    clip_scenes = _cap_clips(_scenes_wanting_clips(targets, settings), settings)
    all_candidates: list[Candidate] = []
    seen: set[str] = set()
    # Which query found each candidate, recorded per asset (item 25).
    found_by: dict[str, str] = {}
    pool = _Pool()

    # How many scenes would search each query, so one request can serve them.
    chains = {index: _queries_for(plan, index) for index in targets}
    demand: dict[str, int] = {}
    for chain in chains.values():
        for query in {_Pool.key(q, AssetKind.IMAGE)[0] for q in chain}:
            demand[query] = demand.get(query, 0) + 1

    def keep(batch: list[Candidate], query: str) -> None:
        for candidate in batch:
            # Neighbouring scenes often search similar text, so the same
            # image comes back repeatedly. Downloading it once is both
            # faster and what the library's own dedupe would arrive at.
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            all_candidates.append(candidate)
            found_by[candidate.url] = query

    # Recurring themes first: each serves many scenes for one request, so they
    # are the best use of a source's hourly allowance (D-166).
    themes = sorted(
        (query for query, scenes in demand.items() if scenes >= THEME_MIN_SCENES),
        key=lambda query: -demand[query],
    )[:MAX_THEMES]
    for theme in themes:
        found, failures = search_adapters(
            adapters,
            SearchRequest(
                query=theme,
                orientation=orientation,
                kind=AssetKind.IMAGE,
                limit=min(MAX_PAGE, per_scene * demand[theme]),
                language=plan.language,
                commercial_only=not policy.allow_non_commercial,
            ),
        )
        result.adapter_failures.extend(failures)
        pool.put(theme, AssetKind.IMAGE, list(found))
        if found:
            result.theme_queries.append(theme)

    order = spread_order(targets)
    for position, index in enumerate(order):
        if all(adapter.resting for adapter in adapters if adapter.available()):
            # Every source has used its allowance. What is left is recorded
            # rather than silently dropped, so the person can be told, and a
            # later render continues from here: everything found so far stays
            # in the library and the searches stay cached.
            result.unsearched = sorted(order[position:])
            break
        result.searched_scenes += 1
        wants_clip = index in clip_scenes

        found = _search_scene(
            adapters,
            plan,
            index,
            orientation=orientation,
            kind=AssetKind.VIDEO if wants_clip else AssetKind.IMAGE,
            limit=1 if wants_clip and long_recording else 2 if wants_clip else per_scene,
            policy=policy,
            result=result,
            pool=pool,
            demand=demand,
        )

        if wants_clip and not found:
            # No clip matched. Falling through to a still is what keeps fill
            # rate with clips at least as high as stills-only: the alternative
            # is a gradient, and a still is strictly better than that (D-086).
            log.info("sourcing.clip.fallback_to_still", scene=index)
            found = _search_scene(
                adapters,
                plan,
                index,
                orientation=orientation,
                kind=AssetKind.IMAGE,
                limit=per_scene,
                policy=policy,
                result=result,
                pool=pool,
                demand=demand,
            )
            if found:
                result.clip_fallbacks += 1

        # An abstract scene also gets a search for its visual metaphor, as a
        # second pass rather than a replacement: the query chain stops at the
        # first that returns anything, so a metaphor placed last would never
        # run, and placed first it would stop the literal search that found,
        # for instance, a praying silhouette for "when God calls a man" (D-136).
        #
        # Each candidate keeps the query that found it, for its provenance
        # (Phase 4 item 25): reading the last query after both passes would
        # credit the metaphor with the literal images too.
        literal_query = result.queries[-1] if found and result.queries else ""
        batches: list[tuple[list[Candidate], str]] = [(found[:per_scene], literal_query)]
        metaphors = _metaphors_for(plan, index)
        if metaphors:
            extra = _search_scene(
                adapters,
                plan,
                index,
                orientation=orientation,
                kind=AssetKind.IMAGE,
                limit=METAPHOR_CANDIDATES,
                policy=policy,
                result=result,
                queries=metaphors,
                pool=pool,
            )[:METAPHOR_CANDIDATES]
            if extra:
                batches.append((extra, result.queries[-1]))
                result.metaphor_searches += 1

        if not any(batch for batch, _ in batches):
            log.debug("sourcing.scene.no_results", scene=index)

        for batch, query in batches:
            keep(batch, query)

    # Theme results no scene took are still good images on the recording's
    # subject: the whole plan is matched against the library afterwards, so
    # they fill scenes whose own searches found nothing.
    for theme in result.theme_queries:
        keep(pool.take(theme, AssetKind.IMAGE, MAX_PAGE), theme)

    result.resting = [
        (adapter.name, adapter.resting, adapter.rest_seconds)
        for adapter in adapters
        if adapter.resting
    ]
    result.candidates_found = len(all_candidates)

    if not all_candidates:
        log.warning(
            "sourcing.no_candidates",
            scenes=len(targets),
            failures=len(result.adapter_failures),
        )
        return result

    result.fetch = download_candidates(
        all_candidates,
        staging_dir,
        queries_by_url=found_by,
        max_file_mb=settings.resolved_max_clip_mb,
        max_total_mb=settings.max_download_mb,
    )

    log.info("sourcing.done", summary=result.summary())
    return result


def source_queries(
    queries: list[str],
    staging_dir: Path,
    settings: Settings,
    *,
    language: str = "en",
    orientation: str = "landscape",
    per_query: int = 4,
    policy: LicensePolicy = DEFAULT_POLICY,
) -> SourcingPlan:
    """Fetch images for free-standing queries, not tied to any one scene.

    Used for the atmospheric fallback (D-137): a recording's overall theme is
    searched once, and the results serve whichever scenes found nothing of
    their own. Each download keeps the query that found it, for provenance.

    Failures are collected, never raised, like :func:`source_for_plan`.
    """
    adapters = build_adapters(settings, policy)
    result = SourcingPlan()
    candidates: list[Candidate] = []
    found_by: dict[str, str] = {}

    for query in queries:
        found, failures = search_adapters(
            adapters,
            SearchRequest(
                query=query,
                orientation=orientation,
                kind=AssetKind.IMAGE,
                limit=per_query,
                language=language,
                commercial_only=not policy.allow_non_commercial,
            ),
        )
        result.adapter_failures.extend(failures)
        if found:
            result.queries.append(query)
        for candidate in found[:per_query]:
            if candidate.url not in found_by:
                found_by[candidate.url] = query
                candidates.append(candidate)

    result.candidates_found = len(candidates)
    if candidates:
        result.fetch = download_candidates(
            candidates,
            staging_dir,
            queries_by_url=found_by,
            max_file_mb=settings.resolved_max_clip_mb,
            max_total_mb=settings.max_download_mb,
        )
    log.info("sourcing.theme.done", queries=queries, summary=result.summary())
    return result
