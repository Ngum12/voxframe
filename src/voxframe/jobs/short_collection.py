"""Immutable ZIP collections of finished exports, never source files or plans."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10))}


@dataclass(frozen=True)
class CollectionClip:
    job_id: str
    title: str
    files: tuple[tuple[str, Path], ...]
    credits: tuple[str, ...]
    width: int | None = None
    height: int | None = None
    pending_edits: int = 0
    platform: str | None = None
    sound_destination: str | None = None


def slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    result = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:60].rstrip("-")
    result = result or "voxframe-shorts"
    return f"voxframe-{result}" if result in RESERVED else result


def _stamps(clips: list[CollectionClip]) -> list:
    result = []
    for clip in clips:
        for kind, path in clip.files:
            info = path.stat()
            result.append([clip.job_id, kind, str(path.resolve()), info.st_size, info.st_mtime_ns])
    return result


def package(title: str, clips: list[CollectionClip], directory: Path,
            check_current: Callable[[], None]) -> dict:
    check_current()
    before = _stamps(clips)
    material = {"version": 1, "title": title, "files": before,
                "clips": [{"id": c.job_id, "title": c.title, "credits": c.credits,
                           "width": c.width, "height": c.height,
                           "pending_edits": c.pending_edits, "platform": c.platform,
                           "sound_destination": c.sound_destination} for c in clips]}
    key = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:24]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{key}.zip"
    result = {"key": key, "name": f"{slug(title)}.zip", "clips": len(clips)}
    if target.is_file() and zipfile.is_zipfile(target):
        check_current()
        if _stamps(clips) != before:
            raise ValueError("An export changed. Build the collection again.")
        return {**result, "bytes": target.stat().st_size}
    folder_name = slug(title)
    manifest = {"version": 1, "collection": title,
                "created_at": datetime.now(UTC).isoformat(), "clips": []}
    credit_sections = []
    with tempfile.TemporaryDirectory(prefix="package-", dir=directory) as folder:
        partial = Path(folder) / "collection.zip"
        with zipfile.ZipFile(partial, "w", allowZip64=True) as archive:
            for number, clip in enumerate(clips, 1):
                stem = f"{number:02d}-{slug(clip.title)}"
                item = {"number": number, "title": clip.title, "project_id": clip.job_id,
                        "width": clip.width, "height": clip.height,
                        "pending_edits": clip.pending_edits, "platform": clip.platform,
                        "sound_destination": clip.sound_destination, "files": [],
                        "credits": list(clip.credits)}
                for kind, source in clip.files:
                    name = f"{stem}.{kind}"
                    digest, size = hashlib.sha256(), 0
                    info = zipfile.ZipInfo(f"{folder_name}/{name}")
                    info.compress_type = (zipfile.ZIP_STORED if kind == "mp4"
                                          else zipfile.ZIP_DEFLATED)
                    with source.open("rb") as incoming, archive.open(
                            info, "w", force_zip64=True) as out:
                        while chunk := incoming.read(1024 * 1024):
                            out.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    item["files"].append({"name": name, "bytes": size,
                                          "sha256": digest.hexdigest()})
                credit_text = "\n".join(clip.credits) if clip.credits else "No credits recorded."
                section = f"{number:02d}. {clip.title}\n{credit_text}\n"
                archive.writestr(f"{folder_name}/{stem}.credits.txt", section,
                                 compress_type=zipfile.ZIP_DEFLATED)
                credit_sections.append(section)
                manifest["clips"].append(item)
            archive.writestr(f"{folder_name}/manifest.json",
                json.dumps(manifest, indent=2, ensure_ascii=False),
                compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr(f"{folder_name}/CREDITS.txt", "\n".join(credit_sections),
                             compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr(f"{folder_name}/README.txt",
                f"{title}\n\nA collection of finished Voxframe exports.\n"
                "MP4: rendered video. SRT/VTT: subtitle companions, when produced.\n"
                "Matching names keep each video's subtitles and credits together.\n"
                "CREDITS.txt contains recorded media attribution for the collection.\n"
                "manifest.json lists the order, dimensions and file checksums.\n"
                "Pending edits are not included: Update video before packaging new edits.\n"
                "This collection contains no source recordings or editable project plans.\n",
                compress_type=zipfile.ZIP_DEFLATED)
        check_current()
        if _stamps(clips) != before:
            raise ValueError("An export changed while packaging. Build the collection again.")
        partial.replace(target)
    return {**result, "bytes": target.stat().st_size}
