"""Owned music survives temporary uploads, hiding, and future metadata changes."""
from __future__ import annotations

import shutil
import wave
from pathlib import Path

import pytest

from voxframe.music.library import MusicLibrary
from voxframe.render.ffpath import FFmpegError


def audio(path: Path) -> Path:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\x10\x00" * 16000)
    return path


@pytest.fixture
def library(tmp_path: Path) -> MusicLibrary:
    return MusicLibrary(tmp_path / "music")


def test_copy_duplicate_metadata_and_hide(library: MusicLibrary, tmp_path: Path) -> None:
    source = audio(tmp_path / "original.wav")
    original = source.read_bytes()
    track, duplicate = library.import_track(source, " Evening  glow ", "Composer, CC0", "calm")
    assert not duplicate
    assert track.title == "Evening glow" and track.seconds == 2
    assert "suffix" not in track.public()
    copy = library.file(track)
    source.unlink()
    assert copy.read_bytes() == original
    updated = library.update(track.id, "Glow", "New credit", "cinematic")
    assert updated.credit == "New credit" and track.credit == "Composer, CC0"
    library.hide(track.id)
    assert library.list()["total"] == 0
    with pytest.raises(KeyError):
        library.get(track.id)
    assert copy.read_bytes() == original  # Saved projects keep rendering.
    source.write_bytes(original)
    restored, duplicate = library.import_track(source, "Ignored", "Ignored", "other")
    assert duplicate and restored.title == "Glow" and restored.credit == "New credit"
    assert library.list()["total"] == 1


def test_literal_search_mood_and_paging(library: MusicLibrary, tmp_path: Path) -> None:
    track, _ = library.import_track(audio(tmp_path / "song.wav"), "100%_glow", "A singer", "calm")
    assert library.list("%_")["tracks"][0]["id"] == track.id
    assert library.list("singer", "calm")["total"] == 1
    assert library.list("singer", "energetic")["total"] == 0
    assert library.list("' OR 1=1 --")["total"] == 0
    assert library.list(offset=1)["tracks"] == []
    with pytest.raises(KeyError):
        library.get("../../etc/passwd")


@pytest.mark.parametrize("title,credit,mood", [(" ", "", "other"), ("ok", "x" * 301, "calm"),
                                              ("ok", "", "unknown")])
def test_invalid_details(library: MusicLibrary, title: str, credit: str, mood: str) -> None:
    with pytest.raises(ValueError):
        library.clean(title, credit, mood)


def test_invalid_audio_and_external_symlink_are_refused(library: MusicLibrary, tmp_path: Path) -> None:
    fake = tmp_path / "fake.mp3"
    fake.write_text("#EXTM3U\nhttps://example.com/private.mp3\n")
    with pytest.raises((FFmpegError, ValueError)):
        library.import_track(fake, "Fake", "", "other")
    assert library.list()["total"] == 0
    track, _ = library.import_track(audio(tmp_path / "ok.wav"), "Good", "", "other")
    owned = library.root / track.id
    shutil.rmtree(owned)
    outside = tmp_path / "outside"
    outside.mkdir()
    audio(outside / "audio.wav")
    owned.symlink_to(outside, target_is_directory=True)
    with pytest.raises(KeyError):
        library.file(track)
    with pytest.raises(ValueError, match="outside"):
        library.import_track(tmp_path / "ok.wav", "Good", "", "other")
