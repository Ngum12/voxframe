"""The music component's manifest, shared by both installer builds (D-172).

The installers leave the music-fitting libraries out and ship this manifest
instead: every wheel's PyPI address, SHA-256 and size, for the app to fetch
the first time someone uses their own music track (``voxframe.components``).
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

__all__ = ["distribution", "split_music", "write_music_manifest"]


def distribution(filename: str) -> str:
    """A wheel's distribution name, normalised: 'scikit_learn-1.9...' -> 'scikit-learn'."""
    return filename.split("-", 1)[0].lower().replace("_", "-").replace(".", "-")


def write_music_manifest(folder: Path, manifest: Path) -> None:
    """Every wheel in ``folder`` with its PyPI address, SHA-256 and size.

    The address comes from PyPI's own record of that exact file, and its
    published SHA-256 must match the downloaded one.
    """
    entries = []
    for path in sorted(folder.glob("*.whl")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        name, version = path.name.split("-")[:2]
        record = json.loads(
            urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json").read()
        )
        published = next((f for f in record["urls"] if f["filename"] == path.name), None)
        if published is None or published["digests"]["sha256"] != digest:
            raise SystemExit(f"{path.name} is not the file PyPI publishes; not shipping it")
        entries.append(
            {"filename": path.name, "url": published["url"], "sha256": digest,
             "size": path.stat().st_size}
        )
    manifest.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"name": "music", "wheels": entries}, indent=1)
    manifest.write_text(text, encoding="utf-8")


def split_music(combined: Path, app_alone: Path, music: Path) -> None:
    """Move every wheel in ``combined`` that the app alone does not need to ``music``."""
    app_names = {distribution(path.name) for path in app_alone.glob("*.whl")}
    music.mkdir(parents=True, exist_ok=True)
    for path in list(combined.glob("*.whl")):
        if distribution(path.name) not in app_names:
            path.replace(music / path.name)


def pinned(folder: Path) -> str:
    """``name==version`` for every wheel in a folder, as a pip constraints file."""
    lines = sorted({"==".join(path.name.split("-")[:2]) for path in folder.glob("*.whl")})
    return "\n".join(lines) + "\n"
