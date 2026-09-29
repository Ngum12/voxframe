"""Sourcing: license policy, provenance, and query construction.

The licensing rules are the part worth testing hardest. Fetching an image is
trivial; fetching one whose terms the user then unknowingly breaks is a real
harm, and a bug here is invisible until someone is in trouble.

Nothing here touches the network: adapters are faked, and the one real HTTP
seam is patched.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from voxframe.models.asset import AssetKind, LicenseInfo
from voxframe.models.provenance import (
    PROVENANCE_SUFFIX,
    read_provenance,
    write_provenance,
)
from voxframe.plan import PlannedScene, PlanWord, QuerySource, ScenePlan
from voxframe.sourcing import (
    DEFAULT_POLICY,
    Adapter,
    AdapterError,
    Candidate,
    LicensePolicy,
    SearchRequest,
    parse_license,
    search_adapters,
)
from voxframe.sourcing.openverse import OpenverseAdapter
from voxframe.sourcing.registry import _queries_for


class TestLicenseParsing:
    @pytest.mark.parametrize(
        ("code", "version", "expected"),
        [
            ("cc0", "1.0", "CC0-1.0"),
            ("pdm", "1.0", "Public Domain Mark 1.0"),
            ("by", "2.0", "CC-BY-2.0"),
            ("by-sa", "4.0", "CC-BY-SA-4.0"),
            ("CC-BY-4.0", "", "CC-BY-4.0"),
        ],
    )
    def test_known_codes_are_canonicalised(
        self, code: str, version: str, expected: str
    ) -> None:
        terms = parse_license(code, version)
        assert terms is not None
        assert terms.code == expected

    def test_unknown_code_returns_none(self) -> None:
        """Guessing would put the user in breach of terms nobody checked."""
        assert parse_license("some-invented-license") is None
        assert parse_license("") is None

    def test_public_domain_has_no_obligations(self) -> None:
        terms = parse_license("cc0", "1.0")
        assert terms is not None
        assert terms.is_public_domain
        assert terms.allows_commercial
        assert not terms.requires_attribution

    def test_nc_forbids_commercial_use(self) -> None:
        terms = parse_license("by-nc", "4.0")
        assert terms is not None
        assert not terms.allows_commercial

    def test_sa_is_flagged(self) -> None:
        terms = parse_license("by-sa", "4.0")
        assert terms is not None
        assert terms.is_share_alike

    def test_nd_forbids_modification(self) -> None:
        terms = parse_license("by-nd", "4.0")
        assert terms is not None
        assert not terms.allows_modification

    def test_deed_url_is_recorded(self) -> None:
        """Credits should link to the terms, not merely name them."""
        terms = parse_license("by-sa", "4.0")
        assert terms is not None
        assert terms.url.startswith("https://creativecommons.org/")

    def test_summary_names_the_obligations(self) -> None:
        terms = parse_license("by-nc-sa", "4.0")
        assert terms is not None
        summary = terms.summary()
        assert "credit" in summary
        assert "same license" in summary
        assert "NO COMMERCIAL" in summary


class TestLicensePolicy:
    def test_default_accepts_public_domain(self) -> None:
        terms = parse_license("cc0", "1.0")
        assert terms is not None
        assert DEFAULT_POLICY.permits(terms)

    def test_default_accepts_attribution(self) -> None:
        terms = parse_license("by", "4.0")
        assert terms is not None
        assert DEFAULT_POLICY.permits(terms)

    def test_default_rejects_share_alike(self) -> None:
        """It would impose a license on the user's own video (D-071)."""
        terms = parse_license("by-sa", "4.0")
        assert terms is not None
        assert not DEFAULT_POLICY.permits(terms)
        assert "same license" in DEFAULT_POLICY.rejection_reason(terms)

    def test_default_rejects_non_commercial(self) -> None:
        terms = parse_license("by-nc", "4.0")
        assert terms is not None
        assert not DEFAULT_POLICY.permits(terms)

    @pytest.mark.parametrize(
        "policy",
        [
            LicensePolicy(),
            LicensePolicy(allow_share_alike=True),
            LicensePolicy(allow_non_commercial=True),
            LicensePolicy(allow_share_alike=True, allow_non_commercial=True),
        ],
    )
    def test_no_derivatives_is_rejected_at_every_setting(
        self, policy: LicensePolicy
    ) -> None:
        """Ken Burns crops and scales, so ND is unusable, not merely awkward."""
        terms = parse_license("by-nd", "4.0")
        assert terms is not None
        assert not policy.permits(terms)
        assert "modification" in policy.rejection_reason(terms)

    def test_opting_in_accepts_share_alike(self) -> None:
        terms = parse_license("by-sa", "4.0")
        assert terms is not None
        assert LicensePolicy(allow_share_alike=True).permits(terms)

    def test_openverse_codes_follow_the_policy(self) -> None:
        assert "by-sa" not in DEFAULT_POLICY.openverse_codes()
        assert "by-sa" in LicensePolicy(allow_share_alike=True).openverse_codes()
        assert "cc0" in DEFAULT_POLICY.openverse_codes()

    def test_permissive_policy_is_public_domain_only(self) -> None:
        from voxframe.sourcing import PERMISSIVE_POLICY

        attribution = parse_license("by", "4.0")
        public_domain = parse_license("cc0", "1.0")
        assert attribution is not None and public_domain is not None

        assert not PERMISSIVE_POLICY.permits(attribution)
        assert PERMISSIVE_POLICY.permits(public_domain)


