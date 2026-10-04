"""Explicit Openverse audio discovery and bounded, temporary local previews."""
from __future__ import annotations

import ipaddress
import math
import secrets
import socket
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from uuid import UUID

from voxframe.music.library import MAX_BYTES, MOODS, SUFFIXES
from voxframe.sourcing.licenses import LicensePolicy, parse_license
from voxframe.sourcing.openverse import OpenverseAdapter


@dataclass(frozen=True)
class Result:
    id: str
    title: str
    creator: str
    license: str
    license_url: str
    source_url: str
    url: str
    seconds: float
    mood: str
    instrumental: bool

    def public(self, token: str) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "url"} | {"token": token}

    @property
    def credit(self) -> str:
        # The Openverse detail page links the provider and the license deed.
        return f"{self.title} by {self.creator} ({self.license}) https://openverse.org/audio/{self.id}"


def search(query: str, *, page: int = 1, mood: str = "", min_seconds: int = 0,
           max_seconds: int = 7200, instrumental: bool = False,
           share_alike: bool = False) -> dict:
    query = " ".join(query.split())
    if not query:
        raise ValueError("Type a music search first.")
    policy = LicensePolicy(allow_share_alike=share_alike)
    payload = OpenverseAdapter(timeout=15)._get("/audio/", {
        "q": query, "page": str(page), "page_size": "20", "category": "music",
        "license": ",".join(policy.openverse_codes()), "mature": "false",
    })
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("Openverse returned an unreadable result list.")
    results = []
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        if str(item.get("license_version") or "") not in {"", "1.0", "2.0", "2.5", "3.0", "4.0"}:
            continue
        terms = parse_license(str(item.get("license") or ""),
                              str(item.get("license_version") or ""))
        if terms is None or not policy.permits(terms):
            continue
        try:
            key = str(UUID(str(item.get("id"))))
            seconds = float(item.get("duration") or 0) / 1000  # Openverse reports milliseconds.
        except (ValueError, TypeError):
            continue
        url = str(item.get("url") or "")
        if not safe_link(url) or not math.isfinite(seconds) or not .1 <= seconds <= 7200:
            continue
        raw_tags = item.get("tags")
        tags = {str(tag.get("name", "")).strip().lower()
                for tag in (raw_tags if isinstance(raw_tags, list) else [])
                if isinstance(tag, dict)}
        track_mood = next((m for m in MOODS if m != "other" and m in tags), "other")
        is_instrumental = "instrumental" in tags and not tags.intersection({"vocals", "vocal"})
        if not min_seconds <= seconds <= max_seconds:
            continue
        if (mood and mood not in tags) or (instrumental and not is_instrumental):
            continue
        source = str(item.get("foreign_landing_url") or "")
        results.append(Result(key, " ".join(str(item.get("title") or "Untitled").split())[:120],
            " ".join(str(item.get("creator") or "Unknown creator").split())[:80],
            terms.code, terms.url, source if safe_link(source) else "", url, seconds,
            track_mood, is_instrumental))
    count = payload.get("page_count")
    more = isinstance(count, int) and page < min(count, 20)
    return {"results": results, "has_more": more}


def safe_link(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
        return bool(parts.scheme == "https" and parts.hostname and not parts.username
                    and not parts.password and parts.port in (None, 443))
    except ValueError:
        return False


def public_url(url: str) -> None:
    if not safe_link(url):
        raise ValueError("This source does not use a supported HTTPS address.")
    host = urllib.parse.urlsplit(url).hostname
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("This source is not a public music host.")


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(result: Result, directory: Path) -> Path:
    suffix = Path(urllib.parse.urlsplit(result.url).path).suffix.lower()
    if suffix not in SUFFIXES:
        raise ValueError("This result has no supported audio file.")
    target = directory / f"source{suffix}"
    if target.is_file():
        return target
    public_url(result.url)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".{secrets.token_hex(8)}.partial"
    request = urllib.request.Request(result.url, headers={"User-Agent": "voxframe/2.1"})
    try:
        with urllib.request.build_opener(PublicRedirect()).open(request, timeout=20) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_BYTES:
                raise ValueError("This track is larger than 200 MB.")
            size = 0
            deadline = time.monotonic() + 60
            with temporary.open("wb") as output:
                while chunk := response.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES or time.monotonic() > deadline:
                        raise ValueError("This track is too large or took too long to download.")
                    output.write(chunk)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


@dataclass
class Results:
    entries: OrderedDict[str, tuple[float, Result]] = field(default_factory=OrderedDict)
    lock: Lock = field(default_factory=Lock)

    def remember(self, results: list[Result]) -> list[dict]:
        with self.lock:
            shown = []
            for result in results:
                token = secrets.token_hex(16)
                self.entries[token] = (time.monotonic(), result)
                shown.append(result.public(token))
            while len(self.entries) > 120:
                self.entries.popitem(last=False)
            return shown

    def get(self, token: str) -> Result:
        with self.lock:
            entry = self.entries.get(token)
            if entry is None or time.monotonic() - entry[0] > 900:
                self.entries.pop(token, None)
                raise KeyError(token)
            return entry[1]


def prune_previews(root: Path) -> None:
    """Remove yesterday's temporary downloads; saved tracks live elsewhere."""
    import re
    import shutil

    if not root.is_dir():
        return
    cutoff = time.time() - 24 * 60 * 60
    for folder in root.iterdir():
        if (re.fullmatch(r"[a-f0-9]{32}", folder.name) and not folder.is_symlink()
                and folder.is_dir() and folder.stat().st_mtime < cutoff):
            shutil.rmtree(folder, ignore_errors=True)
