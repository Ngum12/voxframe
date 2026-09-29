"""Tests that embeddings from different models can never be mixed (D-039).

Different embedding models occupy unrelated coordinate systems. Comparing
vectors across them produces plausible-looking but meaningless rankings, which
is worse than an outright failure: the search returns results, they are simply
wrong, and nothing appears broken.

The guard therefore fails loudly and names the remedy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxframe.library import AssetLibrary, EmbeddingModelMismatch, LibraryError
from voxframe.models import Asset, AssetKind, LicenseInfo

MODEL_A = "ViT-B-32/laion2b_s34b_b79k"
MODEL_B = "xlm-roberta-base-ViT-B-32/laion5b"


def _asset(index: int) -> Asset:
    return Asset(
        id=f"a{index}",
        path=Path(f"img/a{index}.jpg"),
        kind=AssetKind.IMAGE,
        sha256=str(index).ljust(64, "0"),
        width=1920,
        height=1080,
        license=LicenseInfo(name="CC0-1.0", author="Someone", source="local"),
    )


@pytest.fixture
def library(tmp_path: Path) -> AssetLibrary:
    return AssetLibrary(tmp_path / "lib")


class TestModelRecording:
    def test_model_stored_with_embedding(self, library: AssetLibrary) -> None:
        library.add(_asset(1), embedding=[0.1] * 512, embed_model=MODEL_A)
        assert library.embedding_models() == {MODEL_A}

    def test_embedding_without_model_id_rejected(self, library: AssetLibrary) -> None:
        """A vector whose origin is unknown cannot be safely compared."""
        with pytest.raises(LibraryError, match="must be stored with the id"):
            library.add(_asset(1), embedding=[0.1] * 512)

    def test_whitespace_model_id_rejected(self, library: AssetLibrary) -> None:
        with pytest.raises(LibraryError, match="must be stored with the id"):
            library.add(_asset(1), embedding=[0.1] * 512, embed_model="   ")

    def test_asset_without_embedding_needs_no_model(self, library: AssetLibrary) -> None:
        """Metadata-only rows are legitimate; only vectors need provenance."""
        library.add(_asset(1))
        assert library.embedding_models() == frozenset()


class TestMismatchDetection:
    def test_matching_model_passes(self, library: AssetLibrary) -> None:
        library.add(_asset(1), embedding=[0.1] * 512, embed_model=MODEL_A)
        library.check_embedding_model(MODEL_A)  # must not raise

    def test_empty_library_passes(self, library: AssetLibrary) -> None:
        """Nothing to be inconsistent with."""
        library.check_embedding_model(MODEL_A)

    def test_different_model_rejected(self, library: AssetLibrary) -> None:
        library.add(_asset(1), embedding=[0.1] * 512, embed_model=MODEL_A)
        with pytest.raises(EmbeddingModelMismatch):
            library.check_embedding_model(MODEL_B)

    def test_mixed_library_rejected(self, library: AssetLibrary) -> None:
        """A partial re-embed leaves vectors that cannot be compared."""
        library.add(_asset(1), embedding=[0.1] * 512, embed_model=MODEL_A)
        library.add(_asset(2), embedding=[0.2] * 512, embed_model=MODEL_B)

        assert len(library.embedding_models()) == 2
        with pytest.raises(EmbeddingModelMismatch):
            library.check_embedding_model(MODEL_A)

    def test_error_names_both_models_and_the_remedy(self, library: AssetLibrary) -> None:
        library.add(_asset(1), embedding=[0.1] * 512, embed_model=MODEL_A)

        with pytest.raises(EmbeddingModelMismatch) as exc:
            library.check_embedding_model(MODEL_B)

        message = str(exc.value)
        assert MODEL_A in message
        assert MODEL_B in message
        assert "voxframe reembed" in message

    def test_exception_carries_structured_detail(self, library: AssetLibrary) -> None:
        """Callers may want to act on this, not only print it."""
        library.add(_asset(1), embedding=[0.1] * 512, embed_model=MODEL_A)

        with pytest.raises(EmbeddingModelMismatch) as exc:
            library.check_embedding_model(MODEL_B)

        assert exc.value.configured == MODEL_B
        assert exc.value.stored == {MODEL_A}


class TestSearchGuard:
    def test_search_refuses_on_mismatch(self, library: AssetLibrary) -> None:
        """The failure that matters: silent wrong results become a loud error."""
        if not library.supports_search:
            pytest.skip("sqlite-vec unavailable")

        library.add(_asset(1), embedding=[0.1] * 512, embed_model=MODEL_A)

        with pytest.raises(EmbeddingModelMismatch):
            library.search([0.1] * 512, embed_model=MODEL_B)

    def test_search_allowed_when_models_agree(self, library: AssetLibrary) -> None:
        if not library.supports_search:
            pytest.skip("sqlite-vec unavailable")

        library.add(_asset(1), embedding=[0.1] * 512, embed_model=MODEL_A)
        results = library.search([0.1] * 512, embed_model=MODEL_A)
        assert len(results) == 1

    def test_unspecified_model_skips_the_check(self, library: AssetLibrary) -> None:
        """Internal callers that have already checked need not check twice."""
        if not library.supports_search:
            pytest.skip("sqlite-vec unavailable")

        library.add(_asset(1), embedding=[0.1] * 512, embed_model=MODEL_A)
        assert library.search([0.1] * 512) is not None


class TestReembedTargeting:
    def test_lists_only_stale_assets(self, library: AssetLibrary) -> None:
        library.add(_asset(1), embedding=[0.1] * 512, embed_model=MODEL_A)
        library.add(_asset(2), embedding=[0.2] * 512, embed_model=MODEL_B)

        stale = library.assets_needing_reembed(MODEL_A)
        assert [asset.id for asset in stale] == ["a2"]

    def test_nothing_stale_when_models_agree(self, library: AssetLibrary) -> None:
        library.add(_asset(1), embedding=[0.1] * 512, embed_model=MODEL_A)
        assert library.assets_needing_reembed(MODEL_A) == ()

    def test_unembedded_assets_count_as_stale(self, library: AssetLibrary) -> None:
        """An asset with no vector cannot be searched, so it needs embedding."""
        library.add(_asset(1))
        assert len(library.assets_needing_reembed(MODEL_A)) == 1