class TestProvenanceSidecar:
    def _license(self, author: str = "A Photographer") -> LicenseInfo:
        return LicenseInfo(
            name="CC-BY-4.0",
            author=author,
            source="openverse",
            source_url="https://example.invalid/photo",
        )

    def test_round_trip(self, tmp_path: Path) -> None:
        media = tmp_path / "photo.jpg"
        media.write_bytes(b"not really a jpeg")

        write_provenance(media, self._license())
        recovered = read_provenance(media)

        assert recovered is not None
        assert recovered.author == "A Photographer"
        assert recovered.name == "CC-BY-4.0"

    def test_sidecar_name_appends_rather_than_replaces(self, tmp_path: Path) -> None:
        """photo.jpg and photo.png must not share one sidecar."""
        media = tmp_path / "photo.jpg"
        media.write_bytes(b"x")
        write_provenance(media, self._license())

        assert (tmp_path / f"photo.jpg{PROVENANCE_SUFFIX}").is_file()

    def test_missing_sidecar_returns_none(self, tmp_path: Path) -> None:
        """A hand-dropped file falls back to the folder's license."""
        media = tmp_path / "photo.jpg"
        media.write_bytes(b"x")

        assert read_provenance(media) is None

    def test_malformed_sidecar_returns_none(self, tmp_path: Path) -> None:
        """Better to ingest with the folder license than drop the asset."""
        media = tmp_path / "photo.jpg"
        media.write_bytes(b"x")
        (tmp_path / f"photo.jpg{PROVENANCE_SUFFIX}").write_text("{ not json")

        assert read_provenance(media) is None

    def test_sidecar_is_readable_json(self, tmp_path: Path) -> None:
        """A user should be able to check provenance without running Voxframe."""
        media = tmp_path / "photo.jpg"
        media.write_bytes(b"x")
        write_provenance(media, self._license())

        raw = json.loads(
            (tmp_path / f"photo.jpg{PROVENANCE_SUFFIX}").read_text(encoding="utf-8")
        )
        assert raw["license"]["author"] == "A Photographer"


class _FakeAdapter(Adapter):
    """An adapter returning canned results, for registry tests."""

    def __init__(
        self,
        name: str,
        results: list[Candidate] | None = None,
        *,
        error: str | None = None,
        available: bool = True,
    ) -> None:
        self.name = name
        self._results = results or []
        self._error = error
        self._available = available

    def search(self, request: SearchRequest) -> tuple[Candidate, ...]:
        if self._error:
            raise AdapterError(self._error)
        return tuple(self._results)

    def available(self) -> bool:
        return self._available


