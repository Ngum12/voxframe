"""Sample fetching must verify what it downloads.

The release path exists so a fresh clone gets known bytes from a host that is
not blocked where archive.org is (D-064). That is only worth anything if a
wrong or corrupted download fails loudly, so the checksum behaviour is tested
directly rather than assumed.

Nothing here touches the network: ``_fetch_bytes`` is the single seam every
download goes through, and it is patched.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import fetch_samples as fs  # noqa: E402


@pytest.fixture
def sample() -> fs.Sample:
    return fs.SAMPLES[0]


class TestReleaseDownload:
    def test_no_recorded_hash_skips_without_downloading(
        self, sample: fs.Sample, tmp_path: Path
    ) -> None:
        """An unverifiable asset is not worth fetching.

        Downloading unverified bytes from the release would give up the only
        property the release path has over archive.org.
        """
        unhashed = dataclasses.replace(sample, excerpt_sha256="")

        with mock.patch.object(
            fs, "_fetch_bytes", side_effect=AssertionError("must not download")
        ):
            assert fs.fetch_from_release(unhashed, tmp_path / "out.wav") is False

    def test_matching_hash_is_written(
        self, sample: fs.Sample, tmp_path: Path
    ) -> None:
        payload = b"pretend audio"
        expected = dataclasses.replace(
            sample, excerpt_sha256=fs._sha256_bytes(payload)
        )
        destination = tmp_path / "out.wav"

        with mock.patch.object(fs, "_fetch_bytes", return_value=payload):
            assert fs.fetch_from_release(expected, destination) is True

        assert destination.read_bytes() == payload

    def test_wrong_bytes_raise_and_write_nothing(
        self, sample: fs.Sample, tmp_path: Path
    ) -> None:
        """A mismatch must not fall through to another source.

        Falling back would hide exactly the problem the hash exists to catch:
        a substituted or corrupted asset.
        """
        wrong = dataclasses.replace(sample, excerpt_sha256="0" * 64)
        destination = tmp_path / "out.wav"

        with (
            mock.patch.object(fs, "_fetch_bytes", return_value=b"anything"),
            pytest.raises(fs.ChecksumMismatch),
        ):
            fs.fetch_from_release(wrong, destination)

        assert not destination.exists()

    def test_mismatch_message_names_both_hashes(
        self, sample: fs.Sample, tmp_path: Path
    ) -> None:
        """The message has to be enough to diagnose it without re-running."""
        wrong = dataclasses.replace(sample, excerpt_sha256="0" * 64)

        with (
            mock.patch.object(fs, "_fetch_bytes", return_value=b"anything"),
            pytest.raises(fs.ChecksumMismatch) as caught,
        ):
            fs.fetch_from_release(wrong, tmp_path / "out.wav")

        message = str(caught.value)
        assert "0" * 64 in message
        assert fs._sha256_bytes(b"anything") in message
        assert sample.release_url in message

    def test_unreachable_release_falls_back(
        self, sample: fs.Sample, tmp_path: Path
    ) -> None:
        """A network failure is different from wrong bytes: retry elsewhere."""
        hashed = dataclasses.replace(sample, excerpt_sha256="a" * 64)

        with mock.patch.object(
            fs, "_fetch_bytes", side_effect=RuntimeError("blocked")
        ):
            assert fs.fetch_from_release(hashed, tmp_path / "out.wav") is False


class TestArchiveFallback:
    def test_cdn_alternatives_are_derived_from_the_url(
        self, sample: fs.Sample
    ) -> None:
        alternatives = fs._cdn_alternatives(sample.url)

        assert len(alternatives) == len(fs.ARCHIVE_CDN_NODES)
        assert all(url.endswith("calendar_sonnets_01_jackson_64kb.mp3") for url in alternatives)
        assert all("/0/items/" in url for url in alternatives)

    def test_a_url_without_download_has_no_alternatives(self) -> None:
        assert fs._cdn_alternatives("https://example.invalid/file.mp3") == []

    def test_next_node_is_tried_when_the_first_host_fails(
        self, sample: fs.Sample, tmp_path: Path
    ) -> None:
        attempts: list[str] = []

        def flaky(url: str, timeout: int = 120) -> bytes:
            attempts.append(url)
            if len(attempts) == 1:
                raise RuntimeError("blocked at TLS")
            return b"full recording"

        unhashed = dataclasses.replace(sample, source_sha256="")
        destination = tmp_path / "out.mp3"

        with mock.patch.object(fs, "_fetch_bytes", side_effect=flaky):
            fs.fetch_from_archive(unhashed, destination)

        assert len(attempts) == 2
        assert destination.read_bytes() == b"full recording"

    def test_every_host_failing_raises_with_instructions(
        self, sample: fs.Sample, tmp_path: Path
    ) -> None:
        destination = tmp_path / "out.mp3"

        with (
            mock.patch.object(fs, "_fetch_bytes", side_effect=RuntimeError("blocked")),
            pytest.raises(RuntimeError) as caught,
        ):
            fs.fetch_from_archive(sample, destination)

        message = str(caught.value)
        # A user on a blocking network needs to know what to do next.
        assert str(destination) in message
        assert "browser" in message
        assert not destination.exists()

    def test_wrong_source_bytes_raise(
        self, sample: fs.Sample, tmp_path: Path
    ) -> None:
        wrong = dataclasses.replace(sample, source_sha256="0" * 64)

        with (
            mock.patch.object(fs, "_fetch_bytes", return_value=b"not the recording"),
            pytest.raises(fs.ChecksumMismatch),
        ):
            fs.fetch_from_archive(wrong, tmp_path / "out.mp3")


class TestRecordedHashes:
    """The hashes committed in the script must be real, not placeholders."""

    @pytest.mark.parametrize("sample", fs.SAMPLES, ids=lambda s: s.name)
    def test_source_hash_is_recorded_and_well_formed(
        self, sample: fs.Sample
    ) -> None:
        assert len(sample.source_sha256) == 64
        assert all(c in "0123456789abcdef" for c in sample.source_sha256)

    @pytest.mark.parametrize("sample", fs.SAMPLES, ids=lambda s: s.name)
    def test_excerpt_hash_is_either_empty_or_well_formed(
        self, sample: fs.Sample
    ) -> None:
        """Empty means "release not cut yet", which is honest. A wrong-length
        value means someone typed a placeholder, which would silently disable
        verification."""
        if sample.excerpt_sha256:
            assert len(sample.excerpt_sha256) == 64
            assert all(c in "0123456789abcdef" for c in sample.excerpt_sha256)

    @pytest.mark.parametrize("sample", fs.SAMPLES, ids=lambda s: s.name)
    def test_release_url_points_at_the_pinned_tag(self, sample: fs.Sample) -> None:
        """Pinned, not "latest": a fresh clone must get the hashed bytes."""
        assert fs.SAMPLES_RELEASE_TAG in sample.release_url
        assert sample.release_url.endswith(sample.trimmed_filename)
        assert "/latest/" not in sample.release_url
