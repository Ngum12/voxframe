"""faster-whisper can read audio with the PyAV that is installed (D-190).

PyAV 19 removed an option faster-whisper 1.2.1 passes when it opens a file,
so a fresh install could not transcribe anything. The macOS release build
found it, not the suite, because this machine still had an older PyAV.
This reads the sonnet through faster-whisper's own decoder, so the pair is
checked wherever the suite runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("faster_whisper", reason="transcribe extra not installed")

REPO_ROOT = Path(__file__).resolve().parents[2]
SONNET = REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav"


def test_faster_whisper_decodes_with_the_installed_pyav() -> None:
    from faster_whisper.audio import decode_audio

    audio = decode_audio(str(SONNET), sampling_rate=16000)

    assert len(audio) == pytest.approx(45 * 16000, rel=0.01)


def test_pyav_is_capped_below_the_version_that_broke_it() -> None:
    import av

    assert int(av.__version__.split(".")[0]) < 19