def _candidate(source: str, index: int) -> Candidate:
    return Candidate(
        url=f"https://example.invalid/{source}/{index}.jpg",
        license=LicenseInfo(
            name="CC0-1.0", author="Someone", source=source, source_url=""
        ),
        width=1600,
        height=1200,
        kind=AssetKind.IMAGE,
    )


class TestSearchAdapters:
    def test_results_are_interleaved(self) -> None:
        """One source must not monopolise the results."""
        first = _FakeAdapter("a", [_candidate("a", i) for i in range(3)])
        second = _FakeAdapter("b", [_candidate("b", i) for i in range(3)])

        found, failures = search_adapters(
            [first, second], SearchRequest(query="x", limit=6)
        )

        assert not failures
        sources = [c.license.source for c in found]
        assert sources[:4] == ["a", "b", "a", "b"]

    def test_a_failing_adapter_does_not_stop_the_others(self) -> None:
        """A user offline should get what could be fetched, not a traceback."""
        broken = _FakeAdapter("broken", error="network down")
        working = _FakeAdapter("working", [_candidate("working", 0)])

        found, failures = search_adapters(
            [broken, working], SearchRequest(query="x")
        )

        assert len(found) == 1
        assert failures == [("broken", "network down")]

    def test_unavailable_adapters_are_skipped_silently(self) -> None:
        """An unconfigured optional feature is not an error."""
        unconfigured = _FakeAdapter("keyed", [_candidate("keyed", 0)], available=False)

        found, failures = search_adapters(
            [unconfigured], SearchRequest(query="x")
        )

        assert not found
        assert not failures

    def test_no_adapters_returns_empty(self) -> None:
        found, failures = search_adapters([], SearchRequest(query="x"))
        assert not found
        assert not failures


class TestSourcingQueries:
    """Sourcing searches keywords, not sentences (D-073, D-076)."""

    def _plan(self, text: str, **scene_kwargs: object) -> ScenePlan:
        return ScenePlan(
            audio_path="a.wav",
            audio_sha256="0" * 64,
            audio_duration=3.0,
            fps=30.0,
            total_frames=90,
            language="en",
            scenes=(
                PlannedScene(
                    index=0,
                    start_frame=0,
                    end_frame=90,
                    text=text,
                    words=(PlanWord(text=text.split()[0], start=0.0, end=0.5),),
                    **scene_kwargs,  # type: ignore[arg-type]
                ),
            ),
        )

    def test_a_long_sentence_becomes_short_queries(self) -> None:
        """A stock API ANDs its terms: a full sentence matches nothing."""
        # Henry David Thoreau, Walden (1854).
        plan = self._plan(
            "I went to the woods because I wished to live deliberately, to front "
            "only the essential facts of life, and see if I could not learn what it "
            "had to teach."
        )
        queries = _queries_for(plan, 0)

        assert queries
        assert all(len(query.split()) <= 6 for query in queries)
        assert len(queries[0]) < 40

    def test_queries_broaden(self) -> None:
        """Later queries must be no more specific than earlier ones."""
        plan = self._plan("Coral reefs are dying in warm tropical oceans")
        queries = _queries_for(plan, 0)

        assert len(queries) >= 2
        assert len(queries[-1].split()) <= len(queries[0].split())

    def test_user_edited_queries_win(self) -> None:
        """Editing a query is how a person steers image choice (D-052)."""
        plan = self._plan(
            "Some unrelated spoken text here",
            queries=("mountain sunrise",),
            query_source=QuerySource.USER,
        )

        assert _queries_for(plan, 0) == ["mountain sunrise"]

    def test_queries_are_deduplicated(self) -> None:
        plan = self._plan("Snow falls")
        queries = _queries_for(plan, 0)

        assert len(queries) == len({q.lower() for q in queries})

    def test_a_scene_with_no_content_words_yields_nothing(self) -> None:
        plan = self._plan("and the of a")
        assert _queries_for(plan, 0) == []


