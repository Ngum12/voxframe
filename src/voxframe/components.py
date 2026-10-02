"""Parts of Voxframe fetched only when someone first needs them (D-172).

**Music analysis** (librosa, with SciPy, scikit-learn, numba and llvmlite:
about 91 MB) is only needed to fit a person's *own* music track to their
speech. The installers leave it out and carry instead a small manifest: every
wheel's PyPI address and SHA-256, resolved together with the rest of the app
at build time, so the versions agree. The first time someone uses their own
track, the wheels are fetched, each checked against its SHA-256, unpacked into
the person's data folder and put on the import path.

Unpacking a wheel is enough to use it: a wheel is a zip laid out exactly as it
would be installed, and these are all binary or pure-Python wheels with no
build step. The app's own copy of every shared dependency (numpy, above all)
is the one the manifest was resolved against.

A development checkout or a ``pip install voxframe[music]`` has librosa
already, and needs none of this.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import io
import json
import shutil
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

__all__ = [
    "MUSIC_MANIFEST",
    "ComponentError",
    "Manifest",
    "activate_music",
    "install_music",
    "music_manifest",
    "music_ready",
]

log = structlog.get_logger(__name__)

MUSIC_MANIFEST = "music.json"
USER_AGENT = "voxframe (+https://github.com/Ngum12/voxframe)"


class ComponentError(RuntimeError):
    """A component could not be fetched or checked."""


@dataclass(frozen=True)
class Wheel:
    filename: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Manifest:
    """What makes up a component, and where it lives once unpacked."""

    name: str
    wheels: tuple[Wheel, ...]
    path: Path

    @property
    def megabytes(self) -> float:
        return sum(wheel.size for wheel in self.wheels) / 1_000_000

    @property
    def digest(self) -> str:
        """Names the unpacked folder: a new manifest gets a new folder."""
        text = "|".join(f"{w.filename}:{w.sha256}" for w in self.wheels)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    @classmethod
    def load(cls, path: Path) -> Manifest:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=str(data["name"]),
            wheels=tuple(
                Wheel(str(w["filename"]), str(w["url"]), str(w["sha256"]), int(w["size"]))
                for w in data["wheels"]
            ),
            path=path,
        )


def music_manifest(python: Path | None = None) -> Manifest | None:
    """The music component's manifest, when an installer shipped one.

    Beside the bundled FFmpeg, in the same places (``launcher.bundled_ffmpeg``):
    ``<app>/components`` for the Windows installer, and
    ``Voxframe.app/Contents/Resources/components`` for the Mac app.
    """
    executable = (python or Path(sys.executable)).resolve()
    for folder in (executable.parent.parent / "components", executable.parents[2] / "components"):
        candidate = folder / MUSIC_MANIFEST
        if candidate.is_file():
            try:
                return Manifest.load(candidate)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.warning("components.manifest_unreadable", path=str(candidate), error=str(exc))
                return None
    return None


def _home(manifest: Manifest) -> Path:
    from voxframe.config.paths import data_dir

    return data_dir() / "components" / f"{manifest.name}-{manifest.digest}"


def music_ready() -> bool:
    """Whether music analysis can run now, without a download."""
    if importlib.util.find_spec("librosa") is not None:
        return True
    manifest = music_manifest()
    return manifest is not None and (_home(manifest) / ".complete").is_file()


def activate_music() -> bool:
    """Put an unpacked music component on the import path, if there is one.

    Returns:
        Whether librosa can now be imported.
    """
    if importlib.util.find_spec("librosa") is not None:
        return True
    manifest = music_manifest()
    if manifest is None:
        return False
    home = _home(manifest)
    if not (home / ".complete").is_file():
        return False
    if str(home) not in sys.path:
        sys.path.insert(0, str(home))
        importlib.invalidate_caches()
    return importlib.util.find_spec("librosa") is not None


def install_music(progress: Callable[[float, float], None] | None = None) -> Path:
    """Fetch, check and unpack the music component, once.

    Args:
        progress: Called with (megabytes so far, megabytes in all).

    Returns:
        The folder the component was unpacked into.

    Raises:
        ComponentError: If there is no manifest, a download fails, or a file
            does not match its SHA-256. Nothing half-unpacked is left behind.
    """
    manifest = music_manifest()
    if manifest is None:
        raise ComponentError(
            "this copy of Voxframe has no music component to download; "
            'install it with: pip install "voxframe[music]"'
        )
    return install(manifest, progress)


def install(manifest: Manifest, progress: Callable[[float, float], None] | None = None) -> Path:
    """Fetch, check and unpack any component described by ``manifest``."""
    home = _home(manifest)
    if (home / ".complete").is_file():
        return home
    staging = home.with_name(home.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    done = 0.0
    total = manifest.megabytes
    try:
        for wheel in manifest.wheels:
            # Unpacked from memory, never from a copy in ``staging``: a wheel
            # may hold a file named like the wheel itself (scipy 1.18.1 for
            # Windows does), and unpacking it would empty the zip being read.
            with zipfile.ZipFile(io.BytesIO(_fetch(wheel))) as archive:
                _check_names(archive, staging)
                archive.extractall(staging)
            done += wheel.size / 1_000_000
            if progress is not None:
                progress(done, total)
        (staging / ".complete").write_text(manifest.digest, encoding="utf-8")
        shutil.rmtree(home, ignore_errors=True)
        staging.replace(home)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    log.info(
        "components.installed", name=manifest.name, megabytes=round(total, 1), folder=str(home)
    )
    return home


def _fetch(wheel: Wheel) -> bytes:
    request = urllib.request.Request(wheel.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data: bytes = response.read()
    except OSError as exc:
        raise ComponentError(f"could not download {wheel.filename}: {exc}") from exc
    actual = hashlib.sha256(data).hexdigest()
    if actual != wheel.sha256:
        raise ComponentError(f"{wheel.filename} does not match its SHA-256; nothing was installed")
    return data


def _check_names(archive: zipfile.ZipFile, root: Path) -> None:
    """Refuse a wheel whose files would land outside the component's folder."""
    base = root.resolve()
    for name in archive.namelist():
        target = (root / name).resolve()
        if not target.is_relative_to(base):
            raise ComponentError(f"a wheel tried to write outside its folder: {name}")
