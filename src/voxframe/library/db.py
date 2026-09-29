"""SQLite storage for the asset library, with vector search via sqlite-vec.

One file holds metadata and embeddings together, so a library is a directory
plus a database and nothing else. That keeps the local-first promise concrete:
a user can copy, back up or delete their library without special tooling.

Why SQLite rather than a vector database
----------------------------------------
Libraries here are thousands of images, not millions. At that scale sqlite-vec's
brute-force search is a few milliseconds, and it costs no daemon, no separate
install and no extra format to back up. The :class:`~voxframe.storage` protocol
exists so a larger deployment can swap this out without touching the engine.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import structlog

from voxframe.models.asset import Asset, AssetKind, LicenseInfo

__all__ = ["EMBEDDING_DIM", "AssetLibrary", "LibraryError"]

log = structlog.get_logger(__name__)

#: CLIP ViT-B-32 embedding width. Fixed by the model; changing models means
#: re-embedding, which the schema version guards against doing silently.
EMBEDDING_DIM = 512

#: Bumped when the schema or the embedding model changes incompatibly.
SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id               TEXT PRIMARY KEY,
    path             TEXT NOT NULL UNIQUE,
    kind             TEXT NOT NULL,
    sha256           TEXT NOT NULL UNIQUE,
    width            INTEGER NOT NULL,
    height           INTEGER NOT NULL,
    duration         REAL,

    -- Provenance is mandatory (D-012). NOT NULL alone is insufficient: it
    -- accepts '' and '   ', which would satisfy the column while leaving the
    -- credits file with a blank author. The CHECK constraints reject empty
    -- and whitespace-only values, so an unattributable asset cannot physically
    -- enter the library (D-035).
    --
    -- source_url is exempt: some legitimate local assets genuinely have no
    -- canonical URL, and demanding a fake one would make provenance less
    -- honest rather than more.
    license_name     TEXT NOT NULL CHECK (TRIM(license_name) <> ''),
    license_author   TEXT NOT NULL CHECK (TRIM(license_author) <> ''),
    license_source   TEXT NOT NULL CHECK (TRIM(license_source) <> ''),
    license_url      TEXT NOT NULL DEFAULT '',
    requires_attrib  INTEGER NOT NULL DEFAULT 1,
    allows_commercial INTEGER NOT NULL DEFAULT 1,

    tags             TEXT NOT NULL DEFAULT '[]',
    dominant_colors  TEXT NOT NULL DEFAULT '[]',
    phash            TEXT NOT NULL DEFAULT '',
    added_at         TEXT NOT NULL,

    -- Which model produced this asset's embedding (D-039). Stored per row
    -- rather than once for the library, so a partially re-embedded library is
    -- detectable rather than silently mixed.
    embed_model      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_assets_kind   ON assets(kind);
CREATE INDEX IF NOT EXISTS idx_assets_phash  ON assets(phash);
CREATE INDEX IF NOT EXISTS idx_assets_commercial ON assets(allows_commercial);
CREATE INDEX IF NOT EXISTS idx_assets_embed_model ON assets(embed_model);
"""