class TestOpenverseAdapter:
    def _result(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "url": "https://example.invalid/photo.jpg",
            "license": "by",
            "license_version": "4.0",
            "creator": "A Photographer",
            "foreign_landing_url": "https://example.invalid/page",
            "width": 1600,
            "height": 1200,
            "title": "A photo",
        }
        base.update(overrides)
        return base

    def _search(self, results: list[dict[str, object]], **kwargs: object) -> tuple[Candidate, ...]:
        adapter = OpenverseAdapter(**kwargs)  # type: ignore[arg-type]
        with mock.patch.object(
            adapter, "_get", return_value={"results": results}
        ):
            return adapter.search(SearchRequest(query="test", limit=5))

    def test_a_usable_result_becomes_a_candidate(self) -> None:
        found = self._search([self._result()])

        assert len(found) == 1
        assert found[0].license.name == "CC-BY-4.0"
        assert found[0].license.author == "A Photographer"
        assert found[0].license.source == "openverse"

    def test_an_unrecognised_license_is_dropped(self) -> None:
        found = self._search([self._result(license="mystery-license")])
        assert not found

    def test_a_result_with_no_url_is_dropped(self) -> None:
        found = self._search([self._result(url="")])
        assert not found

    def test_policy_rejection_drops_the_result(self) -> None:
        found = self._search([self._result(license="by-sa")])
        assert not found

    def test_missing_creator_becomes_unknown_not_blank(self) -> None:
        """Provenance cannot be blank (D-035)."""
        found = self._search([self._result(creator="")])

        assert len(found) == 1
        assert found[0].license.author == "Unknown"

    def test_orientation_is_translated(self) -> None:
        """Openverse uses wide/tall/square and rejects anything else."""
        adapter = OpenverseAdapter()
        parameters = adapter._parameters(
            SearchRequest(query="x", orientation="landscape")
        )
        assert parameters["aspect_ratio"] == "wide"

    def test_no_orientation_omits_the_parameter(self) -> None:
        adapter = OpenverseAdapter()
        parameters = adapter._parameters(SearchRequest(query="x"))
        assert "aspect_ratio" not in parameters

    def test_license_filter_is_applied_at_the_api(self) -> None:
        adapter = OpenverseAdapter()
        parameters = adapter._parameters(SearchRequest(query="x"))

        assert "by-sa" not in parameters["license"]
        assert "cc0" in parameters["license"]

    def test_video_requests_return_nothing(self) -> None:
        adapter = OpenverseAdapter()
        found = adapter.search(SearchRequest(query="x", kind=AssetKind.VIDEO))
        assert found == ()

    def test_needs_no_api_key(self) -> None:
        """The core pipeline must work with no account anywhere."""
        adapter = OpenverseAdapter()
        assert not adapter.requires_key
        assert adapter.available()

    def test_a_malformed_body_raises(self) -> None:
        adapter = OpenverseAdapter()
        with (
            mock.patch.object(adapter, "_get", return_value={"unexpected": 1}),
            pytest.raises(AdapterError),
        ):
            adapter.search(SearchRequest(query="x"))


