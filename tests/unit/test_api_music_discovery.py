"""Online operations require music consent and preserve the selected attribution."""
from __future__ import annotations

# ruff: noqa: F811
import shutil
from pathlib import Path

import pytest

from tests.unit.test_api_editing import (  # noqa: F401
    _saved_plan,
    client,
    context,
    finished_job,
    library,
)
from tests.unit.test_music_discovery import ITEM
from tests.unit.test_music_library import audio
from voxframe.music import discovery


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VOXFRAME_CONFIG_DIR", str(tmp_path / "config"))


@pytest.fixture
def services(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    calls = []
    def get(self, path, params):  # type: ignore[no-untyped-def]
        calls.append((path, params))
        return {"results": [ITEM, dict(ITEM, license="by-sa")], "page_count": 2}
    source = audio(tmp_path / "music.wav")
    def download(result, directory):  # type: ignore[no-untyped-def]
        calls.append(("download", result.id))
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "source.wav"
        shutil.copyfile(source, target)
        return target
    monkeypatch.setattr(discovery.OpenverseAdapter, "_get", get)
    monkeypatch.setattr(discovery, "download", download)
    return calls


def test_consent_download_preview_save_and_project_credit(
    client, context, finished_job, services  # type: ignore[no-untyped-def]
):
    assert client.post("/api/music-search", json={"query": "piano"}).status_code == 409
    assert not services
    # Image consent does not implicitly consent to music search.
    client.put("/api/settings", json={"sourcing_consent": True})
    assert client.post("/api/music-search", json={"query": "piano"}).status_code == 409
    client.put("/api/settings", json={"music_search_consent": True})
    assert client.get("/api/settings").json()["music_search"]["enabled"]
    response = client.post("/api/music-search", json={"query": "piano"})
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert len(services) == 1 and "url" not in result
    preview = client.get(f"/api/music-search/{result['token']}/preview")
    assert preview.status_code == 200 and preview.content[:4] == b"RIFF"
    assert client.get("/api/music-library").json()["total"] == 0
    saved = client.post(f"/api/music-search/{result['token']}/save")
    assert saved.status_code == 200
    track = saved.json()["track"]
    assert "Ada Composer" in track["credit"] and "CC-BY-4.0" in track["credit"]
    assert "openverse.org/audio" in track["credit"]
    client.put(f"/api/jobs/{finished_job}/music",
               json={"choice": "own", "library_id": track["id"]})
    plan = _saved_plan(context, finished_job)
    assert plan.music_credit == track["credit"]
    assert (Path(plan.music_path).parent / "openverse.json").is_file()
    client.put("/api/settings", json={"music_search_consent": False})
    count = len(services)
    assert client.get(f"/api/music-search/{result['token']}/preview").status_code == 409
    assert client.post(f"/api/music-search/{result['token']}/save").status_code == 409
    assert len(services) == count
    assert client.get(f"/api/music-library/{track['id']}/audio").status_code == 200


def test_sa_policy_changes_and_unknown_tokens(client, services):  # type: ignore[no-untyped-def]
    client.put("/api/settings", json={"music_search_consent": True, "music_share_alike": True})
    results = client.post("/api/music-search", json={"query": "piano"}).json()["results"]
    assert len(results) == 2
    sa = results[1]["token"]
    client.put("/api/settings", json={"music_share_alike": False})
    assert client.post(f"/api/music-search/{sa}/save").status_code == 409
    assert client.post(f"/api/music-search/{'0'*32}/save").status_code == 404
    assert client.post("/api/music-search", json={"query": "piano", "page": 21}).status_code == 422
    assert client.post("/api/music-search", json={"query": "piano", "min_seconds": 10,
                                                "max_seconds": 5}).status_code == 422


def test_provider_errors_are_plain_and_do_not_leak_urls(client, monkeypatch):  # type: ignore[no-untyped-def]
    client.put("/api/settings", json={"music_search_consent": True})
    def fail(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("https://provider.example/?secret=private")
    monkeypatch.setattr(discovery, "search", fail)
    response = client.post("/api/music-search", json={"query": "piano"})
    assert response.status_code == 502 and "private" not in response.text


def test_download_failure_explains_a_blocked_host_without_exposing_its_url(client, services, monkeypatch):
    from urllib.error import HTTPError

    client.put("/api/settings", json={"music_search_consent": True})
    result = client.post("/api/music-search", json={"query": "piano"}).json()["results"][0]

    def denied(*args):
        raise HTTPError("https://provider.example/?key=private", 403, "Forbidden", {}, None)

    monkeypatch.setattr(discovery, "download", denied)
    response = client.get(f"/api/music-search/{result['token']}/preview")
    assert response.status_code == 422 and "host refused" in response.json()["detail"]
    assert "private" not in response.text and "provider.example" not in response.text


def test_bad_temporary_audio_does_not_poison_retries(client, context, services, monkeypatch, tmp_path):
    client.put("/api/settings", json={"music_search_consent": True})
    result = client.post("/api/music-search", json={"query": "piano"}).json()["results"][0]
    source = audio(tmp_path / "retry.wav")
    paths = []

    def download(result, directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "source.wav"
        if not paths:
            path.write_bytes(b"<html>Unavailable</html>")
        else:
            shutil.copyfile(source, path)
        paths.append(path)
        return path

    monkeypatch.setattr(discovery, "download", download)
    first = client.get(f"/api/music-search/{result['token']}/preview")
    assert first.status_code == 422 and not paths[0].exists()
    assert client.get(f"/api/music-search/{result['token']}/preview").status_code == 200


def test_duplicate_audio_gets_selected_source_credit(client, context, services, tmp_path):  # type: ignore[no-untyped-def]
    from voxframe.music.library import MusicLibrary

    library = MusicLibrary(context.settings.library_path / "music")
    owned, _ = library.import_track(audio(tmp_path / "owned.wav"), "My copy", "Own work", "calm")
    client.put("/api/settings", json={"music_search_consent": True})
    result = client.post("/api/music-search", json={"query": "piano"}).json()["results"][0]
    saved = client.post(f"/api/music-search/{result['token']}/save").json()
    assert saved["already_there"] and saved["track"]["id"] == owned.id
    assert "CC-BY-4.0" in saved["track"]["credit"]
    assert "Ada Composer" in library.get(owned.id).credit


def test_playlist_cannot_preview_private_audio(client, monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    private = audio(tmp_path / "private.wav")
    monkeypatch.setattr(discovery.OpenverseAdapter, "_get",
                        lambda *_args: {"results": [ITEM]})
    def playlist(result, directory):  # type: ignore[no-untyped-def]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "source.wav"
        target.write_text(f"ffconcat version 1.0\nfile '{private}'\n")
        return target
    monkeypatch.setattr(discovery, "download", playlist)
    client.put("/api/settings", json={"music_search_consent": True})
    token = client.post("/api/music-search", json={"query": "piano"}).json()["results"][0]["token"]
    assert client.get(f"/api/music-search/{token}/preview").status_code == 422
    assert client.post(f"/api/music-search/{token}/save").status_code == 422


def test_public_domain_mark_can_be_saved(client, monkeypatch, services):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(discovery.OpenverseAdapter, "_get",
                        lambda *_args: {"results": [dict(ITEM, license="pdm", license_version="1.0")]})
    client.put("/api/settings", json={"music_search_consent": True})
    result = client.post("/api/music-search", json={"query": "piano"}).json()["results"][0]
    assert client.post(f"/api/music-search/{result['token']}/save").status_code == 200


def test_audition_keeps_inflight_preview_files(client, context, finished_job, services):  # type: ignore[no-untyped-def]
    from voxframe.render.audio.mixdown import Stems
    from voxframe.render.compose.from_plan import stems_path

    client.put("/api/settings", json={"music_search_consent": True})
    token = client.post("/api/music-search", json={"query": "piano"}).json()["results"][0]["token"]
    video = context.store.artifact_path(finished_job, "video")
    Stems(voice=audio(video.parent / "voice.wav"), music=None, spans=(),
          landing=2, video_end=2, fps=30).save(stems_path(video))
    endpoint = f"/api/jobs/{finished_job}/mix/preview"
    request = {"mix": {}, "music_search_token": token, "seconds": 2}
    assert client.post(endpoint, json=request).status_code == 200
    first = next((video.parent / ".previews").glob("*.wav"))
    assert client.post(endpoint, json=request).status_code == 200
    assert first.is_file()  # Another listener may still be streaming this response.
    assert client.post(endpoint, json={**request, "kept_track": True}).status_code == 422
    assert not _saved_plan(context, finished_job).music_path
