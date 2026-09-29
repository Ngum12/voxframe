"""A music bed from the browser (D-148).

Uploaded like the recording and named by its upload id, so the page never
names a path. Voxframe never supplies music of its own, and a track with no
credit is recorded as supplied by the person, never given an invented one
(D-091).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="web extra not installed")
from fastapi import HTTPException

from voxframe.api.app import ApiContext, RenderRequest, _job_options
from voxframe.api.security import SessionToken
from voxframe.config.settings import Settings
from voxframe.jobs.store import JobStore


@pytest.fixture
def context(tmp_path: Path) -> ApiContext:
    root = tmp_path / "web"
    return ApiContext(
        settings=Settings(
            library_path=tmp_path / "library", cache_path=tmp_path / "cache",
            output_path=tmp_path / "out",
        ),
        store=JobStore(root, reap_interval=None),
        token=SessionToken("t"),
        allowed_paths=(root.resolve(),),
    )


def _uploaded(context: ApiContext, upload_id: str, name: str) -> Path:
    directory = context.store.root / "uploads" / upload_id
    directory.mkdir(parents=True)
    path = directory / name
    path.write_bytes(b"audio")
    return path


def test_the_music_upload_becomes_the_bed(context: ApiContext) -> None:
    voice = _uploaded(context, "voice", "source.wav")
    music = _uploaded(context, "music", "source.mp3")

    options = _job_options(
        RenderRequest(
            upload_id="voice", music_upload_id="music",
            music_credit="  Calm  Piano by   Someone (CC BY 4.0) ",
        ),
        voice,
        context,
    )

    assert options.music is not None
    assert options.music.path == music
    assert options.music.credit == "Calm Piano by Someone (CC BY 4.0)"


def test_no_credit_is_recorded_honestly(context: ApiContext) -> None:
    voice = _uploaded(context, "voice", "source.wav")
    _uploaded(context, "music", "source.mp3")

    options = _job_options(
        RenderRequest(upload_id="voice", music_upload_id="music"), voice, context
    )

    assert options.music is not None
    assert options.music.attribution().endswith("(supplied by the user)")


def test_no_music_means_none(context: ApiContext) -> None:
    voice = _uploaded(context, "voice", "source.wav")

    assert _job_options(RenderRequest(upload_id="voice"), voice, context).music is None


@pytest.mark.parametrize("bad", ["../voice", "missing"])
def test_a_bad_music_id_is_refused(context: ApiContext, bad: str) -> None:
    voice = _uploaded(context, "voice", "source.wav")

    with pytest.raises(HTTPException):
        _job_options(RenderRequest(upload_id="voice", music_upload_id=bad), voice, context)


def test_an_uncredited_track_is_named_as_the_person_named_it(context: ApiContext) -> None:
    """Stored as source.mp3; credited by the name it was uploaded under."""
    voice = _uploaded(context, "voice", "source.wav")
    music = _uploaded(context, "music", "source.mp3")
    (music.parent / "name.txt").write_text("Evening Piano.mp3", encoding="utf-8")

    options = _job_options(
        RenderRequest(upload_id="voice", music_upload_id="music"), voice, context
    )

    assert options.music is not None
    assert options.music.attribution() == "Music: Evening Piano.mp3 (supplied by the user)"


def test_a_hostile_name_stays_a_name(context: ApiContext) -> None:
    voice = _uploaded(context, "voice", "source.wav")
    music = _uploaded(context, "music", "source.mp3")
    (music.parent / "name.txt").write_text("../../etc/passwd.mp3", encoding="utf-8")

    options = _job_options(
        RenderRequest(upload_id="voice", music_upload_id="music"), voice, context
    )

    assert options.music is not None
    assert options.music.path.parent == music.parent / "named"
