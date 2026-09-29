"""Renders that search online for imagery, when the person has allowed it (D-132).

The network is replaced with fakes: these tests pin the decisions -- when to
search, which scenes, what to say when it fails -- not the sourcing adapters,
which have their own tests. The real thing is measured in the browser
verification against the live services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from voxframe.config.settings import Settings
from voxframe.config.userprefs import UserPreferences, sourcing_active
from voxframe.jobs import pipeline
from voxframe.plan.scene_plan import MotionKind, PlanAsset, PlannedScene, ScenePlan


class TestTheRule:
    """Consent and at least one key, from anywhere (D-116, D-132)."""

    def test_consent_and_an_app_key(self) -> None:
        preferences = UserPreferences(sourcing_consent=True, api_keys={"pexels": "k"})

        assert sourcing_active(Settings(), preferences)

    def test_consent_and_an_environment_key(self) -> None:
        """The bug: keys in .env did not count, so switching it on did nothing."""
        preferences = UserPreferences(sourcing_consent=True)

        assert sourcing_active(Settings(pexels_api_key="from-env"), preferences)

    def test_consent_without_any_key(self) -> None:
        """Openverse alone was measured as insufficient (D-077)."""
        assert not sourcing_active(Settings(), UserPreferences(sourcing_consent=True))

    @pytest.mark.parametrize("consent", [None, False])
    def test_a_key_without_consent(self, consent: bool | None) -> None:
        """Having a key is not permission to send anything."""
        preferences = UserPreferences(sourcing_consent=consent, api_keys={"pexels": "k"})

        assert not sourcing_active(Settings(pexels_api_key="env"), preferences)


# --- the pipeline step ----------------------------------------------------------


def _asset(asset_id: str) -> PlanAsset:
    return PlanAsset(
        id=asset_id, path=f"/lib/{asset_id}.jpg", width=1600, height=900,
        license_name="CC0", license_author="A", license_source="Test",
    )


def _plan(filled: int, empty: int, *, card: bool = True) -> ScenePlan:
    scenes: list[PlannedScene] = []
    cursor = 0
    if card:
        scenes.append(PlannedScene(
            index=0, start_frame=0, end_frame=60, card_kind="title",
            card_text="T", motion=MotionKind.NONE,
        ))
        cursor = 60
    for number in range(filled + empty):
        scenes.append(PlannedScene(
            index=len(scenes), start_frame=cursor, end_frame=cursor + 100,
            text=f"a river through a forest {number}",
            asset=_asset(f"a{number}") if number < filled else None,
        ))
        cursor += 100
    return ScenePlan(
        audio_path="a.wav", audio_sha256="0" * 64, audio_duration=cursor / 30,
        fps=30.0, total_frames=cursor, scenes=tuple(scenes),
    )


@dataclass
class FakeFetch:
    downloaded: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.downloaded)


@dataclass
class FakeSourcing:
    fetch: FakeFetch = field(default_factory=FakeFetch)
    adapter_failures: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class FakeIngest:
    added: list[str]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Record what the step asked the network and the library to do."""
    record: dict[str, list] = {"sourced": [], "ingested": []}

    def source_for_plan(plan: ScenePlan, staging: Path, settings: object, **_: object):
        wanted = [
            s.index for s in plan.scenes
            if s.asset is None and not s.is_card and s.text
        ]
        record["sourced"].append(wanted)
        return FakeSourcing(FakeFetch([f"file{i}.jpg" for i in wanted]))

    def ingest_directory(directory: Path, library: object, embedder: object):
        record["ingested"].append(directory)
        return FakeIngest(added=["x"])

    monkeypatch.setattr("voxframe.sourcing.source_for_plan", source_for_plan)
    monkeypatch.setattr("voxframe.library.ingest.ingest_directory", ingest_directory)
    monkeypatch.setattr("voxframe.library.db.AssetLibrary", lambda path: object())
    return record


def _run(plan: ScenePlan, tmp_path: Path):
    events: list[str] = []
    outcome = pipeline._source_missing_imagery(
        plan, tmp_path / "library", Settings(), object(),  # type: ignore[arg-type]
        lambda stage, message, fraction: events.append(message),
    )
    return outcome, events


class TestTheStep:
    def test_only_scenes_without_an_image_are_searched(
        self, calls: dict[str, list], tmp_path: Path
    ) -> None:
        _run(_plan(filled=2, empty=3), tmp_path)

        assert calls["sourced"] == [[3, 4, 5]]

    def test_cards_are_never_searched(
        self, calls: dict[str, list], tmp_path: Path
    ) -> None:
        _run(_plan(filled=0, empty=1), tmp_path)

        assert 0 not in calls["sourced"][0]

    def test_a_full_plan_searches_nothing(
        self, calls: dict[str, list], tmp_path: Path
    ) -> None:
        outcome, _ = _run(_plan(filled=3, empty=0), tmp_path)

        assert calls["sourced"] == []
        assert outcome.added == 0

    def test_downloads_are_added_to_the_library(
        self, calls: dict[str, list], tmp_path: Path
    ) -> None:
        """They stay in <library>/sourced, where the library's paths point (D-074)."""
        outcome, _ = _run(_plan(filled=0, empty=2), tmp_path)

        assert calls["ingested"] == [tmp_path / "library" / "sourced"]
        assert outcome.added == 1

    def test_progress_says_what_is_happening(
        self, calls: dict[str, list], tmp_path: Path
    ) -> None:
        """The search can take a while; the page must not look stuck."""
        _, events = _run(_plan(filled=0, empty=2), tmp_path)

        assert any("Searching online" in event for event in events)

    def test_a_long_recording_is_capped(
        self, calls: dict[str, list], tmp_path: Path
    ) -> None:
        """Hundreds of scenes would spend a whole hour's quota on one video."""
        empty = pipeline.MAX_SOURCED_SCENES + 15
        outcome, _ = _run(_plan(filled=0, empty=empty), tmp_path)

        assert len(calls["sourced"][0]) == pipeline.MAX_SOURCED_SCENES
        assert any("first" in warning for warning in outcome.warnings)


class TestFailuresAreReportedNotRaised:
    """Offline, or a revoked key: the video still gets made (D-132)."""

    def test_an_exception_becomes_a_warning(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def explode(*args: object, **kwargs: object) -> None:
            raise ConnectionError("no route to host")

        monkeypatch.setattr("voxframe.sourcing.source_for_plan", explode)

        outcome, _ = _run(_plan(filled=0, empty=2), tmp_path)

        assert outcome.added == 0
        assert outcome.warnings and "did not work" in outcome.warnings[0]

    def test_a_failing_source_is_named_but_its_error_is_not_shown(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """An error can quote a request URL carrying a key (D-075)."""
        secret = "https://pixabay.com/api/?key=12345678-abcdefabcdefabcdef"  # made up, not a key; gitleaks:allow

        def failing(*args: object, **kwargs: object) -> FakeSourcing:
            return FakeSourcing(adapter_failures=[("pixabay", f"401 at {secret}")])

        monkeypatch.setattr("voxframe.sourcing.source_for_plan", failing)

        outcome, _ = _run(_plan(filled=0, empty=2), tmp_path)

        text = " ".join(outcome.warnings)
        assert "Pixabay" in text
        assert secret not in text
        assert "12345678" not in text

    def test_nothing_found_says_so(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "voxframe.sourcing.source_for_plan", lambda *a, **k: FakeSourcing()
        )

        outcome, _ = _run(_plan(filled=0, empty=2), tmp_path)

        assert outcome.added == 0
        assert any("found nothing" in warning for warning in outcome.warnings)
