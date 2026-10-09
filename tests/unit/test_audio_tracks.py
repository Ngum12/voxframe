"""Only finished, sandboxed voice/music stems are available as private downloads."""

from fastapi.testclient import TestClient

from tests.unit import test_api_editing as fixtures
from voxframe.render.audio.mixdown import Stems
from voxframe.render.compose.from_plan import stems_path

client = fixtures.client
context = fixtures.context
finished_job = fixtures.finished_job
library = fixtures.library


def stems(context, job_id):
    video = context.store.artifact_path(job_id, "video")
    voice, music = video.parent / "voice.wav", video.parent / "music.wav"
    voice.write_bytes(b"voice wave")
    music.write_bytes(b"music wave")
    saved = Stems(voice=voice, music=music, spans=(), landing=5, video_end=7, fps=30)
    saved.save(stems_path(video))
    return saved, stems_path(video)


def test_tracks_have_no_private_paths_and_download_independently(client, context, finished_job):
    stems(context, finished_job)
    url = f"/api/jobs/{finished_job}/mix/tracks"
    response = client.get(url)
    assert response.status_code == 200
    assert [track["id"] for track in response.json()["tracks"]] == ["voice", "music"]
    assert str(context.store.root) not in response.text
    for kind, content in (("voice", b"voice wave"), ("music", b"music wave")):
        downloaded = client.get(url + f"/{kind}")
        assert downloaded.status_code == 200
        assert downloaded.content == content
        assert downloaded.headers["content-type"] == "audio/wav"
        partial = client.get(url + f"/{kind}", headers={"range": "bytes=0-3"})
        assert partial.status_code == 206 and partial.content == content[:4]
    context.store.set_pending(context.store.get(finished_job), 1)
    assert client.get(url).json()["pending_edits"] == 1
    assert client.get(url + "/other").status_code == 422
    with TestClient(client.app, base_url=fixtures.LOOPBACK) as anonymous:
        assert anonymous.get(url).status_code == 401
        assert anonymous.get(url + "/music").status_code == 401


def test_missing_music_manifest_and_stems_have_useful_responses(client, context, finished_job):
    url = f"/api/jobs/{finished_job}/mix/tracks"
    assert client.get(url).json()["tracks"] == []
    saved, manifest = stems(context, finished_job)
    from dataclasses import replace
    replace(saved, music=None).save(manifest)
    assert len(client.get(url).json()["tracks"]) == 1
    assert client.get(url + "/music").status_code == 404
    saved.voice.unlink()
    assert client.get(url).status_code == 409


def test_forged_paths_and_active_renders_are_rejected(client, context, finished_job, tmp_path):
    saved, manifest = stems(context, finished_job)
    from dataclasses import replace
    outside = tmp_path / "private.wav"
    outside.write_bytes(b"private")
    replace(saved, voice=outside).save(manifest)
    response = client.get(f"/api/jobs/{finished_job}/mix/tracks/voice")
    assert response.status_code in {403, 422}
    assert b"private" != response.content
    saved.save(manifest)
    from voxframe.jobs.store import JobState
    context.store.get(finished_job).state = JobState.RUNNING
    assert client.get(f"/api/jobs/{finished_job}/mix/tracks").status_code == 409


def test_renderer_sound_cache_is_allowed_but_other_cache_files_are_not(client, context, finished_job):
    from dataclasses import replace
    saved, manifest = stems(context, finished_job)
    directory = context.settings.cache_path / "sound"
    directory.mkdir(parents=True)
    cached = directory / "voice-cached.wav"
    cached.write_bytes(b"generated voice")
    replace(saved, voice=cached).save(manifest)
    url = f"/api/jobs/{finished_job}/mix/tracks/voice"
    assert client.get(url).content == b"generated voice"
    private = context.settings.cache_path / "private.wav"
    private.write_bytes(b"private cache data")
    replace(saved, voice=private).save(manifest)
    assert client.get(url).status_code == 403


def test_generated_score_flac_is_converted_to_a_real_wav(client, context, finished_job):
    import io
    from dataclasses import replace

    import numpy as np
    import soundfile as sf
    saved, manifest = stems(context, finished_job)
    score = saved.music.with_suffix(".flac")
    sf.write(score, .1 * np.sin(np.arange(4800) * 2 * np.pi * 220 / 48000), 48000)
    replace(saved, music=score).save(manifest)
    response = client.get(f"/api/jobs/{finished_job}/mix/tracks/music")
    assert response.status_code == 200
    assert response.content[:4] == b"RIFF"
    info = sf.info(io.BytesIO(response.content))
    assert info.samplerate == 48000 and info.frames == 4800


def test_directed_music_beds_are_downloadable_but_music_cache_inputs_are_not(client, context, finished_job):
    from dataclasses import replace
    saved, manifest = stems(context, finished_job)
    directory = context.settings.cache_path / "music" / "beds"
    directory.mkdir(parents=True)
    bed = directory / "directed.wav"
    bed.write_bytes(b"directed music bed")
    replace(saved, music=bed).save(manifest)
    url = f"/api/jobs/{finished_job}/mix/tracks"
    assert client.get(url).status_code == 200
    assert client.get(url + "/music").content == b"directed music bed"
    for relative in ("music/decoded.wav", "music/speech/recording.wav", "music/analysis/private.wav"):
        private = context.settings.cache_path / relative
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_bytes(b"private cache data")
        replace(saved, music=private).save(manifest)
        assert client.get(url + "/music").status_code == 403


def test_directed_music_bed_folder_cannot_escape_through_a_symlink(client, context, finished_job, tmp_path):
    from dataclasses import replace

    import pytest
    saved, manifest = stems(context, finished_job)
    outside = tmp_path / "outside-beds"
    outside.mkdir()
    (outside / "private.wav").write_bytes(b"private audio")
    parent = context.settings.cache_path / "music"
    parent.mkdir(parents=True)
    linked = parent / "beds"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    replace(saved, music=linked / "private.wav").save(manifest)
    response = client.get(f"/api/jobs/{finished_job}/mix/tracks/music")
    assert response.status_code == 403
    assert response.content != b"private audio"