class TestPexelsAdapter:
    """Pexels is optional and must stay off without a key (D-079)."""

    def _result(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "id": 1,
            "width": 4000,
            "height": 3000,
            "url": "https://www.pexels.com/photo/winter-1/",
            "photographer": "A Photographer",
            "photographer_url": "https://www.pexels.com/@someone",
            "alt": "Snow covered pine forest at dawn",
            "src": {
                "original": "https://images.pexels.com/photos/1/original.jpg",
                "large2x": "https://images.pexels.com/photos/1/large2x.jpg",
                "medium": "https://images.pexels.com/photos/1/medium.jpg",
            },
        }
        base.update(overrides)
        return base

    def _search(
        self, results: list[dict[str, object]], **kwargs: object
    ) -> tuple[Candidate, ...]:
        from voxframe.sourcing.pexels import PexelsAdapter

        adapter = PexelsAdapter("fake-key-for-tests", **kwargs)  # type: ignore[arg-type]
        with mock.patch.object(
            adapter, "_get", return_value=({"photos": results}, None)
        ):
            return adapter.search(SearchRequest(query="winter", limit=5))

    def test_no_key_means_unavailable(self) -> None:
        from voxframe.sourcing.pexels import PexelsAdapter

        adapter = PexelsAdapter("")
        assert not adapter.available()
        assert adapter.search(SearchRequest(query="x")) == ()

    def test_unavailable_reason_says_where_to_get_a_key(self) -> None:
        from voxframe.sourcing.pexels import PexelsAdapter

        assert "pexels.com/api" in PexelsAdapter("").unavailable_reason()

    def test_a_photo_becomes_a_candidate(self) -> None:
        found = self._search([self._result()])

        assert len(found) == 1
        assert found[0].license.author == "A Photographer"
        assert found[0].license.source == "pexels"
        assert found[0].license.requires_attribution

    def test_photographer_profile_is_recorded(self) -> None:
        """Pexels asks that the photographer be credited and linked."""
        found = self._search([self._result()])
        assert found[0].license.source_url == "https://www.pexels.com/@someone"

    def test_description_is_carried_as_the_title(self) -> None:
        """The alt text is the only clue an image depicts text (D-082)."""
        found = self._search([self._result()])
        assert "pine forest" in found[0].title

    def test_large2x_is_preferred_over_original(self) -> None:
        """A 6000px original is a slow download for no visible gain."""
        found = self._search([self._result()])
        assert "large2x" in found[0].url

    def test_missing_photographer_becomes_unknown(self) -> None:
        found = self._search([self._result(photographer="")])
        assert found[0].license.author == "Unknown"

    def test_too_small_is_dropped(self) -> None:
        """An image narrower than the render needs is soft after the zoom."""
        found = self._search([self._result(width=640, height=480)], min_width=1280)
        assert not found

    def test_a_result_with_no_sources_is_dropped(self) -> None:
        found = self._search([self._result(src={})])
        assert not found


