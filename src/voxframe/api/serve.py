"""Launching the local server.

Separated from :mod:`voxframe.api.app` so importing the app for a test does not
drag in uvicorn, and so the launch policy — which interface, which port, whether
to print the token — lives in one readable place.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

import structlog
from fastapi import FastAPI

from voxframe.api.app import ApiContext, create_app
from voxframe.api.security import SessionToken, assert_local_bind
from voxframe.config.settings import Settings, get_settings
from voxframe.jobs.store import JobStore

__all__ = ["ServerHandle", "build_context", "find_free_port"]

log = structlog.get_logger(__name__)

#: Default port. Chosen high and unremarkable; if it is taken we pick another
#: rather than failing, because a local tool should start.
DEFAULT_PORT = 8765


@dataclass(frozen=True, slots=True)
class ServerHandle:
    """A configured, not-yet-running server."""

    app: FastAPI
    host: str
    port: int
    token: SessionToken
    store: JobStore

    @property
    def url(self) -> str:
        """The URL to open, carrying the token.

        The token travels in the query string on this first navigation only,
        because nothing has run in the page yet to set a header. The page is
        expected to store it and strip it from the address bar.
        """
        return f"http://{self.host}:{self.port}/?token={self.token.value}"


def find_free_port(host: str, preferred: int = DEFAULT_PORT) -> int:
    """Return ``preferred`` if free, else an arbitrary free port.

    A local tool that refuses to start because something else holds one port is
    a worse experience than one that moves.
    """
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, candidate))
            except OSError:
                continue
            return int(probe.getsockname()[1])

    raise OSError(f"No free port available on {host}")


def build_context(
    settings: Settings | None = None,
    *,
    root: Path | None = None,
    require_token: bool = True,
) -> ApiContext:
    """Assemble the API context, including the path sandbox.

    The sandbox is the job root plus the library. Nothing else on the disk is
    reachable through the API, so an upload cannot be pointed at ``~/.ssh`` and
    a download cannot walk out of the output directory.
    """
    from voxframe.config.paths import data_dir, is_source_checkout
    from voxframe.config.userprefs import load_preferences

    resolved = settings or get_settings()
    installed = not is_source_checkout()

    # A library moved in Settings, unless the environment names one (D-156).
    moved = load_preferences().library_path
    if settings is None and moved and "VOXFRAME_LIBRARY_PATH" not in os.environ:
        resolved = resolved.model_copy(update={"library_path": Path(moved)})

    # Jobs -- uploads, thumbnails, working files -- are app data. In a checkout
    # they sit beside the output as they always have; installed, they go in the
    # data folder, and only finished videos go to the videos folder (D-156).
    default_root = data_dir() / "jobs" if installed else resolved.output_path / "web"
    job_root = (root or default_root).resolve()
    job_root.mkdir(parents=True, exist_ok=True)

    # The library is allowed whether or not it exists yet (D-143). A new user's
    # library is created by their first render's download, and allowing it
    # only if it existed at startup refused every image that render found --
    # the filmstrip showed none of them until the app was restarted.
    allowed = [job_root, resolved.library_path.resolve()]

    return ApiContext(
        settings=resolved,
        store=JobStore(job_root),
        token=SessionToken(),
        allowed_paths=tuple(allowed),
        require_token=require_token,
        videos_folder=resolved.output_path if installed else None,
    )


def build_server(
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    settings: Settings | None = None,
    allow_remote: bool = False,
) -> ServerHandle:
    """Configure a server without starting it.

    Raises:
        AccessDenied: If asked to bind somewhere other than loopback without
            ``allow_remote``. The app has no multi-user authentication, so this
            has to be a deliberate choice rather than a default.
    """
    assert_local_bind(host, allow_remote=allow_remote)

    context = build_context(settings)
    chosen = port if port is not None else find_free_port(host)

    log.info("api.configured", host=host, port=chosen, jobs=str(context.store.root))

    return ServerHandle(
        app=create_app(context),
        host=host,
        port=chosen,
        token=context.token,
        store=context.store,
    )


def serve(handle: ServerHandle) -> None:
    """Run the server until interrupted.

    Blocks. ``uvicorn`` is imported here so the rest of the module stays usable
    without the ``web`` extra installed.
    """
    import uvicorn

    config = uvicorn.Config(
        handle.app,
        host=handle.host,
        port=handle.port,
        log_level="warning",
        access_log=False,
    )
    try:
        uvicorn.Server(config).run()
    finally:
        handle.store.shutdown()
