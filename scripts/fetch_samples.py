#!/usr/bin/env python3
"""Download the public-domain sample recordings used by tests and demos.

Run once after cloning::

    python scripts/fetch_samples.py

Downloads two short LibriVox recordings (English and French) into
``samples/public/`` and writes ``samples/PROVENANCE.md`` recording the full
attribution for each, on the same terms as any other asset in the library
(DECISIONS.md D-012).

Nothing here is committed to the repository: the files are gitignored, and
tests that need them skip cleanly when they are absent. Contributors who skip
this step still get a passing suite, minus the real-audio checks.

Where the audio comes from
--------------------------
Two sources, tried in order:

1. **A GitHub release asset** holding the 45-second excerpt, already trimmed to
   16 kHz mono. This is the default because it is one small file per sample,
   from a host that is not blocked on the networks where archive.org is, and it
   needs no ffmpeg. Every byte is checked against a SHA-256 recorded in this
   file, so a corrupted or substituted download fails loudly instead of
   producing mysterious transcription results.
2. **archive.org**, the original LibriVox source, as a fallback. Used when the
   release asset is unreachable or when ``--source archive`` is passed. The full
   recording is downloaded and trimmed locally, which needs ffmpeg.

The release asset is a redistribution, not a new work: LibriVox recordings are
public domain, so republishing a 45-second excerpt is permitted. ``PROVENANCE.md``
records the original archive.org URL either way, so the chain back to the source
is never lost.

Why the files are not vendored
------------------------------
Audio is large and binary, and the brief forbids committing media. Fetching on
demand also keeps provenance honest: the URL in this file is the actual source,
not a copy whose origin has been forgotten.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIR = REPO_ROOT / "samples" / "public"
PROVENANCE = REPO_ROOT / "samples" / "PROVENANCE.md"

USER_AGENT = "voxframe-sample-fetch/0.1 (+https://github.com/Ngum12/voxframe)"

#: Archive.org CDN nodes, tried in order when the main host is unreachable.
#:
#: Some networks block `archive.org` and `www.archive.org` at the TLS layer
#: while leaving the storage nodes reachable — observed on the development
#: machine, where the main host fails with curl exit 35 and these return data
#: normally. The metadata API that would name the correct node lives on the
#: blocked host, so the nodes are tried in turn instead (D-058).
#: The release that holds the pre-trimmed excerpts.
#:
#: Pinned to a tag rather than "latest" so a fresh clone gets the same bytes as
#: the SHA-256 values below, whatever is released afterwards.
SAMPLES_RELEASE_TAG = "samples-v1"
SAMPLES_RELEASE_BASE = (
    "https://github.com/Ngum12/voxframe/releases/download/" + SAMPLES_RELEASE_TAG
)


class ChecksumMismatch(RuntimeError):
    """Raised when a downloaded file does not match its recorded hash.

    Separate from a download failure because the response is different: a
    network error is worth retrying elsewhere, whereas wrong bytes from a
    reachable host mean the recorded hash or the asset is wrong, and silently
    falling back would hide that.
    """


ARCHIVE_CDN_NODES: tuple[str, ...] = (
    "ia801604.us.archive.org",
    "ia601504.us.archive.org",
    "ia801504.us.archive.org",
    "ia902606.us.archive.org",
    "ia800204.us.archive.org",
)


@dataclass(frozen=True, slots=True)
class Sample:
    """A public-domain recording, with everything needed to credit it."""

    name: str
    language: str
    url: str
    title: str
    work: str
    author: str
    author_dates: str
    reader: str
    source_page: str
    librivox_id: str
    duration_hint: str
    #: Seconds to keep from the start. The brief asks for 30-60s samples;
    #: these recordings run slightly longer.
    #:
    #: Zero means keep the whole recording, which is what the long-audio
    #: sample needs: chapters, caching and memory at scale cannot be tested on
    #: a 45-second excerpt (D-103).
    trim_seconds: float = 45.0

    #: SHA-256 of the trimmed excerpt as published in the release. Empty until
    #: the release is cut; an empty value means "cannot verify", and the release
    #: path is skipped rather than trusted (see ``_verified_release_download``).
    excerpt_sha256: str = ""

    #: SHA-256 of the full archive.org mp3, for the fallback path. Empty for the
    #: same reason: recording a guess would be worse than recording nothing.
    source_sha256: str = ""
    license_name: str = "Public domain"
    license_note: str = field(
        default=(
            "LibriVox recordings are released into the public domain. "
            "The underlying text is public domain by age."
        )
    )

    @property
    def filename(self) -> str:
        return f"{self.name}.mp3"

    @property
    def is_full_length(self) -> bool:
        """Whether this sample is kept whole rather than excerpted."""
        return self.trim_seconds <= 0

    @property
    def trimmed_filename(self) -> str:
        if self.is_full_length:
            return f"{self.name}_full.wav"
        return f"{self.name}_{int(self.trim_seconds)}s.wav"

    @property
    def release_url(self) -> str:
        """Where the pre-trimmed excerpt lives in the GitHub release."""
        return f"{SAMPLES_RELEASE_BASE}/{self.trimmed_filename}"


SAMPLES: tuple[Sample, ...] = (
    Sample(
        name="en_sonnet_january",
        language="en",
        url=(
            "https://www.archive.org/download/calendar_of_sonnets_librivox/"
            "calendar_sonnets_01_jackson_64kb.mp3"
        ),
        title="January",
        work="A Calendar of Sonnets",
        author="Helen Hunt Jackson",
        author_dates="1830-1885",
        reader="LibriVox volunteers",
        source_page="https://librivox.org/a-calendar-of-sonnets-by-helen-hunt-jackson/",
        librivox_id="139",
        duration_hint="00:01:20",
        # Verified against the file this script fetched on 2026-09-26.
        source_sha256=(
            "4ccee95596950ca00bdd238baed1eb89b3892f46d582bb27d28d722656ff1d7a"
        ),
        # The excerpt is committed (D-151), so this is the hash of the file in
        # the repository, verified 2026-09-28.
        excerpt_sha256=(
            "cd927d85f09c3bde1422529afd66488302fea67d264a784f5453342e7fad4b2d"
        ),
    ),
    Sample(
        name="fr_fable_cigale",
        language="fr",
        url=(
            "https://www.archive.org/download/fables_lafontaine_01_librivox/"
            "fables_01_01_lafontaine_64kb.mp3"
        ),
        title="La Cigale et la Fourmi",
        work="Fables de La Fontaine, livre 01",
        author="Jean de La Fontaine",
        author_dates="1621-1695",
        reader="LibriVox volunteers",
        source_page="https://librivox.org/fables-de-la-fontaine-livre-01-by-jean-de-la-fontaine/",
        librivox_id="80",
        duration_hint="00:01:11",
        source_sha256=(
            "f7499bc4736b5e75d56770ac77fad6e956efe14dcd4d4bf263f1e14b4b1e8f6b"
        ),
        excerpt_sha256="",
    ),
    Sample(
        name="en_golden_river_chapter",
        language="en",
        url=(
            "https://www.archive.org/download/how_to_tell_stories_1001_librivox/"
            "howtotellstoriestochildren_03b_bryant_64kb.mp3"
        ),
        title="3b - Example Story: The Golden River",
        work="How to Tell Stories to Children",
        author="Sara Cone Bryant",
        author_dates="1873-",
        reader="Sean McGaughey",
        source_page=(
            "https://librivox.org/how-to-tell-stories-to-children-and-some-"
            "stories-to-tell-by-sara-cone-bryant/"
        ),
        librivox_id="2000",
        duration_hint="00:14:40",
        # Verified against the file this script fetched on 2026-09-27.
        source_sha256=(
            "c0de88da5ffe68af33b637d736f553193713ff7eefbe2c0107cf798726a560d0"
        ),
        # Kept whole. A 45-second excerpt cannot exercise chapter detection
        # (which needs three minutes), segment caching at scale, or memory
        # across hundreds of scenes (D-103).
        trim_seconds=0.0,
    ),
)


def _cdn_alternatives(url: str) -> list[str]:
    """Storage-node URLs equivalent to an archive.org download URL.

    Some networks block ``archive.org`` and ``www.archive.org`` at the TLS layer
    while leaving the storage nodes reachable. The metadata API that would name
    the correct node lives on the blocked host, so the known nodes are tried in
    turn instead (D-058).
    """
    marker = "/download/"
    if marker not in url:
        return []

    item_and_file = url.split(marker, 1)[1]
    return [
        f"https://{node}/0/items/{item_and_file}"
        for node in ARCHIVE_CDN_NODES
    ]


def _fetch_bytes(url: str, timeout: int = 120) -> bytes:
    """Fetch a URL, raising ``RuntimeError`` with the reason on failure."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data: bytes = response.read()
            return data
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_from_release(sample: Sample, destination: Path) -> bool:
    """Download the pre-trimmed excerpt from the GitHub release.

    The bytes are verified against ``sample.excerpt_sha256`` before being
    written, so a corrupted or substituted download never reaches disk. A
    mismatch raises rather than falling through to archive.org: wrong bytes from
    a reachable host mean the recorded hash or the published asset is wrong, and
    quietly using a different source would hide that.

    Returns:
        ``True`` if the excerpt was downloaded and verified, ``False`` if the
        release has no recorded hash yet or could not be reached.

    Raises:
        ChecksumMismatch: If the download succeeded but the bytes are wrong.
    """
    if not sample.excerpt_sha256:
        # No hash recorded means the release has not been cut for this sample.
        # Downloading unverified bytes would defeat the point of the release.
        return False

    try:
        data = _fetch_bytes(sample.release_url)
    except RuntimeError as exc:
        print(f"  . release unavailable ({exc}); falling back to archive.org")
        return False

    actual = _sha256_bytes(data)
    if actual != sample.excerpt_sha256:
        raise ChecksumMismatch(
            f"{sample.trimmed_filename} does not match its recorded hash.\n"
            f"  expected: {sample.excerpt_sha256}\n"
            f"  actual:   {actual}\n"
            f"  url:      {sample.release_url}\n\n"
            "This means the release asset changed or the download was corrupted.\n"
            "Re-run to retry, or use --source archive to fetch from the original."
        )

    destination.write_bytes(data)
    return True