class TestPixabayAdapter:
    """Pixabay terms are implemented, not merely noted (D-080)."""

    def _hit(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "id": 42,
            "pageURL": "https://pixabay.com/photos/winter-42/",
            "tags": "winter, forest, snow",
            "user": "SomeUser",
            "user_id": 998877,
            "imageWidth": 6000,
            "imageHeight": 4000,
            "webformatURL": "https://pixabay.com/get/expiring.jpg",
            "largeImageURL": "https://pixabay.com/get/large.jpg",
            "fullHDURL": None,
        }
        base.update(overrides)
        return base

    def _search(
        self, hits: list[dict[str, object]], **kwargs: object
    ) -> tuple[Candidate, ...]:
        from voxframe.sourcing.pixabay import PixabayAdapter

        adapter = PixabayAdapter("12345678-fake", **kwargs)  # type: ignore[arg-type]
        with mock.patch.object(adapter, "_get", return_value=({"hits": hits}, None)):
            return adapter.search(SearchRequest(query="winter", limit=5))

    def test_no_key_means_unavailable(self) -> None:
        from voxframe.sourcing.pixabay import PixabayAdapter

        assert not PixabayAdapter("").available()

    def test_contributor_profile_url_follows_their_format(self) -> None:
        """https://pixabay.com/users/{USERNAME}-{ID}/ (item 18)."""
        found = self._search([self._hit()])
        assert found[0].license.source_url == (
            "https://pixabay.com/users/SomeUser-998877/"
        )

    def test_license_is_named_exactly(self) -> None:
        """It is not a Creative Commons license and must not be called one."""
        from voxframe.sourcing.pixabay import PIXABAY_LICENSE

        found = self._search([self._hit()])
        assert found[0].license.name == PIXABAY_LICENSE

    def test_expiring_webformat_url_is_not_preferred(self) -> None:
        """webformatURL expires after 24h and must never become a path."""
        found = self._search([self._hit()])
        assert "expiring" not in found[0].url

    def test_original_dimensions_are_recorded(self) -> None:
        """Not the download dimensions: the matcher needs the real size."""
        found = self._search([self._hit()])
        assert found[0].width == 6000
        assert found[0].height == 4000

    def test_low_quality_is_dropped(self) -> None:
        found = self._search([self._hit(isLowQuality=True)])
        assert not found

    def test_ai_generated_is_dropped(self) -> None:
        """A synthesised image is a claim the user has not agreed to make."""
        found = self._search([self._hit(isAiGenerated=True)])
        assert not found

    def test_tags_are_split(self) -> None:
        found = self._search([self._hit()])
        assert "winter" in found[0].tags
        assert "snow" in found[0].tags

    def test_safesearch_is_always_set(self) -> None:
        from voxframe.sourcing.pixabay import PixabayAdapter

        adapter = PixabayAdapter("12345678-fake")
        parameters = adapter._common_parameters(SearchRequest(query="x"))
        assert parameters["safesearch"] == "true"

    def test_language_is_passed_when_supported(self) -> None:
        from voxframe.sourcing.pixabay import PixabayAdapter

        adapter = PixabayAdapter("12345678-fake")
        assert (
            adapter._common_parameters(SearchRequest(query="x", language="fr"))["lang"]
            == "fr"
        )

    def test_unsupported_language_is_omitted(self) -> None:
        """An unknown value would be rejected outright rather than ignored."""
        from voxframe.sourcing.pixabay import PixabayAdapter

        adapter = PixabayAdapter("12345678-fake")
        parameters = adapter._common_parameters(
            SearchRequest(query="x", language="yo")
        )
        assert "lang" not in parameters


class TestQueryTruncation:
    """Pixabay rejects a q longer than 100 characters (item 17)."""

    def test_a_long_query_is_shortened(self) -> None:
        from voxframe.sourcing.pixabay import MAX_QUERY_CHARS, truncate_query

        query = "word " * 40
        assert len(truncate_query(query)) <= MAX_QUERY_CHARS

    def test_it_cuts_at_a_word_boundary(self) -> None:
        """Cutting mid-word turns a real term into one matching nothing."""
        from voxframe.sourcing.pixabay import truncate_query

        query = "a" * 60 + " mountainside"
        assert not truncate_query(query).endswith("mountainsi")

    def test_a_short_query_is_untouched(self) -> None:
        from voxframe.sourcing.pixabay import truncate_query

        assert truncate_query("winter forest") == "winter forest"


class TestTextSubjectPenalty:
    """A photograph of printed text is worse than a gradient (D-082)."""

    def _asset(self, tags: tuple[str, ...]):  # type: ignore[no-untyped-def]
        from voxframe.models.asset import Asset, AssetKind, LicenseInfo

        return Asset(
            id="a" * 16,
            path=Path("photo.jpg"),
            kind=AssetKind.IMAGE,
            sha256="b" * 64,
            width=4000,
            height=3000,
            license=LicenseInfo(
                name="Pexels License", author="Someone", source="pexels"
            ),
            tags=tags,
        )

    def test_a_page_of_scripture_is_flagged(self) -> None:
        from voxframe.match.matcher import _is_text_subject

        assert _is_text_subject(
            self._asset(("biblical", "book", "deuteronomy", "page"))
        )

    def test_a_landscape_is_not_flagged(self) -> None:
        from voxframe.match.matcher import _is_text_subject

        assert not _is_text_subject(self._asset(("winter", "forest", "snow")))

    def test_typography_is_flagged(self) -> None:
        from voxframe.match.matcher import _is_text_subject

        assert _is_text_subject(self._asset(("typography", "poster")))
