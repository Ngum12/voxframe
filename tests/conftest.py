"""Shared fixtures, including sample-audio discovery.

Sample audio is never committed (D-019). Public samples are fetched by
``scripts/fetch_samples.py``; private samples are the developer's own
recordings. Tests needing either skip cleanly when the files are absent, so a
fresh clone has a passing suite without downloading anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SAMPLES = REPO_ROOT / "samples" / "public"
PRIVATE_SAMPLES = REPO_ROOT / "samples" / "private"

AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus")

#: Files generated during development that must never stand in for a real
#: recording. Synthetic speech has regular pacing and no accent, so using one
#: as the "private sample" would report a meaningless result as a real one.
_GENERATED_STEMS = frozenset({"demo_tts", "nature_demo", "demo_fr", "probe_speech"})


def _find_sample(directory: Path, stem_prefix: str) -> Path | None:
    """First audio file whose stem starts with ``stem_prefix``.

    Prefers WAV, which the fetch script produces pre-trimmed at 16 kHz mono —
    the format Whisper consumes, so tests skip a conversion.
    """
    if not directory.is_dir():
        return None

    candidates = [
        path
        for path in sorted(directory.iterdir())
        if path.is_file()
        and path.suffix.lower() in AUDIO_SUFFIXES
        and path.stem.startswith(stem_prefix)
    ]
    if not candidates:
        return None

    wavs = [p for p in candidates if p.suffix.lower() == ".wav"]
    return wavs[0] if wavs else candidates[0]


@pytest.fixture(scope="session")
def english_sample() -> Path:
    """Public-domain English recording, or skip.

    "January" from *A Calendar of Sonnets* by Helen Hunt Jackson (LibriVox,
    public domain). See samples/PROVENANCE.md.
    """
    sample = _find_sample(PUBLIC_SAMPLES, "en_")
    if sample is None:
        pytest.skip("No English sample. Run: python scripts/fetch_samples.py")
    return sample


@pytest.fixture(scope="session")
def french_sample() -> Path:
    """Public-domain French recording, or skip.

    "La Cigale et la Fourmi" from *Fables de La Fontaine* (LibriVox, public
    domain). See samples/PROVENANCE.md.
    """
    sample = _find_sample(PUBLIC_SAMPLES, "fr_")
    if sample is None:
        pytest.skip("No French sample. Run: python scripts/fetch_samples.py")
    return sample


@pytest.fixture(scope="session")
def private_sample() -> Path:
    """The developer's own recording, for accent testing, or skip.

    Never committed. A file named ``accent_test.*`` is preferred; otherwise the
    first audio file in ``samples/private/`` is used. Results from private
    samples are reported separately in phase reports and never used as
    committed fixtures, because voice recordings are personal data.
    """
    sample = _find_sample(PRIVATE_SAMPLES, "accent_test")

    if sample is None and PRIVATE_SAMPLES.is_dir():
        candidates = [
            path
            for path in sorted(PRIVATE_SAMPLES.iterdir())
            if path.is_file()
            and path.suffix.lower() in AUDIO_SUFFIXES
            and path.stem.lower() not in _GENERATED_STEMS
        ]
        if candidates:
            # Most recent first: a file just dropped in is the one being
            # tested, and alphabetical order would pick whatever happens to
            # sort first.
            sample = max(candidates, key=lambda p: p.stat().st_mtime)

    if sample is None:
        pytest.skip("No private sample; drop an audio file in samples/private/")
    return sample


@pytest.fixture(scope="session")
def any_sample(english_sample: Path) -> Path:
    """Any available sample, for tests that do not care about language."""
    return english_sample


#: Settings fields that hold a real credential. `Settings` reads `.env` by
#: design, so any test constructing one inherits the developer's actual keys --
#: which then appear in assertion output and in CI logs. Found exactly that way
#: (D-118).
_SECRET_ENVIRONMENT = (
    "VOXFRAME_PEXELS_API_KEY",
    "VOXFRAME_PIXABAY_API_KEY",
    "VOXFRAME_UNSPLASH_ACCESS_KEY",
    "VOXFRAME_ANTHROPIC_API_KEY",
)


@pytest.fixture(autouse=True)
def isolate_user_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Keep real credentials and real user settings out of every test.

    Two separate leaks, both autouse because opting in per test is a rule
    someone will eventually forget:

    1. **`.env`.** An environment variable takes precedence over the file, so
       setting each secret to empty is enough to mask it -- no surgery on the
       settings class, and nothing to undo.

    2. **The user's own config directory.** A test that writes preferences
       would otherwise overwrite the real ones, and a test that reads them
       would pass or fail depending on whose machine it ran on.
    """
    for name in _SECRET_ENVIRONMENT:
        monkeypatch.setenv(name, "")

    monkeypatch.setenv(
        "VOXFRAME_CONFIG_DIR", str(tmp_path_factory.mktemp("voxframe-config"))
    )


@pytest.fixture
def tone_wav(tmp_path: Path) -> Path:
    """A synthetic tone.

    Useful for exercising duration and frame-grid arithmetic without needing a
    download. Useless for transcription: it contains no speech, which is why
    real recordings are used for anything involving words.
    """
    from voxframe.render.encode.probe import FFmpegNotFound, probe_capabilities
    from voxframe.render.ffpath import run_ffmpeg

    try:
        caps = probe_capabilities()
    except FFmpegNotFound:
        pytest.skip("FFmpeg not available")

    out = tmp_path / "tone.wav"
    run_ffmpeg(
        caps.ffmpeg_path,
        [
            "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-ar", "16000", "-ac", "1", "-y", str(out),
        ],
    )
    return out