def fetch_from_archive(sample: Sample, destination: Path) -> None:
    """Download the full recording from archive.org, trying CDN nodes.

    Raises:
        RuntimeError: If every host fails, with instructions for fetching by
            hand.
    """
    attempts = [sample.url, *_cdn_alternatives(sample.url)]
    failures: list[str] = []

    for attempt, url in enumerate(attempts):
        try:
            data = _fetch_bytes(url)
        except RuntimeError as exc:
            failures.append(f"  {url}\n    {exc}")
            continue

        if sample.source_sha256:
            actual = _sha256_bytes(data)
            if actual != sample.source_sha256:
                raise ChecksumMismatch(
                    f"{sample.filename} from {url} does not match its recorded "
                    f"hash.\n  expected: {sample.source_sha256}\n"
                    f"  actual:   {actual}"
                )

        destination.write_bytes(data)
        if attempt:
            print(f"  . main host unreachable; used {url.split('/')[2]}")
        return

    raise RuntimeError(
        f"Could not download {sample.name} from any host.\n"
        + "\n".join(failures)
        + "\n\nIf archive.org is blocked on your network, download this file in a\n"
        f"browser and save it as:\n  {destination}\n"
        "then re-run this script to trim it and write provenance."
    )


def _trim(source: Path, destination: Path, seconds: float) -> bool:
    """Convert to 16 kHz mono WAV, trimming when ``seconds`` is positive.

    16 kHz mono is what Whisper consumes, so doing it once here keeps the
    transcription path simple and makes test runs faster.

    ``seconds <= 0`` keeps the whole recording, which the long-audio sample
    needs (D-103).
    """
    ffmpeg = "ffmpeg"
    try:
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-i", str(source),
                *(["-t", str(seconds)] if seconds > 0 else []),
                "-ar", "16000", "-ac", "1",
                "-y", str(destination),
            ],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        print("  ! ffmpeg not found; keeping the full-length mp3 only", file=sys.stderr)
        return False
    except subprocess.CalledProcessError as exc:
        print(f"  ! trim failed: {exc.stderr.decode(errors='replace')[:200]}", file=sys.stderr)
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def write_provenance(records: list[dict[str, str]]) -> None:
    """Record attribution for every fetched sample.

    Mirrors what the library does for any ingested asset: source, author,
    license and URL are mandatory, so credits are a projection of recorded
    facts rather than separate bookkeeping.
    """
    lines = [
        "# Sample provenance",
        "",
        "Generated by `scripts/fetch_samples.py`. Do not edit by hand.",
        "",
        "Every sample below is public domain and may be redistributed freely.",
        "The audio files themselves are gitignored; this file records where they",
        "came from so the attribution survives without committing the media.",
        "",
    ]

    for record in records:
        lines += [
            f"## {record['name']}",
            "",
            f"- **Title:** {record['title']}",
            f"- **Work:** {record['work']}",
            f"- **Author:** {record['author']} ({record['author_dates']})",
            f"- **Reader:** {record['reader']}",
            f"- **Language:** {record['language']}",
            f"- **License:** {record['license_name']}",
            f"- **Source:** {record['source_page']}",
            f"- **File URL:** {record['url']}",
            f"- **LibriVox ID:** {record['librivox_id']}",
            f"- **Excerpt URL:** {record['excerpt_url']}",
            f"- **Excerpt SHA-256:** `{record['excerpt_sha256'] or 'not fetched'}`",
            f"- **Full-file SHA-256:** `{record['sha256'] or 'not fetched'}`",
            f"- **Note:** {record['license_note']}",
            "",
        ]

    PROVENANCE.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="re-download files that already exist"
    )
    parser.add_argument(
        "--source",
        choices=("auto", "release", "archive"),
        default="auto",
        help=(
            "where to fetch from: the GitHub release excerpt (default, "
            "verified by SHA-256), archive.org, or auto to try the release "
            "and fall back"
        ),
    )
    args = parser.parse_args()

    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, str]] = []
    failures = 0

    for sample in SAMPLES:
        source = PUBLIC_DIR / sample.filename
        trimmed = PUBLIC_DIR / sample.trimmed_filename

        print(f"{sample.name} ({sample.language}, {sample.duration_hint})")

        if trimmed.exists() and not args.force:
            print(f"  = already present: {trimmed.name}")
            records.append(_record(sample, source, trimmed))
            continue

        got_excerpt = False

        # --- the release excerpt: one small verified file, no ffmpeg needed ---
        if args.source in {"auto", "release"}:
            try:
                got_excerpt = fetch_from_release(sample, trimmed)
            except ChecksumMismatch as exc:
                # Never fall back on a hash mismatch: that would hide the very
                # problem the hash exists to catch.
                print(f"\n{exc}\n", file=sys.stderr)
                failures += 1
                continue

            if got_excerpt:
                size = trimmed.stat().st_size // 1024
                print(f"  + {trimmed.name} ({size} KB, sha256 verified)")

        # --- archive.org: the original source, trimmed locally ---
        if not got_excerpt:
            if args.source == "release":
                print("  ! no verified release asset for this sample", file=sys.stderr)
                failures += 1
                continue

            if source.exists() and not args.force:
                print(f"  = already present: {source.name}")
            else:
                print(f"  > {sample.url}")
                try:
                    fetch_from_archive(sample, source)
                    print(f"  + {source.name} ({source.stat().st_size // 1024} KB)")
                except (RuntimeError, ChecksumMismatch) as exc:
                    print(f"\n{exc}\n", file=sys.stderr)
                    failures += 1
                    continue

            if _trim(source, trimmed, sample.trim_seconds):
                length = (
                    "full length"
                    if sample.is_full_length
                    else f"{sample.trim_seconds:.0f}s"
                )
                print(f"  + {trimmed.name} (16 kHz mono, {length})")

        records.append(_record(sample, source, trimmed))

    if records:
        write_provenance(records)
        print(f"\nWrote {PROVENANCE.relative_to(REPO_ROOT)}")

    if failures:
        print(f"\n{failures} sample(s) could not be downloaded.", file=sys.stderr)
        print(
            "Tests needing them will skip; the rest of the suite is unaffected.",
            file=sys.stderr,
        )
        return 1

    print(f"\n{len(records)} sample(s) ready in {PUBLIC_DIR.relative_to(REPO_ROOT)}")
    return 0


def _record(sample: Sample, source: Path, trimmed: Path) -> dict[str, str]:
    """Provenance for one sample, whichever source it came from.

    The archive.org URL is recorded either way: the release asset is a
    redistribution of it, so that is where the chain back to the original
    leads.
    """
    return {
        "name": sample.name,
        "title": sample.title,
        "work": sample.work,
        "author": sample.author,
        "author_dates": sample.author_dates,
        "reader": sample.reader,
        "language": sample.language,
        "license_name": sample.license_name,
        "license_note": sample.license_note,
        "source_page": sample.source_page,
        "url": sample.url,
        "librivox_id": sample.librivox_id,
        "sha256": _sha256(source) if source.exists() else "",
        "excerpt_sha256": _sha256(trimmed) if trimmed.exists() else "",
        "excerpt_url": sample.release_url,
    }


if __name__ == "__main__":
    raise SystemExit(main())
