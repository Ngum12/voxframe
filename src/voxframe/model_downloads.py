"""Getting the models before the first video (D-157).

Voxframe needs two models: Whisper, to hear the recording, and CLIP, to match
what is said to pictures. Neither ships with the app; together they are about
3.1 GB (standard) or 750 MB (lite, D-067). Downloading them in the middle of a
first render, under a progress bar about something else, is the worst way to
meet a 3 GB download. This does it up front, when the person says so, and says
how much, what for, and how far it has got.

Progress is **measured, not estimated**: the bytes on disk in the model cache,
partly downloaded files included, rising as they arrive. Each model is fetched
with its own library's download function, so what is fetched is exactly what
the app will use; an interrupted file resumes where it stopped.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from voxframe.config.settings import Settings

__all__ = [
    "Download",
    "ModelNeed",
    "hub_cache",
    "model_needs",
    "start_download",
]

log = structlog.get_logger(__name__)

#: Published download sizes, MB. With the CLIP sizes in EMBEDDING_MODELS they
#: add up to each profile's total (D-067).
WHISPER_DOWNLOAD_MB = {
    "tiny": 75, "base": 145, "small": 485, "medium": 1530, "large-v3-turbo": 1620,
}

#: Hugging Face repositories each model is fetched from, for readiness checks.
_WHISPER_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
_CLIP_REPOS = {
    "default": ("laion/CLIP-ViT-B-32-xlm-roberta-base-laion5B-s13B-b90k", "xlm-roberta-base"),
    "lite": ("laion/CLIP-ViT-B-32-laion2B-s34B-b79K",),
}

#: A repository holding less than this share of its published size is a
#: download in progress, or an abandoned one: not ready.
_READY_SHARE = 0.9


@dataclass(frozen=True)
class ModelNeed:
    key: str
    label: str
    purpose: str
    megabytes: int
    repos: tuple[str, ...]


def hub_cache() -> Path:
    """Where the model libraries keep downloads (set per install, D-156)."""
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE)


def _repo_bytes(repo: str) -> int:
    """Bytes actually held for a repository, each real file counted once.

    The cache's layout varies: files kept in ``blobs/`` and linked into
    ``snapshots/``; real files in ``snapshots/`` on Windows without permission
    to make links; or blobs that are themselves links into a store shared
    across repositories. Following every entry to the file it really is, and
    counting each of those once, gives the true size in all of them -- a partly
    downloaded file included.
    """
    folder = hub_cache() / f"models--{repo.replace('/', '--')}"
    if not folder.is_dir():
        return 0
    real: set[Path] = set()
    total = 0
    for path in folder.rglob("*"):
        try:
            target = path.resolve()
            if target in real or not target.is_file():
                continue
            real.add(target)
            total += target.stat().st_size
        except OSError:
            continue
    return total


def model_needs(settings: Settings) -> list[ModelNeed]:
    """The models this profile needs, largest first."""
    from voxframe.library.embeddings import EMBEDDING_MODELS

    whisper = settings.resolved_transcribe_model
    clip = settings.resolved_embed_model
    return [
        ModelNeed(
            key=f"whisper:{whisper}",
            label="Speech recognition (Whisper)",
            purpose="Hears the recording and times every word.",
            megabytes=WHISPER_DOWNLOAD_MB.get(whisper, 1620),
            repos=(_WHISPER_REPOS.get(whisper, f"Systran/faster-whisper-{whisper}"),),
        ),
        ModelNeed(
            key=f"clip:{clip}",
            label="Picture matching (CLIP)",
            purpose="Matches what is said to photographs and clips.",
            megabytes=EMBEDDING_MODELS[clip][2],
            repos=_CLIP_REPOS[clip],
        ),
    ]


def is_ready(need: ModelNeed) -> bool:
    """Whether a model is fully here and can run.

    Every repository must be present, not just enough bytes in total: the
    default picture model's weights alone pass the size test, so a download
    that stopped before the small tokenizer looked finished (D-163). And the
    modules it needs must import -- bytes on disk are no use without them.
    """
    if any(_repo_bytes(repo) == 0 for repo in need.repos):
        return False
    held = sum(_repo_bytes(repo) for repo in need.repos)
    if held < need.megabytes * 1_000_000 * _READY_SHARE:
        return False
    return _stack_problem(need) is None


def _stack_problem(need: ModelNeed) -> str | None:
    """Why this copy of Voxframe cannot run a model, whatever is downloaded."""
    kind, name = need.key.split(":", 1)
    if kind != "clip":
        return None
    from voxframe.library.embeddings import stack_problem

    return stack_problem(name)


@dataclass
class Download:
    """One download run, read by the Getting-ready screen."""

    state: str = "idle"  # idle, downloading, updating, done, failed
    current: str = ""
    received_mb: float = 0.0
    total_mb: float = 0.0
    seconds_left: float | None = None
    message: str = ""
    completed_assets: int = 0
    total_assets: int = 0
    _started_bytes: int = 0
    _started_at: float = field(default_factory=time.monotonic)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self.state,
                "current": self.current,
                "received_mb": round(self.received_mb, 1),
                "total_mb": round(self.total_mb, 1),
                "seconds_left": None if self.seconds_left is None else round(self.seconds_left),
                "message": self.message,
                "completed_assets": self.completed_assets,
                "total_assets": self.total_assets,
            }


def _fetch(need: ModelNeed) -> None:
    """Download one model with its own library, without loading it."""
    kind, name = need.key.split(":", 1)
    if kind == "whisper":
        from faster_whisper import download_model

        download_model(name)
        return

    problem = _stack_problem(need)
    if problem is not None:
        raise BrokenInstall(problem)

    import open_clip

    from voxframe.library.embeddings import EMBEDDING_MODELS

    model_name, pretrained = EMBEDDING_MODELS[name][:2]
    config = open_clip.get_pretrained_cfg(model_name, pretrained)
    open_clip.pretrained.download_pretrained(config)
    # The multilingual model's text tokenizer is a separate download.
    open_clip.get_tokenizer(model_name)


class BrokenInstall(RuntimeError):
    """This copy of Voxframe lacks something a model needs to run."""


def _failure_message(exc: BaseException) -> str:
    """What to tell a person, which depends on whose problem it is.

    Only a network problem is fixed by checking the connection. Saying that
    about a missing module (D-163) sent a person to their router for a fault
    in the installer.
    """
    if isinstance(exc, BrokenInstall):
        return (
            f"This copy of Voxframe cannot run its picture model: {exc}. "
            "This is not your internet connection. Reinstalling Voxframe "
            "usually fixes it; if it does not, please report it."
        )
    if isinstance(exc, (OSError, TimeoutError)) or "connect" in str(exc).lower():
        return (
            "The download stopped. Check your internet connection and try "
            "again; it will carry on from where it stopped."
        )
    return (
        f"The download stopped: {type(exc).__name__}: {str(exc)[:200]}. "
        "Try again; if it stops the same way, please report it."
    )


def _all_bytes(needs: list[ModelNeed]) -> int:
    return sum(_repo_bytes(repo) for need in needs for repo in need.repos)


def library_ready(settings: Settings) -> bool:
    """Whether the actual app library can be searched with these settings."""
    from voxframe.library.db import AssetLibrary
    from voxframe.library.embeddings import EMBEDDING_MODELS

    if not (settings.library_path / "library.db").is_file():
        return True
    model_id = "/".join(EMBEDDING_MODELS[settings.resolved_embed_model][:2])
    stored = AssetLibrary(settings.library_path).embedding_models()
    return not stored or stored == {model_id}


def _update_library(settings: Settings, download: Download) -> None:
    from voxframe.library.db import AssetLibrary
    from voxframe.library.ingest import reembed_library
    from voxframe.library.manage import shared_embedder

    if library_ready(settings):
        return
    with download._lock:
        download.state = "updating"
        download.current = "Updating picture matching in your library"

    def progress(completed: int, total: int) -> None:
        with download._lock:
            download.completed_assets = completed
            download.total_assets = total

    library = AssetLibrary(settings.library_path)
    result = reembed_library(library, shared_embedder(settings), progress=progress)
    if result.failed or not library_ready(settings):
        raise RuntimeError(
            f"Could not update {len(result.failed)} library items. "
            "Check that the original pictures and clips are still available, then try again."
        )


def start_download(
    settings: Settings, download: Download, *, update_library: bool = False
) -> Download:
    """Fetch every missing model on a background thread, measuring as it goes.

    Idempotent: while a run is in progress, a second call changes nothing.
    """
    with download._lock:
        if download.state in {"downloading", "updating"}:
            return download
        missing = [need for need in model_needs(settings) if not is_ready(need)]
        download.state = "downloading" if missing or update_library else "done"
        download.total_mb = float(sum(need.megabytes for need in missing))
        download.received_mb = 0.0
        download.seconds_left = None
        download.message = ""
        download.completed_assets = 0
        download.total_assets = 0
        download._started_bytes = _all_bytes(missing)
        download._started_at = time.monotonic()
    if not missing and not update_library:
        return download

    stop = threading.Event()

    def measure() -> None:
        while not stop.wait(0.5):
            received = max(0, _all_bytes(missing) - download._started_bytes) / 1_000_000
            elapsed = time.monotonic() - download._started_at
            with download._lock:
                download.received_mb = min(received, download.total_mb)
                rate = received / elapsed if elapsed > 3 and received > 1 else 0
                download.seconds_left = (
                    (download.total_mb - download.received_mb) / rate if rate else None
                )

    def run() -> None:
        watcher = threading.Thread(target=measure, name="voxframe-download-meter", daemon=True)
        watcher.start()
        try:
            for need in missing:
                with download._lock:
                    download.current = need.label
                log.info("models.download.start", model=need.key, mb=need.megabytes)
                _fetch(need)
            if update_library:
                _update_library(settings, download)
            with download._lock:
                download.state = "done"
                download.received_mb = download.total_mb
                download.seconds_left = 0
                download.current = ""
        except Exception as exc:
            log.warning(
                "models.download.failed", error=type(exc).__name__, detail=str(exc)[:300]
            )
            with download._lock:
                download.message = (
                    f"Your library could not be updated: {exc}. Try again before making a video."
                    if download.state == "updating" else _failure_message(exc)
                )
                download.state = "failed"
        finally:
            stop.set()

    threading.Thread(target=run, name="voxframe-model-download", daemon=True).start()
    return download
