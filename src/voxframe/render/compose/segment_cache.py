"""Reuse scene segments across renders, so a long render can resume.

A 45-second clip re-renders in seconds, so nothing here mattered until now. An
hour of audio is several hundred segments: failing at minute 50 and starting
again from zero is the difference between a tool someone uses and one they
abandon.

**What makes a segment reusable** is that it is a pure function of its inputs.
A scene segment depends on the asset, the frame count, the output size, the
motion settings and the quality preset — and on nothing else. Two renders that
agree on all of those produce identical bytes, so the second can copy the first.

The cache key is a hash of exactly those inputs. Anything omitted from the key
is something that can change without invalidating the cache, which is how a
cache serves stale frames; anything included unnecessarily is a cache that
misses when it should hit. The fields below are therefore deliberate rather
than "everything to hand".

**Not keyed on the plan's scene index.** Inserting a title card renumbers every
scene after it (D-099), and renumbering does not change what a segment looks
like. Keying on index would throw away the whole cache for a one-word title.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

import structlog

from voxframe.plan.scene_plan import PlannedScene
from voxframe.render.version import RENDERER_VERSION

__all__ = ["SegmentCache", "segment_key"]

log = structlog.get_logger(__name__)


def segment_key(
    scene: PlannedScene,
    *,
    frames: int,
    width: int,
    height: int,
    fps: float,
    motion_signature: str,
    quality: str,
    background: str,
    footage_signature: str = "",
) -> str:
    """A content hash identifying one rendered segment.

    Args:
        scene: The scene being rendered.
        frames: Frames to produce, which differs from the scene duration when
            a transition needs padding (D-097).
        width: Output width.
        height: Output height.
        fps: Frame rate.
        motion_signature: Stable description of the motion settings.
        quality: Quality preset name.
        background: Background colour for unmatched scenes.
        footage_signature: For a speaker shot, what identifies its frames
            (D-192); empty for every other scene, which keeps their keys as
            they were.

    Returns:
        A short hex digest.
    """
    asset = scene.asset

    parts = [
        f"v{RENDERER_VERSION}",
        # The asset's content hash, not its path: the same image moved or
        # re-downloaded elsewhere is still the same image.
        asset.id if asset else "none",
        asset.kind.value if asset else "none",
        f"{asset.duration:.3f}" if asset and asset.duration else "still",
        scene.motion.value,
        # Cards carry their own text, which is what they look like.
        scene.card_kind,
        scene.card_text,
        str(frames),
        f"{width}x{height}",
        f"{fps:.3f}",
        motion_signature,
        quality,
        background,
    ]
    if footage_signature:
        parts.append(footage_signature)

    return hashlib.sha256("\u0000".join(parts).encode()).hexdigest()[:20]


@dataclass
class SegmentCache:
    """An on-disk store of rendered scene segments.

    Attributes:
        directory: Where segments are kept. ``None`` disables caching, which
            is what a one-off render of a short clip wants.
    """

    directory: Path | None = None

    #: Counters, reported at the end of a render so a user can see whether the
    #: cache is doing anything.
    hits: int = 0
    misses: int = 0

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def path_for(self, key: str) -> Path | None:
        """Where a segment with this key lives."""
        if self.directory is None:
            return None
        return self.directory / f"seg_{key}.mp4"

    def fetch(self, key: str, destination: Path) -> bool:
        """Copy a cached segment into place, if one exists.

        Returns:
            ``True`` on a hit. The caller then skips rendering entirely.
        """
        cached = self.path_for(key)
        if cached is None or not cached.is_file():
            self.misses += 1
            return False

        # A zero-length file is a crashed render, not a cache entry. Treating
        # it as a hit would produce a video with a missing scene and no error.
        if cached.stat().st_size == 0:
            log.warning("render.cache.empty", key=key)
            cached.unlink(missing_ok=True)
            self.misses += 1
            return False

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, destination)
        self.hits += 1
        return True

    def store(self, key: str, segment: Path) -> None:
        """Keep a freshly rendered segment.

        Failures are logged rather than raised: a cache that cannot write is a
        slow render, not a broken one.
        """
        cached = self.path_for(key)
        if cached is None or not segment.is_file():
            return

        try:
            self.directory.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
            # Write beside the target then rename, so an interrupted copy
            # cannot leave a half-written file that looks like a valid entry.
            staging = cached.with_suffix(".partial")
            shutil.copy2(segment, staging)
            staging.replace(cached)
        except OSError as exc:
            log.warning("render.cache.store_failed", key=key, error=str(exc))

    def summary(self) -> str:
        total = self.hits + self.misses
        if not total:
            return "cache disabled"
        return f"{self.hits}/{total} segments reused"
