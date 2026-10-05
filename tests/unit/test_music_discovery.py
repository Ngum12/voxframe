"""Openverse metadata, license gates, expiry and bounded public downloads."""
from __future__ import annotations

import io
import socket
import urllib.request
from dataclasses import replace
from typing import ClassVar

import pytest

from voxframe.music import discovery

TRACK_ID = "11111111-1111-4111-8111-111111111111"
ITEM = {"id": TRACK_ID, "title": "Gentle Piano", "creator": "Ada Composer", "license": "by",
        "license_version": "4.0", "url": "https://music.example/song.wav", "duration": 8000,
        "foreign_landing_url": "https://music.example/source",
        "tags": [{"name": "calm"}, {"name": "instrumental"}]}


def result():  # type: ignore[no-untyped-def]
    return discovery.Result(TRACK_ID, "Gentle Piano", "Ada Composer", "CC-BY-4.0",
        "https://creativecommons.org/licenses/by/4.0/", "https://music.example/source",
        "https://music.example/song.wav", 8, "calm", True)


def test_search_uses_only_typed_words_and_checks_licenses(monkeypatch):  # type: ignore[no-untyped-def]
    calls = []
    def get(self, path, params):  # type: ignore[no-untyped-def]
        calls.append((path, params))
        return {"page_count": 3, "results": [ITEM, *[dict(ITEM, license=code) for code in
                ("by-nc", "by-nd", "by-sa", "unknown")], {"title": "invalid"}]}
    monkeypatch.setattr(discovery.OpenverseAdapter, "_get", get)
    found = discovery.search(" gentle  piano ", mood="calm", instrumental=True,
                             min_seconds=5, max_seconds=10)
    assert len(found["results"]) == 1 and found["has_more"]
    assert found["results"][0].seconds == 8
    assert calls[0][0] == "/audio/" and calls[0][1]["q"] == "gentle piano"
    assert calls[0][1]["license"] == "cc0,pdm,by"
    assert len(discovery.search("piano", share_alike=True)["results"]) == 2
    assert not discovery.search("piano", min_seconds=9)["results"]
    assert not discovery.search("piano", mood="energetic")["results"]
    assert "url" not in found["results"][0].public("token")
    assert "Ada Composer" in found["results"][0].credit
    assert "CC-BY-4.0" in found["results"][0].credit


def test_malformed_and_unknown_tags_are_not_guessed(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(discovery.OpenverseAdapter, "_get", lambda *_: {"results": [
        dict(ITEM, duration="NaN"), dict(ITEM, url="file:///etc/passwd"),
        dict(ITEM, tags=[{"name": "vocal"}, {"name": "instrumental"}]),
        dict(ITEM, tags=None), "not a result"]})
    assert not discovery.search("piano", instrumental=True)["results"]


def test_expiry_and_capacity(monkeypatch):  # type: ignore[no-untyped-def]
    registry = discovery.Results()
    clock = [0.0]
    monkeypatch.setattr(discovery.time, "monotonic", lambda: clock[0])
    first = registry.remember([result()])[0]["token"]
    assert registry.get(first).title == "Gentle Piano"
    registry.remember([result()] * 120)
    with pytest.raises(KeyError):
        registry.get(first)
    token = registry.remember([result()])[0]["token"]
    clock[0] = 901
    with pytest.raises(KeyError):
        registry.get(token)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://example.com/a.wav",
                                  "https://user:secret@example.com/a.wav"])
def test_non_public_protocols_are_refused(url):  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        discovery.public_url(url)


def test_private_dns_and_redirects_are_refused(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kw:
                        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="public"):
        discovery.public_url("https://host.example/audio.wav")
    with pytest.raises(ValueError, match="public"):
        discovery.PublicRedirect().redirect_request(
            urllib.request.Request("https://example.com/song.wav"), None, 302, "", {},
            "https://localhost/private.wav")


def test_download_cap_cleanup_and_cache(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(discovery, "public_url", lambda _: None)
    class Response(io.BytesIO):
        def __init__(self, data):  # type: ignore[no-untyped-def]
            super().__init__(data)
            self.headers = {}
    class Opener:
        def open(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return Response(b"12345678")
    monkeypatch.setattr(discovery.urllib.request, "build_opener", lambda *_: Opener())
    monkeypatch.setattr(discovery, "MAX_BYTES", 5)
    with pytest.raises(ValueError, match="large"):
        discovery.download(result(), tmp_path / "cached")
    assert not list((tmp_path / "cached").iterdir())
    monkeypatch.setattr(discovery, "MAX_BYTES", 100)
    path = discovery.download(result(), tmp_path / "cached")
    assert path.read_bytes() == b"12345678"
    assert discovery.download(result(), tmp_path / "cached") == path
    with pytest.raises(ValueError, match="supported"):
        discovery.download(replace(result(), url="https://example.com/file.m3u"), tmp_path)


def test_old_preview_cleanup_keeps_recent_and_unrelated_files(tmp_path):  # type: ignore[no-untyped-def]
    import os
    import time

    old = tmp_path / ("a" * 32)
    recent = tmp_path / ("b" * 32)
    unrelated = tmp_path / "projects"
    for folder in (old, recent, unrelated):
        folder.mkdir()
        (folder / "source.wav").write_bytes(b"audio")
    earlier = time.time() - 25 * 60 * 60
    os.utime(old, (earlier, earlier))
    os.utime(unrelated, (earlier, earlier))
    discovery.prune_previews(tmp_path)
    assert not old.exists() and recent.is_dir() and unrelated.is_dir()


@pytest.mark.parametrize("content_type,suffix", [
    ("audio/mpeg", ".mp3"), ("audio/wav; charset=binary", ".wav"),
    ("application/octet-stream", ".wav"), ("", ".wav"),
])
def test_download_links_without_extensions_use_audio_response_types(monkeypatch, tmp_path, content_type, suffix):
    monkeypatch.setattr(discovery, "public_url", lambda _: None)
    data = b"RIFF\x00\x00\x00\x00WAVEaudio bytes" if suffix == ".wav" else b"ID3audio bytes"

    class Response(io.BytesIO):
        headers: ClassVar = {"Content-Type": content_type}

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response(data)

    monkeypatch.setattr(discovery.urllib.request, "build_opener", lambda *_: Opener())
    path = discovery.download(replace(result(), url="https://example.com/download?id=42"), tmp_path)
    assert path.suffix == suffix and path.read_bytes() == data
    assert discovery.download(replace(result(), url="https://example.com/download?id=42"), tmp_path) == path


def test_a_provider_web_page_is_not_treated_as_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "public_url", lambda _: None)

    class Response(io.BytesIO):
        headers: ClassVar = {"Content-Type": "text/html"}

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response(b"<html>Sign in</html>")

    monkeypatch.setattr(discovery.urllib.request, "build_opener", lambda *_: Opener())
    with pytest.raises(discovery.DownloadError, match="supported audio"):
        discovery.download(replace(result(), url="https://example.com/download"), tmp_path)
    assert not list(tmp_path.iterdir())