_VEC_SCHEMA = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS asset_vectors USING vec0(
    asset_id TEXT PRIMARY KEY,
    embedding FLOAT[{EMBEDDING_DIM}]
);
"""


class LibraryError(RuntimeError):
    """Raised when the library cannot be opened or used."""


class EmbeddingModelMismatch(LibraryError):
    """Raised when a search would compare vectors from different models.

    Embeddings from different models occupy unrelated coordinate systems.
    Comparing them produces plausible-looking but meaningless rankings, which
    is worse than an outright failure because nothing appears broken.

    Attributes:
        configured: The model the caller intends to use.
        stored: The models actually present in the library.
    """

    def __init__(self, configured: str, stored: frozenset[str]) -> None:
        self.configured = configured
        self.stored = stored

        listed = ", ".join(sorted(m for m in stored if m)) or "(unrecorded)"
        super().__init__(
            "This library was embedded with a different model.\n"
            f"  configured: {configured}\n"
            f"  in library: {listed}\n\n"
            "Vectors from different models are not comparable, so "
            "searching across them would return meaningless results.\n\n"
            "Re-embed the library with the configured model:\n"
            "  voxframe reembed\n\n"
            "Or switch back to the model the library already uses."
        )


class AssetLibrary:
    """The asset library: metadata plus vector search in one SQLite file.

    Args:
        root: Library directory. Media lives here; the database is
            ``library.db`` inside it.

    Raises:
        LibraryError: If the database cannot be opened or the extension is
            unavailable.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.db_path = self.root / "library.db"
        self._vec_available = False
        self._ensure_schema()

    # --- connection handling ---

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection with the vector extension loaded when available.

        A connection per operation rather than one shared: SQLite connections
        are not safe to share across threads, and Phase 8 serves concurrent
        requests.
        """
        self.root.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            # WAL lets readers proceed during a write, which matters when a
            # render searches the library while an ingest is running.
            connection.execute("PRAGMA journal_mode = WAL")

            try:
                import sqlite_vec

                connection.enable_load_extension(True)
                sqlite_vec.load(connection)
                connection.enable_load_extension(False)
                self._vec_available = True
            except Exception as exc:
                # Without the extension the library still stores and lists
                # assets; only similarity search is unavailable. Failing the
                # whole library here would be worse than degrading.
                self._vec_available = False
                log.warning("library.vec.unavailable", error=str(exc))

            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise LibraryError(f"Database error in {self.db_path}: {exc}") from exc
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        """Create tables if absent and check the schema version."""
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

            if self._vec_available:
                try:
                    connection.executescript(_VEC_SCHEMA)
                except sqlite3.Error as exc:
                    log.warning("library.vec.schema_failed", error=str(exc))
                    self._vec_available = False

            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()

            if row is None:
                connection.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(row["value"]) != SCHEMA_VERSION:
                raise LibraryError(
                    f"Library at {self.root} uses schema version {row['value']}, "
                    f"but this Voxframe expects {SCHEMA_VERSION}.\n"
                    "Re-ingest the library, or use a matching Voxframe version."
                )

    @property
    def supports_search(self) -> bool:
        """Whether similarity search is available.

        False when sqlite-vec could not be loaded. The library still works for
        storage and listing; only matching is unavailable.
        """
        with self._connect():
            return self._vec_available

    # --- writing ---

    def add(
        self,
        asset: Asset,
        embedding: list[float] | None = None,
        *,
        embed_model: str = "",
    ) -> None:
        """Insert or replace an asset.

        Args:
            asset: The asset. Its provenance is already validated by the model.
            embedding: Optional CLIP embedding. Stored only when the vector
                extension is available.
            embed_model: Identifier of the model that produced the embedding.
                Required whenever ``embedding`` is given: an embedding whose
                origin is unknown cannot be safely compared with any other
                (D-039).

        Raises:
            LibraryError: On a database error, a wrong-width embedding, or an
                embedding supplied without its model id.
        """
        if embedding is not None and not embed_model.strip():
            raise LibraryError(
                "An embedding must be stored with the id of the model that "
                "produced it. Vectors from different models are not "
                "comparable, and mixing them silently corrupts search."
            )
        if embedding is not None and len(embedding) != EMBEDDING_DIM:
            raise LibraryError(
                f"Embedding has {len(embedding)} dimensions, expected {EMBEDDING_DIM}. "
                "This usually means a different CLIP model produced it."
            )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO assets (
                    id, path, kind, sha256, width, height, duration,
                    license_name, license_author, license_source, license_url,
                    requires_attrib, allows_commercial,
                    tags, dominant_colors, phash, added_at, embed_model
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    asset.id,
                    str(asset.path),
                    asset.kind.value,
                    asset.sha256,
                    asset.width,
                    asset.height,
                    asset.duration,
                    asset.license.name,
                    asset.license.author,
                    asset.license.source,
                    asset.license.source_url,
                    int(asset.license.requires_attribution),
                    int(asset.license.allows_commercial),
                    json.dumps(list(asset.tags)),
                    json.dumps(list(asset.dominant_colors)),
                    asset.phash,
                    asset.added_at.isoformat(),
                    embed_model if embedding is not None else "",
                ),
            )

            if embedding is not None and self._vec_available:
                connection.execute(
                    "DELETE FROM asset_vectors WHERE asset_id = ?", (asset.id,)
                )
                connection.execute(
                    "INSERT INTO asset_vectors (asset_id, embedding) VALUES (?, ?)",
                    (asset.id, json.dumps(embedding)),
                )

    def remove(self, asset_id: str) -> bool:
        """Delete an asset and its embedding. Returns whether it existed."""
        with self._connect() as connection:
            if self._vec_available:
                connection.execute(
                    "DELETE FROM asset_vectors WHERE asset_id = ?", (asset_id,)
                )
            cursor = connection.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
            return cursor.rowcount > 0

    # --- reading ---

    def get(self, asset_id: str) -> Asset | None:
        """Fetch one asset by id."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE id = ?", (asset_id,)
            ).fetchone()
        return _row_to_asset(row) if row else None

    def by_hash(self, sha256: str) -> Asset | None:
        """Find an asset by content hash, for exact-duplicate detection."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE sha256 = ?", (sha256,)
            ).fetchone()
        return _row_to_asset(row) if row else None

    def all_assets(self, kind: AssetKind | None = None) -> tuple[Asset, ...]:
        """Every asset, optionally filtered by kind."""
        query = "SELECT * FROM assets"
        params: tuple[str, ...] = ()
        if kind is not None:
            query += " WHERE kind = ?"
            params = (kind.value,)
        query += " ORDER BY added_at DESC"

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(_row_to_asset(row) for row in rows)

    def vectors(self, asset_ids: list[str]) -> dict[str, list[float]]:
        """Stored embeddings for some assets, by id.

        Lets a caller ask a question of an image -- does it show printed text?
        (D-133) -- from the vector already stored at ingest, without loading or
        re-embedding the image. Assets with no stored vector are simply absent
        from the result.
        """
        if not asset_ids or not self._vec_available:
            return {}

        import array

        found: dict[str, list[float]] = {}
        with self._connect() as connection:
            for asset_id in dict.fromkeys(asset_ids):
                row = connection.execute(
                    "SELECT embedding FROM asset_vectors WHERE asset_id = ?",
                    (asset_id,),
                ).fetchone()
                if row is None:
                    continue
                # sqlite-vec stores FLOAT[] as packed little-endian float32.
                values = array.array("f")
                values.frombytes(row["embedding"])
                found[asset_id] = values.tolist()
        return found

    def count(self) -> int:
        """How many assets the library holds."""
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0])

    def embedding_models(self) -> frozenset[str]:
        """Every embedding model present in the library.

        More than one entry means the library was partially re-embedded and
        its vectors are not mutually comparable.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT embed_model FROM assets WHERE embed_model <> ''"
            ).fetchall()
        return frozenset(row["embed_model"] for row in rows)

    def check_embedding_model(self, configured: str) -> None:
        """Verify the library's vectors match the configured model.

        Called before any search. An empty library, or one with no embeddings
        yet, passes: there is nothing to be inconsistent with.

        Raises:
            EmbeddingModelMismatch: If the library holds vectors from a
                different model, or from more than one.
        """
        stored = self.embedding_models()
        if not stored:
            return
        if stored == {configured}:
            return
        raise EmbeddingModelMismatch(configured, stored)

    def assets_needing_reembed(self, configured: str) -> tuple[Asset, ...]:
        """Assets whose embeddings were made by a different model.

        Returned in insertion order so a re-embed can report stable progress.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assets WHERE embed_model <> ? ORDER BY added_at",
                (configured,),
            ).fetchall()
        return tuple(_row_to_asset(row) for row in rows)

    def search(
        self,
        embedding: list[float],
        limit: int = 10,
        *,
        exclude: frozenset[str] = frozenset(),
        commercial_only: bool = False,
        embed_model: str = "",
    ) -> tuple[tuple[Asset, float], ...]:
        """Find assets whose embeddings are nearest the query.

        Args:
            embedding: Query vector, from the text encoder.
            limit: Maximum results.
            exclude: Asset ids to skip, used for the repetition window.
            commercial_only: Skip assets whose licenses forbid commercial use.
            embed_model: The model that produced ``embedding``. When given, the
                library's stored vectors are checked against it and the search
                is refused on a mismatch rather than returning meaningless
                rankings (D-039).

        Returns:
            ``(asset, similarity)`` pairs, best first. Similarity is in
            ``[0, 1]``, converted from distance.

        Raises:
            LibraryError: If the vector extension is unavailable.
            EmbeddingModelMismatch: If the query and the library were embedded
                by different models.
        """
        if embed_model:
            self.check_embedding_model(embed_model)

        if len(embedding) != EMBEDDING_DIM:
            raise LibraryError(
                f"Query embedding has {len(embedding)} dimensions, "
                f"expected {EMBEDDING_DIM}"
            )

        with self._connect() as connection:
            if not self._vec_available:
                raise LibraryError(
                    "Similarity search needs the sqlite-vec extension.\n"
                    "  pip install sqlite-vec"
                )

            # Over-fetch so filtering does not leave too few results. The
            # repetition window can exclude a large share of a small library.
            fetch = limit + len(exclude) + 10

            rows = connection.execute(
                """
                SELECT v.asset_id, v.distance, a.*
                FROM asset_vectors v
                JOIN assets a ON a.id = v.asset_id
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY v.distance
                """,
                (json.dumps(embedding), fetch),
            ).fetchall()

        results: list[tuple[Asset, float]] = []
        for row in rows:
            if row["asset_id"] in exclude:
                continue
            if commercial_only and not row["allows_commercial"]:
                continue

            # sqlite-vec returns L2 distance over normalised vectors, where
            # d^2 = 2 - 2*cos. Inverting gives cosine similarity directly.
            distance = float(row["distance"])
            similarity = max(0.0, min(1.0, 1.0 - (distance**2) / 2.0))

            results.append((_row_to_asset(row), similarity))
            if len(results) >= limit:
                break

        return tuple(results)


def _row_to_asset(row: sqlite3.Row) -> Asset:
    """Rebuild an Asset from a database row."""
    return Asset(
        id=row["id"],
        path=Path(row["path"]),
        kind=AssetKind(row["kind"]),
        sha256=row["sha256"],
        width=row["width"],
        height=row["height"],
        duration=row["duration"],
        license=LicenseInfo(
            name=row["license_name"],
            author=row["license_author"],
            source=row["license_source"],
            source_url=row["license_url"],
            requires_attribution=bool(row["requires_attrib"]),
            allows_commercial=bool(row["allows_commercial"]),
        ),
        tags=tuple(json.loads(row["tags"])),
        dominant_colors=tuple(json.loads(row["dominant_colors"])),
        phash=row["phash"],
        added_at=datetime.fromisoformat(row["added_at"]).replace(tzinfo=UTC)
        if "+" not in row["added_at"]
        else datetime.fromisoformat(row["added_at"]),
    )
