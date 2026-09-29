"""A long recording's search budget, spent well (D-166).

v0.1.0 searched the first 40 scenes of a 106-scene recording and left the rest
blank. These pin what replaced that: one request per query however many scenes
share it, recurring themes first, the whole recording covered early, a source
that runs out resting while the others carry on, and downloads kept and reused
under names that never change.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from voxframe.config.settings import Settings
from voxframe.models.asset import AssetKind, LicenseInfo
from voxframe.plan import PlannedScene, PlanWord, ScenePlan
from voxframe.sourcing.base import Adapter, AdapterError, Candidate, SearchRequest
from voxframe.sourcing.fetcher import FetchResult, download_candidates, search_adapters
from voxframe.sourcing.http import RateLimit
from voxframe.sourcing.registry import (
    LONG_RECORDING_SCENES,
    _cap_clips,
    source_for_plan,
    spread_order,
)


class CountingAdapter(Adapter):
    """Returns ``page`` fresh candidates per request, and counts requests."""

    def __init__(
        self,
        name: str = "stock",
        *,
        page: int = 30,
        allowance: int | None = None,
        error: str | None = None,
    ) -> None:
        self.name = name
        self.page = page
        self.allowance = allowance
        self.error = error
        self.requests: list[SearchRequest] = []

    def search(self, request: SearchRequest) -> tuple[Candidate, ...]:
        self.requests.append(request)
        if self.error:
            raise AdapterError(self.error)
        if self.allowance is not None:
            self.allowance -= 1
            self.rate_limit = RateLimit(limit=200, remaining=self.allowance, reset_seconds=900)
        start = len(self.requests) * 1000
        return tuple(
            Candidate(
                url=f"https://example.invalid/{self.name}/{start + i}.jpg",
                license=LicenseInfo(name="CC0-1.0", author="A", source=self.name),
                width=1920,
                height=1080,
                kind=request.kind,
            )
            for i in range(min(self.page, request.limit))
        )


def _plan(texts: list[str]) -> ScenePlan:
    scenes = tuple(
        PlannedScene(
            index=i,
            start_frame=i * 30,
            end_frame=(i + 1) * 30,
            text=text,
            words=(PlanWord(text=text.split()[0], start=float(i), end=i + 0.5),),
        )
        for i, text in enumerate(texts)
    )
    return ScenePlan(
        audio_path="a.wav",
        audio_sha256="0" * 64,
        audio_duration=float(len(texts)),
        fps=30.0,
        total_frames=len(texts) * 30,
        language="en",
        scenes=scenes,
    )


@pytest.fixture
def no_downloads(monkeypatch: pytest.MonkeyPatch) -> list[list[Candidate]]:
    """Record what would be downloaded, without a network."""
    batches: list[list[Candidate]] = []

    def fake(candidates: list[Candidate], *args: object, **kwargs: object) -> FetchResult:
        batches.append(list(candidates))
        return FetchResult()

    monkeypatch.setattr("voxframe.sourcing.registry.download_candidates", fake)
    return batches


def _use(monkeypatch: pytest.MonkeyPatch, *adapters: Adapter) -> None:
    monkeypatch.setattr(
        "voxframe.sourcing.registry.build_adapters", lambda *a, **k: list(adapters)
    )


def _stills_only() -> Settings:
    return Settings(media_mix="stills")


class TestOneSearchServesManyScenes:
    def test_a_shared_query_is_searched_once(
        self, monkeypatch: pytest.MonkeyPatch, no_downloads: list[list[Candidate]]
    ) -> None:
        stock = CountingAdapter()
        _use(monkeypatch, stock)
        # The same subject in ten scenes: the kind of repetition a talk has.
        plan = _plan(["The lighthouse keeper watched the sea"] * 10)

        result = source_for_plan(plan, Path("unused"), _stills_only())

        queries = [request.query for request in stock.requests]
        assert len(queries) == len(set(queries)), "a query was searched twice"
        assert result.searched_scenes == 10
        assert result.shared_searches >= 9

    def test_scenes_sharing_a_query_get_different_images(
        self, monkeypatch: pytest.MonkeyPatch, no_downloads: list[list[Candidate]]
    ) -> None:
        stock = CountingAdapter()
        _use(monkeypatch, stock)
        plan = _plan(["The lighthouse keeper watched the sea"] * 6)

        source_for_plan(plan, Path("unused"), _stills_only(), per_scene=2)

        urls = [candidate.url for candidate in no_downloads[0]]
        assert len(urls) == len(set(urls))
        assert len(urls) >= 12, "each scene should have had its own slice"

    def test_a_recurring_theme_is_searched_first(
        self, monkeypatch: pytest.MonkeyPatch, no_downloads: list[list[Candidate]]
    ) -> None:
        stock = CountingAdapter()
        _use(monkeypatch, stock)
        plan = _plan(
            [f"{subject} and the ocean" for subject in ("Ships", "Whales", "Storms", "Gulls")]
        )

        result = source_for_plan(plan, Path("unused"), _stills_only())

        assert "ocean" in result.theme_queries
        assert stock.requests[0].query == "ocean"


class TestTheWholeRecordingIsCovered:
    def test_scenes_are_searched_spread_through_the_recording(self) -> None:
        order = spread_order(list(range(100)))

        assert sorted(order) == list(range(100))
        # The first ten searched reach across the recording, not 0..9.
        assert max(order[:10]) - min(order[:10]) >= 80

    def test_a_long_recording_is_searched_to_the_end(
        self, monkeypatch: pytest.MonkeyPatch, no_downloads: list[list[Candidate]]
    ) -> None:
        stock = CountingAdapter()
        _use(monkeypatch, stock)
        subjects = ["forest", "river", "mountain", "city", "desert", "harbour", "valley"]
        plan = _plan([f"A walk through the {subjects[i % 7]} at dawn" for i in range(106)])

        result = source_for_plan(plan, Path("unused"), _stills_only())

        assert result.searched_scenes == 106
        assert not result.unsearched
        # Shared searches, so far fewer requests than scenes.
        assert len(stock.requests) < 40

    def test_long_recordings_take_fewer_candidates_per_scene(
        self, monkeypatch: pytest.MonkeyPatch, no_downloads: list[list[Candidate]]
    ) -> None:
        """Every download joins one pool the whole plan is matched against."""
        stock = CountingAdapter()
        _use(monkeypatch, stock)
        plan = _plan([f"Scene number {i} shows a quiet street" for i in range(LONG_RECORDING_SCENES + 10)])

        source_for_plan(plan, Path("unused"), _stills_only(), per_scene=4)

        assert len(no_downloads[0]) <= 2 * (LONG_RECORDING_SCENES + 10) + 30


class TestASourceThatRunsOutRests:
    def test_a_nearly_used_allowance_stops_that_source_only(
        self, monkeypatch: pytest.MonkeyPatch, no_downloads: list[list[Candidate]]
    ) -> None:
        limited = CountingAdapter("pexels", allowance=6)
        other = CountingAdapter("pixabay")
        _use(monkeypatch, limited, other)
        plan = _plan([f"Scene {i}: a {word} in the rain" for i, word in enumerate(
            ["cat", "dog", "car", "tree", "bridge", "lamp", "boat", "bell", "kite", "fox"]
        )])

        result = source_for_plan(plan, Path("unused"), _stills_only())

        assert len(limited.requests) <= 4, "kept asking after its allowance was nearly used"
        assert result.searched_scenes == 10 and not result.unsearched
        assert [name for name, _, _ in result.resting] == ["pexels"]

    def test_when_every_source_rests_the_rest_is_recorded(
        self, monkeypatch: pytest.MonkeyPatch, no_downloads: list[list[Candidate]]
    ) -> None:
        only = CountingAdapter("pexels", allowance=6)
        _use(monkeypatch, only)
        plan = _plan([f"Scene {i}: a {word} in the rain" for i, word in enumerate(
            ["cat", "dog", "car", "tree", "bridge", "lamp", "boat", "bell", "kite", "fox"]
        )])

        result = source_for_plan(plan, Path("unused"), _stills_only())

        assert result.unsearched, "scenes left over must be recorded, not dropped"
        assert result.searched_scenes + len(result.unsearched) == 10
        assert result.resting[0][2] == 900

    def test_a_429_rests_the_source(self) -> None:
        refused = CountingAdapter("pexels", error="pexels rate limit reached (0/200 remaining).")

        for _ in range(3):
            search_adapters([refused], SearchRequest(query="x"))

        assert len(refused.requests) == 1
        assert refused.resting

    def test_an_unreachable_source_rests_after_two_tries(self) -> None:
        offline = CountingAdapter("openverse", error="Could not reach openverse: timeout")

        for _ in range(5):
            search_adapters([offline], SearchRequest(query="x"))

        assert len(offline.requests) == 2


class TestClipsStayWithinTheDownloadCap:
    def test_clip_scenes_are_capped_and_spread(self) -> None:
        settings = Settings(max_download_mb=400, max_clip_mb=25)

        capped = _cap_clips(set(range(0, 100, 2)), settings)

        assert len(capped) == 8  # half of 400 MB, at 25 MB a clip
        assert max(capped) - min(capped) >= 80


def _served(folder: Path, name: str, colour: tuple[int, int, int]) -> Candidate:
    """A candidate whose URL is a real local file, so download works offline."""
    path = folder / name
    Image.new("RGB", (32, 32), colour).save(path)
    return Candidate(
        url=path.as_uri(),
        license=LicenseInfo(name="CC0-1.0", author="A", source="pexels"),
        width=32,
        height=32,
        kind=AssetKind.IMAGE,
    )


class TestDownloadsAreKept:
    def test_a_second_run_reuses_the_first_ones_files(self, tmp_path: Path) -> None:
        served = tmp_path / "served"
        served.mkdir()
        candidates = [_served(served, f"{i}.png", (i * 40, 0, 0)) for i in range(3)]
        staging = tmp_path / "sourced"

        first = download_candidates(candidates, staging)
        second = download_candidates(candidates, staging)

        assert first.count == 3 and not first.reused
        assert second.count == 0 and len(second.reused) == 3

    def test_a_later_run_never_overwrites_an_earlier_image(self, tmp_path: Path) -> None:
        """v0.1.0 named files by position (pexels_0000.jpg...), so the next
        render wrote a different image under a name the library still pointed
        at."""
        served = tmp_path / "served"
        served.mkdir()
        red = _served(served, "red.png", (255, 0, 0))
        blue = _served(served, "blue.png", (0, 0, 255))
        staging = tmp_path / "sourced"

        first = download_candidates([red], staging)
        kept = first.downloaded[0].read_bytes()
        download_candidates([blue], staging)

        assert first.downloaded[0].read_bytes() == kept
        assert len([p for p in staging.iterdir() if p.suffix == ".png"]) == 2
