"""Owned track copies and metadata, reusable across projects without network access."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from voxframe.render.encode.probe import probe_capabilities
from voxframe.render.ffpath import run_ffmpeg

SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma"}
FORMATS = {"wav", "mp3", "mov", "mp4", "m4a", "flac", "ogg", "aac", "asf"}
MOODS = ("calm", "energetic", "cinematic", "reflective", "inspiring", "other")
MAX_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    credit: str
    mood: str
    seconds: float
    bytes: int
    added_at: str
    suffix: str

    def public(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "suffix"}

    @property
    def attribution(self) -> str:
        return self.credit or f"{self.title} (supplied by the user)"


class MusicLibrary:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tracks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, credit TEXT NOT NULL,
                mood TEXT NOT NULL, seconds REAL NOT NULL, bytes INTEGER NOT NULL,
                added_at TEXT NOT NULL, suffix TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0)""")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.root / "tracks.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def clean(title: str, credit: str, mood: str) -> tuple[str, str, str]:
        title, credit = " ".join(title.split()), " ".join(credit.split())
        if not 1 <= len(title) <= 120 or len(credit) > 300 or mood not in MOODS:
            raise ValueError(
                "Use a title up to 120 characters, credit up to 300, and a listed mood.")
        return title, credit, mood

    @staticmethod
    def track(row: sqlite3.Row) -> Track:
        return Track(**{key: row[key] for key in Track.__dataclass_fields__})

    def get(self, key: str) -> Track:
        if re.fullmatch(r"[a-f0-9]{64}", key) is None:
            raise KeyError(key)
        with self.connect() as db:
            row = db.execute("SELECT * FROM tracks WHERE id=? AND hidden=0", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return self.track(row)

    def file(self, track: Track) -> Path:
        if track.suffix not in SUFFIXES or re.fullmatch(r"[a-f0-9]{64}", track.id) is None:
            raise KeyError(track.id)
        path = (self.root / track.id / f"audio{track.suffix}").resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise KeyError(track.id)
        return path

    def list(self, query: str = "", mood: str = "", offset: int = 0, limit: int = 60) -> dict:
        escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        where = "hidden=0 AND (title LIKE ? ESCAPE '\\' OR credit LIKE ? ESCAPE '\\')"
        args = [pattern, pattern]
        if mood:
            where += " AND mood=?"
            args.append(mood)
        with self.connect() as db:
            total = db.execute(f"SELECT count(*) FROM tracks WHERE {where}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM tracks WHERE {where} ORDER BY added_at DESC,id "
                              "LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return {"tracks": [self.track(row).public() for row in rows], "total": total}

    @staticmethod
    def inspect_audio(source: Path) -> tuple[float, int]:
        """Accept standalone audio containers before any preview or music fitting."""
        suffix = source.suffix.lower()
        size = source.stat().st_size
        if suffix not in SUFFIXES or not 0 < size <= MAX_BYTES:
            raise ValueError("Choose an audio track up to 200 MB.")
        caps = probe_capabilities()
        if not caps.ffprobe_path:
            raise ValueError("FFprobe is needed to read a music track.")
        result = run_ffmpeg(caps.ffprobe_path, ["-v", "error", "-protocol_whitelist", "file,pipe",
            "-select_streams", "a:0", "-show_entries",
            "stream=codec_type:format=duration,format_name",
            "-of", "json", str(source.resolve())])
        info = json.loads(result.stdout)
        form = info.get("format", {})
        seconds = float(form.get("duration", 0))
        formats = FORMATS.intersection(form.get("format_name", "").split(","))
        if not info.get("streams") or not formats:
            raise ValueError("This file is not a supported audio track.")
        if not .1 <= seconds <= 7200:
            raise ValueError("Choose a track between 0.1 seconds and two hours.")
        run_ffmpeg(caps.ffmpeg_path, ["-v", "error", "-protocol_whitelist", "file,pipe",
            "-i", str(source.resolve()), "-t", "1", "-map", "0:a:0", "-f", "null", "-"])
        return seconds, size

    def import_track(self, source: Path, title: str, credit: str, mood: str) -> tuple[Track, bool]:
        title, credit, mood = self.clean(title, credit, mood)
        seconds, size = self.inspect_audio(source)
        suffix = source.suffix.lower()
        with source.open("rb") as file:
            key = hashlib.file_digest(file, "sha256").hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM tracks WHERE id=?", (key,)).fetchone()
            folder = self.root / key
            folder.mkdir(exist_ok=True)
            if not folder.resolve().is_relative_to(self.root):
                raise ValueError("The music folder is outside the library.")
            target = folder / f"audio{old['suffix'] if old else suffix}"
            if not target.resolve().is_relative_to(self.root):
                raise ValueError("The music file is outside the library.")
            if not target.is_file():
                staged = folder / f".{secrets.token_hex(8)}.tmp"
                try:
                    shutil.copyfile(source, staged)
                    with staged.open("rb") as file:
                        if hashlib.file_digest(file, "sha256").hexdigest() != key:
                            raise ValueError(
                                "The track changed while being copied. Try importing again.")
                    staged.replace(target)
                finally:
                    staged.unlink(missing_ok=True)
            if old:
                db.execute("UPDATE tracks SET hidden=0 WHERE id=?", (key,))
            else:
                db.execute("INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?,0)",
                    (key, title, credit, mood, seconds, size,
                     datetime.now(UTC).isoformat(), suffix))
        return self.get(key), old is not None

    def update(self, key: str, title: str, credit: str, mood: str) -> Track:
        self.get(key)
        title, credit, mood = self.clean(title, credit, mood)
        with self.connect() as db:
            db.execute("UPDATE tracks SET title=?,credit=?,mood=? WHERE id=?",
                       (title, credit, mood, key))
        return self.get(key)

    def hide(self, key: str) -> None:
        self.get(key)
        with self.connect() as db:
            db.execute("UPDATE tracks SET hidden=1 WHERE id=?", (key,))
        # Keep the owned file: saved projects and undo history still refer to it.
