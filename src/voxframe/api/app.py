"""The local HTTP API.

Step 1 of Phase 8: everything the browser will need, with no browser yet. The
routes are deliberately thin — they validate input, hand work to
:mod:`voxframe.jobs`, and serialise the result. No pipeline logic lives here,
because the CLI and the API must stay two front ends over one pipeline rather
than two implementations that drift (see :mod:`voxframe.jobs.pipeline`).

**Security is not a later layer.** A localhost server is reachable by every page
the user visits while it runs, so the Host check, the session token and the path
sandbox are installed here at construction time, not added once the UI works.
See :mod:`voxframe.api.security` for what each one stops.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from voxframe import __version__
from voxframe.api.plan_history import History, PlanHistory, replace_retrying
from voxframe.api.security import (
    PathOutsideSandbox,
    SessionToken,
    host_is_loopback,
    resolve_within,
)
from voxframe.config.settings import AspectRatio, QualityPreset, Settings, get_settings
from voxframe.config.userprefs import (
    KEY_FIELDS,
    apply_to_settings,
    load_preferences,
    save_preferences,
    sourcing_active,
)
from voxframe.jobs.pipeline import JobOptions, PipelineOutcome, Stage
from voxframe.jobs.store import Job, JobStore, ProgressEvent
from voxframe.library.db import AssetLibrary
from voxframe.model_downloads import Download
from voxframe.plan.audio_mix import AudioMix
from voxframe.plan.scene_plan import PlannedScene, ScenePlan
from voxframe.sourcing.manual import SearchResults

__all__ = ["ApiContext", "create_app", "static_root"]

log = structlog.get_logger(__name__)

#: Audio extensions the upload route accepts. Not a security boundary — FFmpeg
#: decides what it can actually decode — but it stops an obvious mistake early
#: and keeps a user from waiting on a render of a PDF.
AUDIO_SUFFIXES = frozenset(
    {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".mov", ".mkv"}
)

#: Upload cap. A 2-hour lossless recording is about 1.2 GB; beyond this is
#: almost certainly a mistake, and an unbounded upload can fill the disk.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024

#: Version of the sourcing-consent wording the user agreed to. Bumping it
#: re-asks, which is the right behaviour if what gets sent ever changes.
CONSENT_VERSION = 1

#: Image types accepted for "use your own photo". Kept to formats every
#: FFmpeg build decodes, so an accepted upload never fails at render time.
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})

#: Per-image upload cap. A 50 MB still is already far beyond anything a video
#: frame can show.
MAX_IMAGE_BYTES = 50 * 1024 * 1024

#: A clip of your own for a scene (D-197): a phone's few minutes of video.
CLIP_SUFFIXES = frozenset({".mp4", ".webm", ".mov", ".m4v"})
MAX_CLIP_BYTES = 1024 * 1024 * 1024

#: Name of the session cookie. HttpOnly, so no script can read it -- including
#: any script that might be injected into the page.
SESSION_COOKIE = "voxframe_session"

#: Routes reachable without authentication. Health, so a launcher can wait for
#: the port; and the session exchange, which authenticates by the token it is
#: handed rather than by a cookie it does not yet have.
UNAUTHENTICATED_PATHS = frozenset({"/api/health", "/api/session"})


def _is_app_shell(path: str) -> bool:
    """Whether a path is the static app shell rather than an API route.

    **The shell must load before any credential exists.** A browser fetches the
    page and its bundle from URLs it constructs itself, before a single line of
    our JavaScript has run -- so those requests carry no token and no cookie.
    Guarding them returns 401 for the script, the page stays blank, and nothing
    can ever authenticate: the code that would do it never loads. Found in a
    real browser; no API test could see it (D-119).

    Serving them unauthenticated is safe because they are the same bytes for
    every user and contain no data: the app shell, one JS bundle, one
    stylesheet. Everything under ``/api`` still requires a credential, which is
    where all the user's data lives.
    """
    return path == "/" or path.startswith("/assets/") or not path.startswith("/api")

#: Headers attached to every response. See :func:`_secured`.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        # WebAssembly may be compiled (the live captions are libass, built
        # for the browser: D-196); JavaScript still may not be evaluated.
        "script-src 'self' 'wasm-unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "form-action 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
}

#: How long to wait for an event before sending a keepalive comment. Long
#: enough not to be chatty, short enough that a proxy or a sleeping laptop
#: does not drop the connection silently.
KEEPALIVE_SECONDS = 15.0

#: Copied in chunks rather than read whole: a 1 GB upload read into memory on a
#: laptop is how a local tool gets itself killed by the OOM killer.
UPLOAD_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ApiContext:
    """Everything the routes need, resolved once at startup.

    Passed explicitly rather than read from module globals so tests can build an
    app against temporary directories without touching the user's real library.
    """

    settings: Settings
    store: JobStore
    token: SessionToken
    allowed_paths: tuple[Path, ...]
    require_token: bool = True
    #: Online search results a person has been shown, by token (D-142).
    search_results: SearchResults = field(default_factory=SearchResults)
    #: Where finished videos are placed under a readable name, for an
    #: installed app; ``None`` in a checkout, where videos stay with their job
    #: (D-156).
    videos_folder: Path | None = None
    #: The first-run model download, read by the Getting-ready screen (D-157).
    downloads: Download = field(default_factory=Download)


class RenderRequest(BaseModel):
    """Options for a render, as a browser sends them.

    Mirrors :class:`~voxframe.jobs.pipeline.JobOptions` but accepts strings and
    validates them here, so an invalid aspect ratio is a 422 rather than a
    stack trace from deep in the renderer.
    """

    upload_id: str = Field(description="Id returned by the upload route.")
    aspect: str = "16:9"
    quality: str = "standard"
    height: int = Field(default=720, ge=240, le=2160)
    style: str | None = None
    title: str = ""
    chapters: bool = True
    highlights_seconds: float | None = Field(default=None, gt=0)
    use_library: bool = True
    #: Show the recording's own picture, when it has one (D-192): the speaker
    #: in sync, with the matched pictures as cutaways.
    use_video: bool = False
    language: str | None = None
    languages: list[str] = Field(default_factory=list)
    model: str | None = None
    #: A music bed, uploaded like the recording and named by its upload id
    #: (D-148). Voxframe never supplies music of its own (D-091).
    music_upload_id: str | None = None
    #: Attribution for the track, written into the credits. Optional: a track
    #: with none is recorded as supplied by the person, never given an
    #: invented credit (D-091).
    music_credit: str = Field(default="", max_length=300)
    #: Music generated for the video instead, in this style (D-176). A new
    #: video gets its own variation; a track and a score are never both used.
    score_style: str | None = Field(default=None, max_length=40)


class MusicEdit(BaseModel):
    """The video's music, changed after it was made (D-179).

    ``score`` composes music in ``style``; ``new_variation`` asks for a new
    piece in it. ``own`` goes back to the person's own track, if this video had
    one, or uses a track uploaded now (``upload_id``), at any time after the
    video was made (D-184). ``forget_track`` removes the person's track, so
    it is no longer offered. Like every edit, it applies with "Update video",
    which re-renders only the sound.
    """

    choice: Literal["none", "own", "score"]
    #: A track uploaded with ``/api/uploads``, for ``own``. Never a path.
    upload_id: str | None = Field(default=None, max_length=64)
    #: The track's credit, as the person states it; never invented (D-091).
    #: ``None`` keeps the credit it has.
    credit: str | None = Field(default=None, max_length=300)
    forget_track: bool = False
    style: str | None = Field(default=None, max_length=40)
    intensity: float = Field(default=0.0, ge=-1.0, le=1.0)
    #: A particular variation (undo goes back to one); otherwise the video's
    #: own, or a new one with ``new_variation``.
    seed: int | None = Field(default=None, ge=0, lt=2**31)
    new_variation: bool = False


class SettingsUpdate(BaseModel):
    """Consent and API keys, as the settings screen sends them."""

    sourcing_consent: bool | None = None
    api_keys: dict[str, str] | None = None
    theme: Literal["system", "dark", "light"] | None = None


class ImageEdit(BaseModel):
    """A change to one scene's image.

    ``choose`` takes the id of one of the scene's recorded candidates -- a
    runner-up or a near miss. ``remove`` takes nothing. A path is never
    accepted.
    """

    action: Literal["choose", "remove"]
    asset_id: str | None = None


class ModelChoice(BaseModel):
    """Which set of models to download: best accuracy, or smallest (D-067)."""

    profile: Literal["standard", "lite"]


class FolderRequest(BaseModel):
    """One of Voxframe's own folders, by name. Never a path (D-115)."""

    which: Literal["library", "videos", "data"]


class MixPreview(BaseModel):
    """A short stretch of the mix at settings not yet applied (D-171)."""

    mix: AudioMix
    start: float = Field(default=0.0, ge=0.0)
    seconds: float = Field(default=15.0, gt=0.0, le=30.0)
    voice_only: bool = False
    #: Hear a track in place of the video's music, before choosing it (D-184):
    #: one uploaded now, or the person's track the video switched away from.
    music_upload_id: str | None = Field(default=None, max_length=64)
    kept_track: bool = False


class MotionEdit(BaseModel):
    """Whether a scene's camera moves."""

    on: bool


class ShotEdit(BaseModel):
    """Whether a scene shows the speaker or its picture (D-192)."""

    shot: Literal["speaker", "picture"]


class CaptionsEdit(BaseModel):
    """How the whole video's captions look and move (D-196). ``None`` is the
    template's choice."""

    animation: str | None = None
    transition: str | None = None
    anchor_y: float | None = Field(default=None, ge=0.05, le=0.95)
    size: float = Field(default=1.0, ge=0.6, le=1.8)
    uppercase: bool | None = None


class LayoutEdit(BaseModel):
    """How a scene shares the frame between the speaker and its picture (D-197)."""

    kind: Literal["full", "split", "inset"] = "full"
    split: float = 0.5
    speaker_first: bool = False
    divider: bool = True
    inset_speaker: bool = True
    inset_shape: Literal["circle", "rounded"] = "circle"
    inset_size: float = 0.36
    inset_x: float = 0.74
    inset_y: float = 0.22


class OverlayEdit(BaseModel):
    """One pop-up, as the studio sends it (D-198). Checked again as an
    ``Overlay`` before it reaches the plan."""

    kind: Literal["text", "sticker", "shape", "counter", "image"]
    scene: int
    word: int = 0
    seconds: float = 2.0
    x: float = 0.5
    y: float = 0.3
    size: float = 0.3
    entrance: Literal["pop", "slide", "bounce", "fade", "none"] = "pop"
    colour: str = "accent"
    text: str = ""
    look: Literal["pill", "bold", "note"] = "pill"
    sticker: str = ""
    shape: Literal[
        "arrow_down", "arrow_up", "arrow_left", "arrow_right", "ring", "underline"
    ] = "arrow_down"


class ProgressEdit(BaseModel):
    on: bool


class SceneCaptionsEdit(BaseModel):
    """One scene's own caption animation and emphasised words (D-196)."""

    animation: str | None = None
    emphasis: list[int] = Field(default_factory=list, max_length=200)


class CaptionEdit(BaseModel):
    """What a scene's captions should say."""

    text: str = Field(max_length=4000)


class CardText(BaseModel):
    """What a title or chapter card should say."""

    text: str = Field(max_length=400)


class NewCard(BaseModel):
    """A card to add: a title at the start, or a chapter before a scene.

    ``before`` is required for a chapter and ignored for a title. An empty
    chapter text takes the opening words of the scene it introduces.
    """

    kind: Literal["title", "chapter"]
    text: str = Field(default="", max_length=400)
    before: int | None = None


class ImageSearch(BaseModel):
    """What to look for online, for one scene."""

    query: str = Field(max_length=200)


class KeyCheck(BaseModel):
    """One key to test. Empty means "test the one already stored"."""

    adapter: str
    key: str = ""


def create_app(context: ApiContext) -> FastAPI:
    """Build the API.

    Args:
        context: Resolved settings, job store, token and sandbox roots.

    Returns:
        The configured application.
    """
    app = FastAPI(
        title="Voxframe",
        description="Local API for turning audio into captioned video.",
        version=__version__,
        # No docs by default: an unauthenticated schema endpoint on a local port
        # is one more thing any page can read.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.context = context

    _install_guards(app, context)
    _install_routes(app, context)
    _install_frontend(app)

    return app


def static_root() -> Path:
    """Where the built frontend lives.

    Populated by ``npm run build`` in ``web/``, which writes here rather than
    to a separate directory so the package ships one tree.
    """
    return Path(__file__).parent / "static"


def _install_frontend(app: FastAPI) -> None:
    """Serve the built React app, if it has been built.

    Mounted **after** the API routes so nothing here can shadow them, and only
    when the build exists: a developer running from a checkout without having
    built the frontend still gets a working API and a clear message rather than
    a confusing 404.

    The catch-all returns ``index.html`` for any unmatched path so a reload of
    a client-side route works. It never reaches outside the static directory:
    it serves exactly one file and ignores the path entirely.
    """
    root = static_root()
    index = root / "index.html"

    if not index.is_file():
        @app.get("/")
        def missing_frontend() -> HTMLResponse:
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8>"
                "<title>Voxframe</title>"
                "<body style='font-family:system-ui;padding:40px;max-width:40em'>"
                "<h1>The web app has not been built</h1>"
                "<p>The API is running. To build the interface:</p>"
                "<pre>cd web &amp;&amp; npm install &amp;&amp; npm run build</pre>",
                status_code=503,
            )
        return

    assets = root / "assets"
    if assets.is_dir():
        # Served by extension, and with nosniff a browser believes the type it
        # is told: a system that does not know these (Windows reads them from
        # the registry) would break the live captions' WebAssembly and fonts.
        import mimetypes

        for media_type, extension in (
            ("application/wasm", ".wasm"),
            ("text/javascript", ".js"),
            ("font/woff2", ".woff2"),
            ("font/ttf", ".ttf"),
        ):
            mimetypes.add_type(media_type, extension)
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def index_page() -> HTMLResponse:
        return _index_page(index)

    favicon = root / "favicon.svg"
    if favicon.is_file():
        @app.get("/favicon.svg")
        def favicon_file() -> FileResponse:
            # One known file, like the index (D-158).
            return FileResponse(favicon, media_type="image/svg+xml")

    @app.get("/{path:path}")
    def catch_all(path: str) -> HTMLResponse:
        # One known file, chosen without consulting `path`: a client-side route
        # must reload cleanly, and a path from a client must never select a
        # file. Anything under /api has already matched a real route above.
        return _index_page(index)


def _index_page(index: Path) -> HTMLResponse:
    """The app's page, already in the chosen look (D-185).

    The look is written into the page as it is served, so it is right from
    the first frame: no flash of the other one while the script loads. "Follow
    the system" writes nothing, and the stylesheet follows the computer.
    """
    page = index.read_text(encoding="utf-8")
    theme = load_preferences().theme
    if theme in ("dark", "light"):
        page = page.replace("<html", f'<html data-theme="{theme}"', 1)
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


# --- middleware ---------------------------------------------------------


def _install_guards(app: FastAPI, context: ApiContext) -> None:
    """Install the Host check, the token check and the response headers.

    Middleware rather than per-route dependencies because a route added later
    without the dependency would be unprotected, and that is exactly the kind of
    mistake that is invisible until someone finds it.

    **No CORS middleware is installed, deliberately.** Without
    ``Access-Control-Allow-Origin`` a browser refuses to let another origin read
    any response, which is the behaviour we want; adding CORS could only loosen
    it. A test asserts the header is absent, so nobody adds it "to fix" a
    cross-origin request that ought to fail.
    """

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[..., Any]) -> Any:
        # 1. Host check: loopback binding does not stop DNS rebinding, because
        #    the attacker's page uses their own origin. A rebound request
        #    carries their hostname, so refusing non-loopback Host values is
        #    what actually stops it.
        if not host_is_loopback(request.headers.get("host")):
            log.warning("api.host_rejected", host=request.headers.get("host"))
            return _secured(
                JSONResponse({"detail": "Invalid Host header."}, status_code=400)
            )

        # 2. Cross-origin requests are refused outright. A same-origin fetch
        #    either omits Origin or sends our own; anything else is another
        #    page talking to us, which no legitimate flow does.
        origin = request.headers.get("origin")
        if origin and not _origin_is_ours(origin, request.headers.get("host")):
            log.warning("api.origin_rejected", origin=origin)
            return _secured(
                JSONResponse(
                    {"detail": "Cross-origin request refused."}, status_code=403
                )
            )

        # 3. Authentication. Three ways to present it, in descending preference:
        #    the session cookie (what the app uses after the first load), the
        #    header (what a script or curl uses), and the launch token in the
        #    query string (the first navigation only, before any JS has run).
        path = request.url.path
        if (
            context.require_token
            and path not in UNAUTHENTICATED_PATHS
            and not _is_app_shell(path)
        ):
            if not _authenticated(request, context):
                return _secured(
                    JSONResponse(
                        {"detail": "Missing or invalid session token."},
                        status_code=401,
                    )
                )

        return _secured(await call_next(request))


def _origin_is_ours(origin: str, host_header: str | None) -> bool:
    """Whether an ``Origin`` names this same server.

    Compared against the request's own ``Host`` rather than a configured value,
    because the port is chosen at launch and may not be the default one.
    """
    from urllib.parse import urlsplit

    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"}:
        return False
    return bool(host_header) and parsed.netloc == host_header


def _authenticated(request: Request, context: ApiContext) -> bool:
    """Whether a request carries a valid session credential."""
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie and context.token.matches(cookie):
        return True

    header = request.headers.get("x-voxframe-token")
    if header and context.token.matches(header):
        return True

    return context.token.matches(request.query_params.get("token"))


def _secured(response: Response) -> Response:
    """Attach the response headers that constrain what a page may do.

    Each one closes a specific door:

    - **Content-Security-Policy** confines the page to its own origin. No
      third-party script, no remote font, no framing. ``'unsafe-inline'`` is
      *not* granted for scripts; the styles allowance exists because the
      bundler inlines a small critical block, and is the one thing here worth
      revisiting if that stops being true.
    - **Referrer-Policy: no-referrer** stops the URL -- which carries the launch
      token on the very first navigation -- from reaching anywhere else.
    - **X-Content-Type-Options** stops a browser guessing that an uploaded file
      is really a script.
    - **X-Frame-Options** keeps the app out of another page's iframe, so a
      clickjacking overlay has nothing to cover.
    """
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


# --- routes -------------------------------------------------------------


def _install_routes(app: FastAPI, context: ApiContext) -> None:
    """Register every route."""

    def ctx() -> ApiContext:
        return context

    def _folders() -> dict[str, Path]:
        from voxframe.config.paths import data_dir, is_source_checkout

        return {
            "library": context.settings.library_path.resolve(),
            "videos": (context.videos_folder or context.store.root).resolve(),
            "data": (
                context.settings.cache_path if is_source_checkout() else data_dir()
            ).resolve(),
        }

    @app.get("/api/folders")
    def folders(context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Where Voxframe keeps things, for Settings (D-156).

        Shown to the person on their own machine; a path is still never taken
        from a request.
        """
        from voxframe.config.userprefs import load_preferences

        places = _folders()
        chosen = load_preferences().library_path
        return {
            **{name: str(path) for name, path in places.items()},
            "library_pending": (
                chosen
                if chosen and Path(chosen).resolve() != places["library"]
                else None
            ),
            "library_from_environment": "VOXFRAME_LIBRARY_PATH" in os.environ,
        }

    @app.post("/api/folders/open")
    def open_folder(request: FolderRequest, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Show one of Voxframe's folders in the file manager."""
        from voxframe.api.desktop import DesktopUnavailable
        from voxframe.api.desktop import open_folder as show

        try:
            show(_folders()[request.which])
        except DesktopUnavailable as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return {"opened": request.which}

    @app.post("/api/folders/library/choose")
    def choose_library(context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Pick a new library folder in the system's own folder window.

        The path comes from that window on this machine, never from the page
        (D-115). Used from the next start: the running app keeps its library,
        so nothing it is rendering moves underneath it.
        """
        from voxframe.api.desktop import DesktopUnavailable, choose_folder
        from voxframe.config.userprefs import load_preferences, save_preferences

        if "VOXFRAME_LIBRARY_PATH" in os.environ:
            raise HTTPException(
                status_code=409,
                detail="The library is set by VOXFRAME_LIBRARY_PATH, which takes precedence.",
            )
        try:
            chosen = choose_folder(
                "Choose a folder for your Voxframe library", _folders()["library"]
            )
        except DesktopUnavailable as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        if chosen is None:
            return {"changed": False}
        preferences = load_preferences()
        preferences.library_path = str(chosen.resolve())
        save_preferences(preferences)
        return {"changed": True, "library_pending": str(chosen.resolve())}

    @app.get("/api/setup/models")
    def model_status(context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """What each model profile needs, what is already here, and progress (D-157)."""
        from voxframe.config.settings import ModelProfile
        from voxframe.model_downloads import hub_cache, is_ready, model_needs

        preferences = load_preferences()
        current = apply_to_settings(context.settings, preferences)
        choices = []
        for profile in ModelProfile:
            needs = model_needs(current.model_copy(update={"profile": profile}))
            ready = [is_ready(need) for need in needs]
            choices.append({
                "profile": profile.value,
                "total_mb": profile.approximate_download_mb,
                "to_download_mb": sum(
                    need.megabytes for need, done in zip(needs, ready, strict=True) if not done
                ),
                "ready": all(ready),
                "models": [
                    {"label": n.label, "purpose": n.purpose, "mb": n.megabytes, "ready": r}
                    for n, r in zip(needs, ready, strict=True)
                ],
            })
        chosen = next(c for c in choices if c["profile"] == current.profile.value)
        return {
            "profile": current.profile.value,
            "chosen": preferences.model_profile or None,
            "profile_from_environment": "VOXFRAME_PROFILE" in os.environ,
            "ready": chosen["ready"],
            "choices": choices,
            "download": context.downloads.snapshot(),
            # Where the libraries really keep them: the app's folder when installed.
            "models_folder": str(hub_cache()),
        }

    @app.post("/api/setup/models/download", status_code=202)
    def download_models(choice: ModelChoice, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Download the chosen profile's models, measuring as they arrive (D-157).

        Nothing is downloaded until the person asks, and the choice is
        remembered -- unless the environment names a profile, which wins.
        """
        from voxframe.config.userprefs import save_preferences
        from voxframe.model_downloads import start_download

        preferences = load_preferences()
        if "VOXFRAME_PROFILE" not in os.environ and preferences.model_profile != choice.profile:
            preferences.model_profile = choice.profile
            save_preferences(preferences)
        start_download(apply_to_settings(context.settings, preferences), context.downloads)
        return context.downloads.snapshot()

    @app.post("/api/updates/check")
    def update_check(context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Ask GitHub for the latest release -- only when the person clicks (D-155).

        A POST, so no page load, prefetch or link can make the call: it happens
        because a button was pressed.
        """
        from voxframe.updates import UpdateError, check_for_update

        try:
            result = check_for_update()
        except UpdateError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "current": result.current,
            "latest": result.latest,
            "newer": result.newer,
            "url": result.url,
        }

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """Liveness, with no secrets in it.

        Deliberately unauthenticated and deliberately uninformative: a launcher
        polls it to know when to open a browser.
        """
        return {"status": "ok", "app": "voxframe"}

    @app.post("/api/session")
    def session(
        request: Request, response: Response, context: ApiContext = Depends(ctx)
    ) -> dict[str, Any]:
        """Exchange the launch token for a session cookie.

        The token arrives in the URL on the very first navigation, because
        nothing has run in the page yet that could set a header. Leaving it
        there would be wrong in several ways at once: it sits in the address
        bar, in history, in any bookmark, and in a screenshot of the window.

        So the page calls this once and then removes it from the URL with
        ``history.replaceState``. The cookie that replaces it is:

        - **HttpOnly**, so no script can read it -- including an injected one.
        - **SameSite=Strict**, so a request originating from any other site
          carries no credential at all. This is a second, independent defence
          against the cross-site case, on top of the Origin check.
        - **Path=/**, session-scoped, and never persisted to disk by us; it
          dies with the browser session, as the token dies with the process.

        Secure is deliberately **not** set: the app is plain HTTP on loopback,
        and a Secure cookie would simply never be stored.
        """
        presented = request.headers.get("x-voxframe-token") or request.query_params.get(
            "token"
        )
        if not context.token.matches(presented):
            raise HTTPException(status_code=401, detail="Invalid session token.")

        response.set_cookie(
            SESSION_COOKIE,
            context.token.value,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return {"authenticated": True}

    @app.get("/api/settings")
    def read_settings(context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """The user's own settings, with keys masked.

        A stored key is never sent back in full. It can be replaced but not
        read, which is the contract a password field has.
        """
        preferences = load_preferences()
        return {
            "sourcing": {
                "consent": preferences.sourcing_consent,
                "has_been_asked": preferences.has_been_asked,
                "enabled": sourcing_active(context.settings, preferences),
                "consent_version": preferences.consent_version,
                "current_version": CONSENT_VERSION,
            },
            "api_keys": preferences.masked_keys(),
            "theme": preferences.theme,
            "adapters": sorted(KEY_FIELDS),
            "environment_keys": sorted(
                name
                for name, field_name in KEY_FIELDS.items()
                if getattr(context.settings, field_name, None)
            ),
        }

    @app.put("/api/settings")
    def write_settings(
        update: SettingsUpdate, context: ApiContext = Depends(ctx)
    ) -> dict[str, Any]:
        """Record consent and API keys.

        Keys go to the user's own config directory, never the repository
        (D-113). An empty string clears a key; omitting one leaves it alone, so
        the UI never has to send a key back to change something else.
        """
        preferences = load_preferences()

        if update.sourcing_consent is not None:
            preferences.sourcing_consent = update.sourcing_consent
            preferences.consent_version = CONSENT_VERSION
        if update.theme is not None:
            preferences.theme = update.theme

        for name, value in (update.api_keys or {}).items():
            if name not in KEY_FIELDS:
                raise HTTPException(
                    status_code=422, detail=f"Unknown adapter {name!r}."
                )
            if value:
                preferences.api_keys[name] = value.strip()
            else:
                preferences.api_keys.pop(name, None)

        save_preferences(preferences)

        return {
            "sourcing": {
                "consent": preferences.sourcing_consent,
                "has_been_asked": preferences.has_been_asked,
                "enabled": sourcing_active(context.settings, preferences),
            },
            "api_keys": preferences.masked_keys(),
            "theme": preferences.theme,
        }

    @app.post("/api/settings/check-key")
    def check_key(request: KeyCheck) -> dict[str, Any]:
        """Test one API key with a single minimal request.

        Answers only "does this work", never echoing the key. A user who pastes
        a key with a trailing space should find out here, not after a render
        quietly produced gradients.
        """
        if request.adapter not in KEY_FIELDS:
            raise HTTPException(
                status_code=422, detail=f"Unknown adapter {request.adapter!r}."
            )

        key = request.key.strip()
        if not key:
            # A stored key can be re-tested without sending it back up.
            key = load_preferences().api_keys.get(request.adapter, "")
        if not key:
            raise HTTPException(status_code=422, detail="No key to check.")

        valid, detail = _probe_key(request.adapter, key)
        return {"adapter": request.adapter, "valid": valid, "detail": detail}

    @app.get("/api/capabilities")
    def capabilities(context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """What this installation can do.

        The web app needs this before its first screen: whether FFmpeg is
        present, which styles exist, whether a library has anything in it, and
        whether sourcing is available. A first-run user with an empty library
        gets told so here rather than after waiting for a render (owner's
        decision 6).
        """
        from voxframe.config.style import BUILTIN_TEMPLATES
        from voxframe.render.encode.probe import probe_capabilities

        try:
            caps = probe_capabilities()
            ffmpeg = {
                "available": True,
                "version": caps.version,
                "has_libass": caps.has_libass,
                "has_xfade": caps.has_xfade,
            }
        except Exception as exc:
            ffmpeg = {"available": False, "error": str(exc)}

        return {
            "ffmpeg": ffmpeg,
            "styles": [
                {"name": name, "description": template.description}
                for name, template in sorted(BUILTIN_TEMPLATES.items())
            ],
            "aspects": [str(a) for a in AspectRatio],
            "qualities": [str(q) for q in QualityPreset],
            "library": _library_summary(context.settings),
            "sourcing": _sourcing_summary(),
            "profile": str(context.settings.profile),
            "music_component": _music_component_summary(),
            "score": _score_summary(),
        }

    @app.post("/api/uploads")
    async def upload(
        file: UploadFile = File(...),
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """Accept an audio file and return an id to render it by.

        The client never names a path. It uploads bytes, gets an opaque id, and
        passes that id to the render route — so there is no request in which a
        client supplies a filesystem path for reading.
        """
        original = Path(file.filename or "audio")
        suffix = original.suffix.lower()
        if suffix not in AUDIO_SUFFIXES:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"Unsupported file type {suffix or '(none)'}. "
                    f"Accepted: {', '.join(sorted(AUDIO_SUFFIXES))}"
                ),
            )

        upload_id = _new_upload_id()
        directory = context.store.root / "uploads" / upload_id
        directory.mkdir(parents=True, exist_ok=True)

        # The stored name is sanitised: the client's filename is used only for
        # its suffix and for display, never as a path component.
        target = directory / f"source{suffix}"

        written = 0
        try:
            with target.open("wb") as handle:
                while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"File exceeds the "
                                f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."
                            ),
                        )
                    handle.write(chunk)
        except HTTPException:
            shutil.rmtree(directory, ignore_errors=True)
            raise

        # The name as the person knows it, for credits only (D-148).
        (directory / "name.txt").write_text(
            _display_name(original.name), encoding="utf-8"
        )
        log.info("api.upload", upload=upload_id, bytes=written, suffix=suffix)

        return {
            "upload_id": upload_id,
            "name": original.name,
            "bytes": written,
            "duration_seconds": _probe_duration(target),
            # Whether "Use my video" applies (D-192): a picture to show.
            "has_video": _probe_has_video(target),
        }

    @app.post("/api/jobs", status_code=202)
    def submit(
        request: RenderRequest, context: ApiContext = Depends(ctx)
    ) -> dict[str, Any]:
        """Queue a render and return its job.

        202 rather than 200: the work has been accepted, not done. The client
        follows the job's events stream from here.
        """
        audio = _upload_audio(context, request.upload_id)
        options = _job_options(request, audio, context)

        job = context.store.create(
            audio_name=_upload_name(audio), options=request.model_dump()
        )
        context.store.submit(job, _renderer(context, options))

        return job.snapshot()

    @app.get("/api/jobs")
    def list_jobs(context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Every job, newest first. Survives a restart."""
        return {"jobs": [job.snapshot() for job in context.store.all_jobs()]}

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        job = context.store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such job.")
        return job.snapshot()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Ask a render to stop at its next stage boundary.

        Cooperative: FFmpeg cannot be interrupted mid-segment, and killing it
        would leave a partial file that the segment cache must not keep.
        """
        if not context.store.request_cancel(job_id):
            raise HTTPException(
                status_code=404, detail="No such job, or it already finished."
            )
        return {"cancelling": job_id}

    @app.get("/api/jobs/{job_id}/events")
    async def events(
        job_id: str, context: ApiContext = Depends(ctx)
    ) -> StreamingResponse:
        """Progress as server-sent events.

        One direction, server to client, which is exactly what progress is —
        websockets would add a protocol for no gain. History is replayed first
        so a reloaded tab shows what already happened rather than appearing
        stuck.
        """
        job = context.store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such job.")

        return StreamingResponse(
            _event_stream(context, job_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Without this a proxy may buffer the stream and deliver every
                # event at the end, which is worse than no progress at all.
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/jobs/{job_id}/plan")
    def job_plan(job_id: str, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """The scene plan as JSON, for the filmstrip editor.

        Served from the file the render wrote, so what the browser edits is the
        same record the renderer used (D-011).
        """
        path = context.store.artifact_path(job_id, "plan")
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="No plan for this job.")
        _check_sandbox(context, path)
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return payload

    @app.get("/api/jobs/{job_id}/scenes/{index}/thumbnail")
    def scene_thumbnail(
        job_id: str, index: int, context: ApiContext = Depends(ctx)
    ) -> Response:
        """A small preview of one scene's imagery.

        The filmstrip needs to show what each scene looks like, and the asset
        is a file on disk that the browser cannot reach. Rather than exposing
        the library as static files -- which would put every path decision in
        the hands of a URL -- the scene index selects the asset and the server
        resolves it.

        **Two checks, not one.** The asset path comes from the plan, which is a
        file a user may have edited by hand, so it is re-checked against the
        sandbox exactly like any client-supplied path (D-115). A plan pointing
        at ``~/.ssh/id_rsa`` gets a 403, not a thumbnail.

        Thumbnails are cached on disk next to the job: generating one costs an
        FFmpeg invocation, and a 148-scene filmstrip would otherwise pay that
        every time the page is opened.
        """
        plan_path = context.store.artifact_path(job_id, "plan")
        if plan_path is None or not plan_path.is_file():
            raise HTTPException(status_code=404, detail="No plan for this job.")
        _check_sandbox(context, plan_path)

        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        scenes = payload.get("scenes", [])
        if not 0 <= index < len(scenes):
            raise HTTPException(status_code=404, detail="No such scene.")

        footage = payload.get("footage")
        layout = scenes[index].get("layout") or {}
        if (
            footage
            and layout.get("kind", "full") != "full"
            and scenes[index].get("footage_start") is not None
        ):
            # The speaker and the picture together, as the render puts them
            # (D-197).
            return FileResponse(
                _layout_thumbnail(context, job_id, payload, index), media_type="image/jpeg"
            )

        if (
            footage
            and scenes[index].get("shot") == "speaker"
            and scenes[index].get("footage_start") is not None
        ):
            # What the scene shows: the speaker, a moment into it (D-192).
            try:
                recording = resolve_within(Path(footage["path"]), context.allowed_paths)
            except PathOutsideSandbox as exc:
                raise HTTPException(
                    status_code=403,
                    detail="That scene's recording is outside the allowed directories.",
                ) from exc
            if not recording.is_file():
                raise HTTPException(status_code=404, detail="The recording is missing.")
            seconds = (scenes[index]["end_frame"] - scenes[index]["start_frame"]) / float(
                payload.get("fps") or 30.0
            )
            at = float(scenes[index]["footage_start"]) + float(
                footage.get("audio_offset") or 0.0
            ) + min(0.5, seconds / 2)
            return FileResponse(
                _thumbnail_for(context, job_id, recording, at=at), media_type="image/jpeg"
            )

        asset = scenes[index].get("asset")
        if not asset or not asset.get("path"):
            raise HTTPException(status_code=404, detail="Scene has no imagery.")

        source = Path(asset["path"])
        # The plan is a file a user may have edited, so its paths are
        # untrusted input and get the same treatment as any other.
        try:
            resolved = resolve_within(source, context.allowed_paths)
        except PathOutsideSandbox as exc:
            log.warning("api.thumbnail_rejected", job=job_id, scene=index)
            raise HTTPException(
                status_code=403,
                detail="That scene's image is outside the allowed directories.",
            ) from exc

        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="Image file is missing.")

        thumbnail = _thumbnail_for(context, job_id, resolved)
        return FileResponse(thumbnail, media_type="image/jpeg")

    @app.get("/api/jobs/{job_id}/scenes/{index}/candidates/{asset_id}/thumbnail")
    def candidate_thumbnail(
        job_id: str, index: int, asset_id: str, context: ApiContext = Depends(ctx)
    ) -> Response:
        """A preview of one of a scene's recorded candidates.

        The candidate is named by the id the plan recorded, never by a path, and
        its file is sandbox-checked like any other (D-115, D-122). This is what
        lets "use this image anyway" show the image *before* the person commits
        to it.
        """
        plan = _load_job_plan(context, job_id)
        scene = _scene_or_404(plan, index)
        candidates = {asset.id: asset for asset in scene.alternatives}
        candidates.update({asset.id: asset for asset in scene.near_misses})
        asset = candidates.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="No such candidate.")

        resolved = _asset_file(context, asset.path)
        return FileResponse(
            _thumbnail_for(context, job_id, resolved), media_type="image/jpeg"
        )

    @app.put("/api/jobs/{job_id}/scenes/{index}/image")
    def edit_image(
        job_id: str,
        index: int,
        edit: ImageEdit,
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """Choose a scene's image: a runner-up, a near miss, or none at all.

        The edit is saved to the plan immediately and shows in the filmstrip
        straight away; it reaches the video when the person re-renders. Only a
        recorded candidate can be chosen -- the request names an asset id, and
        a path is never accepted (D-115).
        """
        from voxframe.plan.editing import EditError, choose_image, remove_image

        job, plan_path, plan = _editable_plan(context, job_id)
        try:
            if edit.action == "choose":
                if not edit.asset_id:
                    raise EditError("Say which image to use.")
                edited = choose_image(plan, index, edit.asset_id)
            else:
                edited = remove_image(plan, index)
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        saved = _save_plan(edited, plan_path, "a picture")
        context.store.set_pending(job, saved.pending)
        return {"scene": edited.scenes[index].model_dump(mode="json"),
                "pending_edits": job.summary.get("pending_edits", 0)}

    @app.get("/api/jobs/{job_id}/mix")
    def get_mix(job_id: str, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """The video's sound settings, the choices for them, and the last checks."""
        job = context.store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such job.")
        plan = _load_job_plan(context, job_id)
        return _mix_state(job, plan.audio_mix, context)

    @app.put("/api/jobs/{job_id}/mix")
    def edit_mix(job_id: str, mix: AudioMix, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Save the video's sound settings (D-171).

        Like every other edit, it waits for "Update video"; updating then
        re-renders only the sound, because the pictures come from the cache.
        """
        job, plan_path, plan = _editable_plan(context, job_id)
        saved = _save_plan(plan.model_copy(update={"audio_mix": mix}), plan_path, "the mix")
        context.store.set_pending(job, saved.pending)
        state = _mix_state(job, mix, context)
        return {**state, "pending_edits": job.summary.get("pending_edits", 0)}

    @app.put("/api/jobs/{job_id}/music")
    def edit_music(
        job_id: str, edit: MusicEdit, context: ApiContext = Depends(ctx)
    ) -> dict[str, Any]:
        """Change the video's music: none, a generated score, or the person's own track."""
        from voxframe.music.score import styles
        from voxframe.plan.score_choice import ScoreChoice, new_seed

        job, plan_path, plan = _editable_plan(context, job_id)
        if plan.music_path:
            # Kept, so the Sound card can switch back to it.
            context.store.remember(
                job, "kept_track", {"path": plan.music_path, "credit": plan.music_credit}
            )
        update: dict[str, Any] = {"music_path": "", "music_credit": "", "score": None}
        if edit.choice == "own" and edit.upload_id:
            track = _named_for_credits(_upload_audio(context, edit.upload_id))
            credit = " ".join((edit.credit or "").split())
            update.update(music_path=str(track), music_credit=credit)
            context.store.remember(job, "kept_track", {"path": str(track), "credit": credit})
        elif edit.choice == "own":
            kept = job.summary.get("kept_track")
            if not isinstance(kept, dict) or not Path(str(kept.get("path", ""))).is_file():
                raise HTTPException(
                    status_code=409, detail="This video has no track of yours to go back to."
                )
            credit = str(kept.get("credit", ""))
            if edit.credit is not None:
                credit = " ".join(edit.credit.split())
                context.store.remember(
                    job, "kept_track", {"path": str(kept["path"]), "credit": credit}
                )
            update.update(music_path=str(kept["path"]), music_credit=credit)
        elif edit.choice == "score":
            if not edit.style or edit.style not in styles():
                raise HTTPException(
                    status_code=422, detail=f"No music style called {edit.style!r}."
                )
            if edit.seed is not None:
                seed = edit.seed
            elif plan.score is not None and not edit.new_variation:
                seed = plan.score.seed
            else:
                seed = new_seed()
            update["score"] = ScoreChoice(style=edit.style, seed=seed, intensity=edit.intensity)
        if edit.forget_track:
            if edit.choice == "own":
                raise HTTPException(
                    status_code=422, detail="A track cannot be both used and removed."
                )
            context.store.remember(job, "kept_track", None)
        saved = _save_plan(plan.model_copy(update=update), plan_path, "the music")
        context.store.set_pending(job, saved.pending)
        state = _mix_state(job, plan.audio_mix, context)
        return {**state, "pending_edits": job.summary.get("pending_edits", 0)}

    @app.get("/api/score/styles/{name}/preview")
    def score_preview(name: str, context: ApiContext = Depends(ctx)) -> FileResponse:
        """A short sample of a score style, made once from the samples and kept (D-179)."""
        from voxframe.music.score import ScoreUnavailable, style_preview, styles

        if name not in styles():
            raise HTTPException(status_code=404, detail="No such music style.")
        try:
            path = style_preview(name, context.settings.cache_path / "score-previews")
        except ScoreUnavailable as exc:
            raise HTTPException(
                status_code=409, detail=f"The preview cannot be made: {exc}."
            ) from exc
        return FileResponse(path, media_type="audio/wav")

    @app.post("/api/jobs/{job_id}/mix/preview")
    def preview_mix(
        job_id: str, request: MixPreview, context: ApiContext = Depends(ctx)
    ) -> FileResponse:
        """A few seconds of the mix at these settings, made from the kept stems."""
        from voxframe.render.audio.mixdown import Stems, preview
        from voxframe.render.compose.from_plan import stems_path

        job = context.store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such job.")
        video = context.store.artifact_path(job_id, "video")
        stems_file = stems_path(video) if video is not None else None
        if stems_file is None or not stems_file.is_file():
            raise HTTPException(
                status_code=409,
                detail="Update this video once to hear previews of its sound.",
            )
        stems = Stems.load(stems_file)
        if request.music_upload_id or request.kept_track:
            stems = _with_track(stems, _preview_track(job, request, context), request, context)
        kept = [stems.voice, stems.music, stems.voice_polished]
        if any(path is not None and not path.is_file() for path in kept):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This video's sound files were cleared; update the video to "
                    "make them again."
                ),
            )
        folder = stems_file.parent / ".previews"
        folder.mkdir(exist_ok=True)
        for old in folder.glob("*.wav"):
            old.unlink(missing_ok=True)
        output = folder / f"{secrets.token_hex(8)}.wav"
        preview(
            stems, request.mix, request.start, request.seconds, output,
            voice_only=request.voice_only,
        )
        return FileResponse(output, media_type="audio/wav")

    @app.put("/api/jobs/{job_id}/scenes/{index}/motion")
    def edit_motion(
        job_id: str,
        index: int,
        edit: MotionEdit,
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """Turn a scene's camera movement on or off (D-154)."""
        from voxframe.plan.editing import EditError, set_motion

        job, plan_path, plan = _editable_plan(context, job_id)
        try:
            edited = set_motion(plan, index, edit.on)
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        saved = _save_plan(edited, plan_path, "the camera movement")
        context.store.set_pending(job, saved.pending)
        return {"scene": edited.scenes[index].model_dump(mode="json"),
                "pending_edits": job.summary.get("pending_edits", 0)}

    @app.put("/api/jobs/{job_id}/scenes/{index}/shot")
    def edit_shot(
        job_id: str,
        index: int,
        edit: ShotEdit,
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """Show the speaker or the scene's picture (D-192)."""
        from voxframe.plan.editing import EditError, set_shot
        from voxframe.plan.scene_plan import Shot

        job, plan_path, plan = _editable_plan(context, job_id)
        try:
            edited = set_shot(plan, index, Shot(edit.shot))
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        saved = _save_plan(
            edited, plan_path, "you on screen" if edit.shot == "speaker" else "a picture"
        )
        context.store.set_pending(job, saved.pending)
        return {"scene": edited.scenes[index].model_dump(mode="json"),
                "pending_edits": job.summary.get("pending_edits", 0)}

    @app.get("/api/jobs/{job_id}/captions")
    def job_captions(job_id: str, context: ApiContext = Depends(ctx)) -> Response:
        """The captions as the video burns them in, built from the plan as it
        stands now: the studio draws them over the uncaptioned picture, so a
        change shows the moment it is made (D-196)."""
        from voxframe.config.style import get_template
        from voxframe.render.compose.from_plan import plan_captions

        job = context.store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such job.")
        plan = _load_job_plan(context, job_id)
        width, height = job.summary.get("width"), job.summary.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            raise HTTPException(status_code=404, detail="This video has not been made yet.")
        document = plan_captions(plan, get_template(plan.style or None), width, height)
        return Response(
            document, media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/jobs/{job_id}/captions/style")
    def job_caption_style(job_id: str, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """The captions' look as chosen, over the template's (D-196)."""
        from voxframe.config.style import CaptionPosition, get_template

        job = context.store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such job.")
        plan = _load_job_plan(context, job_id)
        template = get_template(plan.style or None).captions
        chosen = plan.captions.apply(template)
        # Where the middle of a two-line caption sits when nobody has moved
        # it: where the studio's handle starts.
        width = job.summary.get("width") or 1080
        height = job.summary.get("height") or 1920
        anchor = chosen.anchor_y
        if anchor is None:
            margin = chosen.margin_vertical_px(width, height)
            lines = chosen.font_size_px(height) * min(2, chosen.max_lines) / 2
            anchor = {
                CaptionPosition.TOP: (margin + lines) / height,
                CaptionPosition.CENTER: 0.5,
            }.get(chosen.position, (height - margin - lines) / height)
        return {
            "choice": plan.captions.model_dump(mode="json"),
            "template": {
                "animation": template.effective_animation.value,
                "transition": template.transition.value,
            },
            "effective": {
                "animation": chosen.effective_animation.value,
                "transition": chosen.transition.value,
                "anchor_y": round(anchor, 4),
                "placed": chosen.anchor_y is not None,
                "uppercase": chosen.uppercase,
            },
        }

    @app.put("/api/jobs/{job_id}/captions")
    def edit_captions(
        job_id: str, edit: CaptionsEdit, context: ApiContext = Depends(ctx)
    ) -> dict[str, Any]:
        """Choose how the whole video's captions look and move (D-196)."""
        from voxframe.config.style import CaptionAnimation, CaptionTransition
        from voxframe.plan.caption_choice import CaptionChoice
        from voxframe.plan.editing import set_captions

        job, plan_path, plan = _editable_plan(context, job_id)
        try:
            choice = CaptionChoice(
                animation=CaptionAnimation(edit.animation) if edit.animation else None,
                transition=CaptionTransition(edit.transition) if edit.transition else None,
                anchor_y=edit.anchor_y,
                size=edit.size,
                uppercase=edit.uppercase,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Unknown caption style.") from exc
        saved = _save_plan(set_captions(plan, choice), plan_path, "the captions")
        context.store.set_pending(job, saved.pending)
        return {"captions": choice.model_dump(mode="json"), **_history_state(plan_path)}

    @app.put("/api/jobs/{job_id}/scenes/{index}/captions")
    def edit_scene_captions(
        job_id: str,
        index: int,
        edit: SceneCaptionsEdit,
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """One scene's own caption animation and emphasised words (D-196)."""
        from voxframe.config.style import CaptionAnimation
        from voxframe.plan.editing import EditError, set_scene_captions

        job, plan_path, plan = _editable_plan(context, job_id)
        try:
            animation = CaptionAnimation(edit.animation) if edit.animation else None
            edited = set_scene_captions(plan, index, animation, tuple(edit.emphasis))
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Unknown caption style.") from exc
        saved = _save_plan(edited, plan_path, "a scene's captions")
        context.store.set_pending(job, saved.pending)
        return {"scene": edited.scenes[index].model_dump(mode="json"),
                **_history_state(plan_path)}

    @app.put("/api/jobs/{job_id}/scenes/{index}/layout")
    def edit_layout(
        job_id: str,
        index: int,
        edit: LayoutEdit,
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """Split the frame, or inset one part in the other (D-197)."""
        from pydantic import ValidationError

        from voxframe.plan.editing import EditError, set_layout
        from voxframe.plan.scene_layout import SceneLayout

        job, plan_path, plan = _editable_plan(context, job_id)
        try:
            layout = SceneLayout.model_validate(edit.model_dump())
            edited = set_layout(plan, index, layout)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="That layout is out of range.") from exc
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        label = {"full": "a full frame", "split": "a split screen", "inset": "an inset"}
        saved = _save_plan(edited, plan_path, label[layout.kind.value])
        context.store.set_pending(job, saved.pending)
        return {"scene": edited.scenes[index].model_dump(mode="json"),
                **_history_state(plan_path)}

    # --- pop-ups (D-198) ---------------------------------------------------

    @app.get("/api/stickers")
    def sticker_list() -> list[dict[str, Any]]:
        """The bundled stickers, with the words that suggest each."""
        from voxframe.render.compose.stickers import stickers

        return [dict(entry) for entry in stickers()]

    @app.get("/api/stickers/{name}")
    def sticker_image(name: str) -> Response:
        """One sticker's picture. Only a name in the set selects a file."""
        from voxframe.render.compose.stickers import sticker_path

        path = sticker_path(name.removesuffix(".png"))
        if path is None:
            raise HTTPException(status_code=404, detail="No such sticker.")
        return FileResponse(
            path, media_type="image/png", headers={"Cache-Control": "max-age=86400"}
        )

    def _overlay_response(
        job: Job, plan_path: Path, plan: ScenePlan, label: str
    ) -> dict[str, Any]:
        saved = _save_plan(plan, plan_path, label)
        context.store.set_pending(job, saved.pending)
        return {**_overlays_state(plan), **_history_state(plan_path)}

    def _overlays_state(plan: ScenePlan) -> dict[str, Any]:
        return {
            "overlays": [o.model_dump(mode="json") for o in plan.overlays],
            "progress_bar": plan.progress_bar,
            # When each is on screen: the studio previews stickers and
            # pictures itself, libass draws the rest (D-198).
            "times": {o.id: list(plan.overlay_times(o)) for o in plan.overlays},
        }

    @app.get("/api/jobs/{job_id}/overlays")
    def job_overlays(job_id: str, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        return _overlays_state(_load_job_plan(context, job_id))

    def _overlay_from(edit: OverlayEdit, overlay_id: str, image_path: str = "") -> Any:
        from pydantic import ValidationError

        from voxframe.plan.overlays import Overlay

        try:
            return Overlay.model_validate(
                {**edit.model_dump(), "id": overlay_id, "image_path": image_path}
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="That pop-up is out of range.") from exc

    @app.get("/api/jobs/{job_id}/overlays/suggestions")
    def overlay_suggestions(
        job_id: str, context: ApiContext = Depends(ctx)
    ) -> list[dict[str, Any]]:
        """Pop-ups worth adding, each with the word that prompted it. Nothing
        is added until the person chooses one."""
        from dataclasses import asdict

        from voxframe.plan.popup_suggestions import suggest_popups

        plan = _load_job_plan(context, job_id)
        return [asdict(suggestion) for suggestion in suggest_popups(plan)]

    @app.post("/api/jobs/{job_id}/overlays")
    def add_overlay_route(
        job_id: str, edit: OverlayEdit, context: ApiContext = Depends(ctx)
    ) -> dict[str, Any]:
        import uuid

        from voxframe.plan.editing import EditError, add_overlay

        if edit.kind == "image":
            raise HTTPException(status_code=422, detail="Add a picture by choosing its file.")
        job, plan_path, plan = _editable_plan(context, job_id)
        overlay = _overlay_from(edit, uuid.uuid4().hex[:10])
        try:
            edited = add_overlay(plan, overlay)
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result = _overlay_response(job, plan_path, edited, "a pop-up")
        return {**result, "id": overlay.id}

    @app.put("/api/jobs/{job_id}/overlays/{overlay_id}")
    def update_overlay_route(
        job_id: str, overlay_id: str, edit: OverlayEdit, context: ApiContext = Depends(ctx)
    ) -> dict[str, Any]:
        from voxframe.plan.editing import EditError, update_overlay

        job, plan_path, plan = _editable_plan(context, job_id)
        current = next((o for o in plan.overlays if o.id == overlay_id), None)
        if current is None:
            raise HTTPException(status_code=404, detail="That pop-up is no longer there.")
        if edit.kind != current.kind.value:
            raise HTTPException(status_code=422, detail="A pop-up keeps its kind.")
        overlay = _overlay_from(edit, overlay_id, current.image_path)
        try:
            edited = update_overlay(plan, overlay)
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _overlay_response(job, plan_path, edited, "a pop-up")

    @app.delete("/api/jobs/{job_id}/overlays/{overlay_id}")
    def remove_overlay_route(
        job_id: str, overlay_id: str, context: ApiContext = Depends(ctx)
    ) -> dict[str, Any]:
        from voxframe.plan.editing import EditError, remove_overlay

        job, plan_path, plan = _editable_plan(context, job_id)
        try:
            edited = remove_overlay(plan, overlay_id)
        except EditError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _overlay_response(job, plan_path, edited, "a pop-up taken out")

    @app.post("/api/jobs/{job_id}/overlays/image")
    async def add_image_overlay(
        job_id: str,
        scene: int = Form(...),
        word: int = Form(0),
        file: UploadFile = File(...),
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """A picture of the person's own, popping up on a word. Stored in the
        job's own folder under a name the server chooses, and checked to be a
        real image first (D-198)."""
        import uuid

        from voxframe.plan.editing import EditError, add_overlay

        job, plan_path, plan = _editable_plan(context, job_id)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            raise HTTPException(status_code=415, detail="Use a JPEG, PNG or WebP image.")
        overlay_id = uuid.uuid4().hex[:10]
        directory = context.store.job_directory(job.id) / "own"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"popup-{overlay_id}{suffix}"
        written = 0
        with target.open("wb") as handle:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_IMAGE_BYTES:
                    handle.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="Images are limited to 50 MB.")
                handle.write(chunk)
        try:
            from PIL import Image

            with Image.open(target) as image:
                image.verify()
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(
                status_code=415, detail="That file is not a readable image."
            ) from exc
        edit = OverlayEdit(kind="image", scene=scene, word=word, size=0.4, y=0.32)
        overlay = _overlay_from(edit, overlay_id, str(target.resolve()))
        try:
            edited = add_overlay(plan, overlay)
        except EditError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result = _overlay_response(job, plan_path, edited, "a picture pop-up")
        return {**result, "id": overlay_id}

    @app.get("/api/jobs/{job_id}/overlays/{overlay_id}/image")
    def overlay_image(
        job_id: str, overlay_id: str, context: ApiContext = Depends(ctx)
    ) -> Response:
        """A picture pop-up's file, for the studio's preview."""
        plan = _load_job_plan(context, job_id)
        overlay = next((o for o in plan.overlays if o.id == overlay_id), None)
        if overlay is None or not overlay.image_path:
            raise HTTPException(status_code=404, detail="No such picture.")
        try:
            path = resolve_within(Path(overlay.image_path), context.allowed_paths)
        except PathOutsideSandbox as exc:
            raise HTTPException(
                status_code=403, detail="That picture is outside the allowed directories."
            ) from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="The picture is missing.")
        return FileResponse(path)

    @app.put("/api/jobs/{job_id}/progress-bar")
    def edit_progress_bar(
        job_id: str, edit: ProgressEdit, context: ApiContext = Depends(ctx)
    ) -> dict[str, Any]:
        from voxframe.plan.editing import set_progress_bar

        job, plan_path, plan = _editable_plan(context, job_id)
        label = "the progress bar" if edit.on else "no progress bar"
        return _overlay_response(job, plan_path, set_progress_bar(plan, edit.on), label)

    @app.put("/api/jobs/{job_id}/scenes/{index}/caption")
    def edit_caption(
        job_id: str,
        index: int,
        edit: CaptionEdit,
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """Correct what a scene's captions say.

        The original transcript is kept beside the correction, never replaced,
        and word timings are re-derived from it when the video is rendered
        (D-062). Sending back exactly what was heard removes the correction.
        """
        from voxframe.plan.editing import EditError, correct_caption

        job, plan_path, plan = _editable_plan(context, job_id)
        try:
            edited = correct_caption(plan, index, edit.text)
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        saved = _save_plan(edited, plan_path, "a caption")
        context.store.set_pending(job, saved.pending)
        return {"scene": edited.scenes[index].model_dump(mode="json"),
                "pending_edits": job.summary.get("pending_edits", 0)}

    @app.post("/api/jobs/{job_id}/scenes/{index}/image/own")
    async def own_image(
        job_id: str,
        index: int,
        file: UploadFile = File(...),
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """Use a photograph or a video clip the person supplies for one scene.

        Stored inside the job's own directory under a name the server chooses;
        the client's filename is used only for its suffix. Checked to be a real
        image, or a real video, before it is accepted, since a renamed file of
        any kind would otherwise reach FFmpeg. Provenance defaults to the
        person's own work (owner's decision 4) and is never blank (D-035). A
        clip plays in the scene, above the speaker in a split screen (D-197).
        """
        from voxframe.plan.editing import EditError, use_image

        job, plan_path, plan = _editable_plan(context, job_id)

        suffix = Path(file.filename or "").suffix.lower()
        is_clip = suffix in CLIP_SUFFIXES
        if suffix not in IMAGE_SUFFIXES and not is_clip:
            raise HTTPException(
                status_code=415,
                detail=(
                    "Use a JPEG, PNG or WebP image, or an MP4, WebM or MOV clip "
                    f"(got {suffix or 'no extension'})."
                ),
            )
        limit = MAX_CLIP_BYTES if is_clip else MAX_IMAGE_BYTES

        import uuid

        asset_id = f"own-{uuid.uuid4().hex[:16]}"
        directory = context.store.job_directory(job.id) / "own"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{asset_id}{suffix}"

        written = 0
        with target.open("wb") as handle:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > limit:
                    handle.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"{'Clips' if is_clip else 'Images'} are limited to "
                            f"{limit // (1024 * 1024)} MB."
                        ),
                    )
                handle.write(chunk)

        from voxframe.plan.scene_plan import PlanAsset

        if is_clip:
            from voxframe.models.asset import AssetKind
            from voxframe.render.encode.probe import probe_footage

            try:
                info = probe_footage(target)
            except Exception:
                info = None
            if info is None or info.duration <= 0:
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=415, detail="That file is not a playable video.")
            asset = PlanAsset(
                id=asset_id,
                path=str(target.resolve()),
                width=info.width,
                height=info.height,
                kind=AssetKind.VIDEO,
                duration=info.duration,
                license_name="Own work",
                license_author="You",
                license_source="Your own video",
            )
            try:
                edited = use_image(plan, index, asset)
            except EditError as exc:
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            saved = _save_plan(edited, plan_path, "your clip")
            context.store.set_pending(job, saved.pending)
            return {"scene": edited.scenes[index].model_dump(mode="json"),
                    "pending_edits": job.summary.get("pending_edits", 0)}

        try:
            from PIL import Image

            with Image.open(target) as image:
                image.verify()
            with Image.open(target) as image:
                width, height = image.size
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(
                status_code=415, detail="That file is not a readable image."
            ) from exc

        asset = PlanAsset(
            id=asset_id,
            path=str(target.resolve()),
            width=width,
            height=height,
            license_name="Own work",
            license_author="You",
            license_source="Your own photo",
        )

        try:
            edited = use_image(plan, index, asset)
        except EditError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        saved = _save_plan(edited, plan_path, "your photo")
        context.store.set_pending(job, saved.pending)
        return {"scene": edited.scenes[index].model_dump(mode="json"),
                "pending_edits": job.summary.get("pending_edits", 0)}

    def _card_edit_response(
        context: ApiContext, job: Job, plan_path: Path, edited: ScenePlan
    ) -> dict[str, Any]:
        saved = _save_plan(edited, plan_path, "a card")
        context.store.set_pending(job, saved.pending)
        return {"plan": json.loads(edited.to_json()),
                "pending_edits": job.summary.get("pending_edits", 0)}

    @app.put("/api/jobs/{job_id}/scenes/{index}/card")
    def edit_card(
        job_id: str,
        index: int,
        edit: CardText,
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """Change what a title or chapter card says (D-145)."""
        from voxframe.plan.editing import EditError, edit_card_text

        job, plan_path, plan = _editable_plan(context, job_id)
        try:
            edited = edit_card_text(plan, index, edit.text)
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _card_edit_response(context, job, plan_path, edited)

    @app.delete("/api/jobs/{job_id}/scenes/{index}/card")
    def delete_card(
        job_id: str, index: int, context: ApiContext = Depends(ctx)
    ) -> dict[str, Any]:
        """Take a card out. Every later scene moves up, so the whole plan is
        returned: scene numbers change."""
        from voxframe.plan.editing import EditError, remove_card

        job, plan_path, plan = _editable_plan(context, job_id)
        try:
            edited = remove_card(plan, index)
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _card_edit_response(context, job, plan_path, edited)

    @app.post("/api/jobs/{job_id}/cards")
    def add_card(
        job_id: str, card: NewCard, context: ApiContext = Depends(ctx)
    ) -> dict[str, Any]:
        """Add a title, or start a chapter before a scene (D-145)."""
        from voxframe.plan.editing import EditError, add_chapter, add_title

        job, plan_path, plan = _editable_plan(context, job_id)
        try:
            if card.kind == "title":
                edited = add_title(plan, card.text)
            else:
                if card.before is None:
                    raise EditError("Say which scene the chapter starts before.")
                edited = add_chapter(plan, card.before, card.text)
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _card_edit_response(context, job, plan_path, edited)

    @app.post("/api/jobs/{job_id}/scenes/{index}/search")
    def search_online(
        job_id: str,
        index: int,
        request: ImageSearch,
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """Search the image services for one scene (D-142).

        Only when the person has agreed to online search and a key is set:
        the same rule as automatic sourcing, so the settings screen means what
        it says. Each result comes back under a token; the browser never sees
        or sends a URL.
        """
        from voxframe.sourcing.manual import SearchError, search

        preferences = load_preferences()
        if not sourcing_active(context.settings, preferences):
            raise HTTPException(
                status_code=409,
                detail="Online search is off. Turn it on in Settings to use it.",
            )
        plan = _load_job_plan(context, job_id)
        _scene_or_404(plan, index)

        try:
            found, failed = search(
                request.query, plan, apply_to_settings(context.settings, preferences)
            )
        except SearchError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        tokens = context.search_results.remember(job_id, found)
        return {
            "results": [
                {
                    "token": token,
                    "title": candidate.title,
                    "author": candidate.license.author,
                    "license": candidate.license.name,
                    "source": candidate.license.source,
                    "width": candidate.width,
                    "height": candidate.height,
                }
                for token, candidate in zip(tokens, found, strict=True)
            ],
            "failed_sources": failed,
        }

    @app.get("/api/jobs/{job_id}/search/{token}/preview")
    def search_preview(
        job_id: str, token: str, context: ApiContext = Depends(ctx)
    ) -> Response:
        """A small preview of one search result, fetched and re-encoded here."""
        from voxframe.sourcing.manual import SearchError, fetch_preview

        candidate = context.search_results.get(job_id, token)
        if candidate is None:
            raise HTTPException(status_code=404, detail="No such result.")
        directory = context.store.job_directory(job_id) / "search" / "previews"
        try:
            preview = fetch_preview(candidate, directory, token)
        except SearchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return FileResponse(preview, media_type="image/jpeg")

    @app.post("/api/jobs/{job_id}/scenes/{index}/search/{token}")
    def use_search_result(
        job_id: str,
        index: int,
        token: str,
        request: ImageSearch,
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """Use one search result for a scene: download it and put it there.

        Kept with the job, beside a provenance record, and credited in the
        video like any sourced image. The query is recorded as what found it.
        """
        from voxframe.plan.editing import EditError, use_image
        from voxframe.plan.scene_plan import PlanAsset
        from voxframe.sourcing.manual import SearchError, fetch_choice

        candidate = context.search_results.get(job_id, token)
        if candidate is None:
            raise HTTPException(
                status_code=404, detail="That result has expired. Search again."
            )
        job, plan_path, plan = _editable_plan(context, job_id)
        _scene_or_404(plan, index)

        settings = apply_to_settings(context.settings, load_preferences())
        directory = context.store.job_directory(job.id) / "search"
        try:
            path, width, height = fetch_choice(candidate, directory, request.query, settings)
        except SearchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        asset = PlanAsset(
            id=f"search-{token}",
            path=str(path.resolve()),
            width=width,
            height=height,
            license_name=candidate.license.name,
            license_author=candidate.license.author,
            license_source=candidate.license.source,
            license_url=candidate.license.source_url,
        )
        try:
            edited = use_image(plan, index, asset)
        except EditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        saved = _save_plan(edited, plan_path, "a picture")
        context.store.set_pending(job, saved.pending)
        return {"scene": edited.scenes[index].model_dump(mode="json"),
                "pending_edits": job.summary.get("pending_edits", 0)}

    # --- the Library screen (D-146) ------------------------------------------

    @app.get("/api/library")
    def library_list(
        source: str = "",
        kind: str = "",
        offset: int = 0,
        limit: int = 60,
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """What the library holds, newest first, a page at a time.

        Each asset says where it came from, under what licence, and which
        videos show it. Paths are never sent.
        """
        from voxframe.library.manage import asset_usage, describe

        root = context.settings.library_path
        if not root.is_dir():
            assets: tuple[Any, ...] = ()
        else:
            assets = AssetLibrary(root).all_assets()
        sources: dict[str, int] = {}
        for asset in assets:
            sources[asset.license.source] = sources.get(asset.license.source, 0) + 1

        chosen = [
            asset
            for asset in assets
            if (not source or asset.license.source == source)
            and (not kind or asset.kind.value == kind)
        ]
        chosen.sort(key=lambda asset: asset.added_at, reverse=True)
        limit = max(1, min(limit, 200))
        page = chosen[max(0, offset) : max(0, offset) + limit]
        usage = asset_usage(context.store)
        return {
            "total": len(chosen),
            "all": len(assets),
            "sources": sources,
            "author": load_preferences().library_author,
            "assets": [
                {**describe(asset, root), "used_in": usage.get(asset.id, [])}
                for asset in page
            ],
        }

    @app.get("/api/library/{asset_id}/thumbnail")
    def library_thumbnail(asset_id: str, context: ApiContext = Depends(ctx)) -> Response:
        """A preview of one library asset, named by id and sandbox-checked."""
        root = context.settings.library_path
        asset = AssetLibrary(root).get(asset_id) if root.is_dir() else None
        if asset is None:
            raise HTTPException(status_code=404, detail="No such asset.")
        resolved = _asset_file(context, str(asset.path))
        thumbnails = context.store.root / "library-thumbnails"
        return FileResponse(_thumbnail_into(thumbnails, resolved), media_type="image/jpeg")

    @app.post("/api/library")
    async def library_upload(
        files: list[UploadFile] = File(...),
        author: str = Form(""),
        license_name: str = Form("Own work", alias="license"),
        context: ApiContext = Depends(ctx),
    ) -> dict[str, Any]:
        """Add a person's own photographs and clips to the library.

        Stored inside the library under names the server chooses; a client's
        filename is used only for its suffix. Each is checked to be what it
        claims before it is kept, credited to the name given, and the name is
        remembered for next time.
        """
        from voxframe.library.manage import (
            CLIP_SUFFIXES,
            MAX_CLIP_BYTES,
            MAX_PHOTO_BYTES,
            PHOTO_SUFFIXES,
            LibraryError,
            add_batch,
            new_batch,
            provenance_for,
            shared_embedder,
        )

        try:
            license_info = provenance_for(author, license_name)
        except LibraryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not files:
            raise HTTPException(status_code=422, detail="Choose at least one file.")
        if len(files) > 50:
            raise HTTPException(status_code=422, detail="Add up to 50 files at a time.")

        for upload in files:
            suffix = Path(upload.filename or "").suffix.lower()
            if suffix not in PHOTO_SUFFIXES | CLIP_SUFFIXES:
                raise HTTPException(
                    status_code=415,
                    detail=(
                        f"{upload.filename or 'A file'} is not a photo or clip Voxframe "
                        "can use. Use JPEG, PNG or WebP photos, or MP4, MOV or WebM clips."
                    ),
                )

        root = context.settings.library_path
        batch = new_batch(root)
        import shutil

        try:
            for number, upload in enumerate(files):
                suffix = Path(upload.filename or "").suffix.lower()
                limit_bytes = MAX_PHOTO_BYTES if suffix in PHOTO_SUFFIXES else MAX_CLIP_BYTES
                target = batch.directory / f"upload-{number:03d}{suffix}"
                written = 0
                with target.open("wb") as handle:
                    while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
                        written += len(chunk)
                        if written > limit_bytes:
                            raise HTTPException(
                                status_code=413,
                                detail=(
                                    f"{upload.filename} is over "
                                    f"{limit_bytes // (1024 * 1024)} MB."
                                ),
                            )
                        handle.write(chunk)
                if suffix in PHOTO_SUFFIXES:
                    try:
                        from PIL import Image

                        with Image.open(target) as image:
                            image.verify()
                    except Exception as exc:
                        raise HTTPException(
                            status_code=415,
                            detail=f"{upload.filename} is not a readable image.",
                        ) from exc
                batch.files.append(target)

            library = AssetLibrary(root)
            result = await run_in_threadpool(
                add_batch, batch, library, shared_embedder(context.settings), license_info
            )
        except HTTPException:
            shutil.rmtree(batch.directory, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(batch.directory, ignore_errors=True)
            log.exception("library.upload_failed")
            raise HTTPException(
                status_code=500, detail="The files could not be added to the library."
            ) from exc

        preferences = load_preferences()
        if preferences.library_author != license_info.author:
            preferences.library_author = license_info.author
            save_preferences(preferences)

        return {
            "added": len(result.added),
            "already_there": len(result.skipped_duplicates),
            "failed": len(result.failed),
            "similar": len(result.near_duplicates),
        }

    @app.delete("/api/library/{asset_id}")
    def library_delete(asset_id: str, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Take an asset out of the library.

        The file goes only if the Library screen added it. Videos already made
        keep their finished files; one that shows the asset cannot be rendered
        again with it, which the screen says before anyone confirms.
        """
        from voxframe.library.manage import delete_asset

        root = context.settings.library_path
        if not root.is_dir():
            raise HTTPException(status_code=404, detail="No such asset.")
        result = delete_asset(AssetLibrary(root), root, asset_id)
        if not result.removed:
            raise HTTPException(status_code=404, detail="No such asset.")
        return {"removed": True, "file_deleted": result.file_deleted}

    @app.post("/api/jobs/{job_id}/render", status_code=202)
    def rerender(job_id: str, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Render the job's plan again, as edited.

        Same job, same id, so the page following it keeps following it. Only the
        scenes whose inputs changed are rendered; the rest come from the segment
        cache (D-101). The plan is rendered as it stands and never re-matched,
        or the person's choices would be replaced (D-128).
        """
        job = context.store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such job.")
        if not job.state.is_terminal:
            raise HTTPException(status_code=409, detail="This job is still rendering.")
        if context.store.artifact_path(job_id, "plan") is None:
            raise HTTPException(status_code=409, detail="This job has no plan yet.")

        plan_path = context.store.artifact_path(job_id, "plan")
        assert plan_path is not None
        try:
            context.store.restart(job_id, _plan_renderer(context))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # The plan as it now stands is what the video will show (D-182).
        PlanHistory(plan_path).rendered()
        return job.snapshot()

    @app.get("/api/jobs/{job_id}/plan/history")
    def plan_history(job_id: str, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Where the plan stands in its edits: undo, redo, and what is not yet in the video."""
        job = context.store.get(job_id)
        plan_path = context.store.artifact_path(job_id, "plan") if job else None
        if job is None or plan_path is None:
            raise HTTPException(status_code=404, detail="No such plan.")
        return _history_state(plan_path)

    @app.post("/api/jobs/{job_id}/plan/undo")
    def plan_undo(job_id: str, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Undo the last saved edit, whatever it was (D-182)."""
        job, plan_path, _ = _editable_plan(context, job_id)
        state = PlanHistory(plan_path).undo()
        if state is None:
            raise HTTPException(status_code=409, detail="There is nothing to undo.")
        context.store.set_pending(job, state.pending)
        return _history_state(plan_path)

    @app.post("/api/jobs/{job_id}/plan/redo")
    def plan_redo(job_id: str, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Redo the edit last undone (D-182)."""
        job, plan_path, _ = _editable_plan(context, job_id)
        state = PlanHistory(plan_path).redo()
        if state is None:
            raise HTTPException(status_code=409, detail="There is nothing to redo.")
        context.store.set_pending(job, state.pending)
        return _history_state(plan_path)

    @app.post("/api/jobs/{job_id}/resume", status_code=202)
    def resume(job_id: str, context: ApiContext = Depends(ctx)) -> dict[str, Any]:
        """Start an interrupted, failed or stopped job again.

        A job that already has a plan resumes from it -- it may carry the
        person's edits, and re-running the pipeline would replace them. One
        without a plan runs the pipeline again; transcripts and finished
        segments are cached, so it picks up close to where it stopped.
        """
        job = context.store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such job.")
        if not job.state.is_resumable:
            raise HTTPException(
                status_code=409, detail=f"A {job.state} job has nothing to resume."
            )

        if context.store.artifact_path(job_id, "plan") is not None:
            work = _plan_renderer(context)
        else:
            try:
                request = RenderRequest(**job.options)
            except Exception as exc:
                raise HTTPException(
                    status_code=409, detail="This job's settings could not be read."
                ) from exc
            audio = _upload_audio(context, request.upload_id)
            work = _renderer(context, _job_options(request, audio, context))

        context.store.resume(job_id, work)
        return job.snapshot()

    @app.get("/api/jobs/{job_id}/artifacts/{name}")
    def artifact(
        job_id: str, name: str, request: Request, context: ApiContext = Depends(ctx)
    ) -> Response:
        """Download one output by name.

        The client sends a *name* the job published, never a path. The name is
        mapped back to a path and re-checked against the sandbox, so even a bug
        in that mapping cannot serve a file from outside it.
        """
        path = context.store.artifact_path(job_id, name)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="No such artifact.")
        _check_sandbox(context, path)
        return _file_or_range(request, path)


# --- helpers ------------------------------------------------------------


def _probe_key(adapter: str, key: str) -> tuple[bool, str]:
    """Make one minimal request to check a key.

    Returns:
        ``(valid, detail)``. The detail is for a person to read and never
        contains the key -- it is passed through the redactor first, because an
        upstream error message can quote back the query string it was given.
    """
    from voxframe.sourcing.base import Adapter
    from voxframe.sourcing.secrets import PLACEHOLDER, redact

    try:
        probe: Adapter
        if adapter == "pexels":
            from voxframe.sourcing.pexels import PexelsAdapter

            probe = PexelsAdapter(key)
        else:
            from voxframe.sourcing.pixabay import PixabayAdapter

            probe = PixabayAdapter(key)

        if not probe.available():
            return False, "The key is empty or malformed."

        from voxframe.sourcing.base import SearchRequest

        probe.search(SearchRequest(query="tree", limit=1))
    except Exception as exc:
        # Two passes, because they catch different things. `redact` knows
        # the shapes of secrets in URLs and headers; but an upstream
        # library can quote the key verbatim in a bare message, and no
        # pattern reliably recognises an arbitrary key. Here the exact
        # value is known, so it is removed by identity first -- which a
        # pattern cannot be trusted to do (D-118).
        message = str(exc).replace(key, PLACEHOLDER)
        return False, redact(message)[:200] or exc.__class__.__name__

    return True, "Key accepted."


#: Longest edge of a filmstrip thumbnail. Large enough to recognise a
#: photograph at a glance, small enough that 148 of them load without thought.
THUMBNAIL_PIXELS = 320


def _layout_thumbnail(
    context: ApiContext, job_id: str, payload: dict[str, Any], index: int
) -> Path:
    """A still of a split or inset scene: its picture and the speaker, put
    together as the render puts them (D-197)."""
    import hashlib

    from PIL import Image

    from voxframe.plan.scene_layout import SceneLayout
    from voxframe.render.compose.layout import compose_still

    scene = payload["scenes"][index]
    footage = payload["footage"]
    try:
        recording = resolve_within(Path(footage["path"]), context.allowed_paths)
    except PathOutsideSandbox as exc:
        raise HTTPException(
            status_code=403, detail="That scene's recording is outside the allowed directories."
        ) from exc
    if not recording.is_file():
        raise HTTPException(status_code=404, detail="The recording is missing.")
    seconds = (scene["end_frame"] - scene["start_frame"]) / float(payload.get("fps") or 30.0)
    at = float(scene["footage_start"]) + float(footage.get("audio_offset") or 0.0) + min(
        0.5, seconds / 2
    )
    speaker = _thumbnail_for(context, job_id, recording, at=at)

    picture: Path | None = None
    asset = scene.get("asset") or {}
    if asset.get("path"):
        try:
            source = resolve_within(Path(asset["path"]), context.allowed_paths)
        except PathOutsideSandbox:
            source = None
        if source is not None and source.is_file():
            picture = _thumbnail_for(context, job_id, source)

    layout = SceneLayout.model_validate(scene.get("layout") or {})
    size = {"9:16": (360, 640), "1:1": (480, 480)}.get(str(payload.get("aspect")), (640, 360))
    key = hashlib.sha1(
        f"{speaker.name}|{picture.name if picture else ''}|{layout.model_dump_json()}|{size}"
        f"|{footage.get('subject_x', 0.5)}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:20]
    out = context.store.job_directory(job_id) / "thumbnails" / f"layout-{key}.jpg"
    if not out.is_file():
        still = compose_still(
            Image.open(picture) if picture else None, Image.open(speaker), *size, layout,
            speaker_x=float(footage.get("subject_x", 0.5)),
        )
        still.save(out, quality=88)
    return out


def _thumbnail_for(
    context: ApiContext, job_id: str, source: Path, *, at: float | None = None
) -> Path:
    """A cached thumbnail for one image in a job, generated if needed.

    ``at`` takes the frame at that many seconds into a recording (D-192).
    """
    return _thumbnail_into(context.store.job_directory(job_id) / "thumbnails", source, at=at)


def _thumbnail_into(directory: Path, source: Path, *, at: float | None = None) -> Path:
    """Return a cached thumbnail for one image, generating it if needed.

    Keyed on the file itself -- its resolved path, size and modification time
    -- not on the scene it appears in. An earlier version keyed on scene index
    and mtime alone, which would have served a scene's *old* thumbnail after
    its image was swapped: stock images downloaded in the same second share an
    mtime (D-128).
    """
    import hashlib

    directory.mkdir(parents=True, exist_ok=True)

    try:
        stat = source.stat()
        identity = f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        identity = str(source)
    if at is not None:
        identity += f"|at={at:.3f}"

    key = hashlib.sha1(identity.encode("utf-8"), usedforsecurity=False).hexdigest()
    cached = directory / f"{key[:20]}.jpg"
    if cached.is_file() and cached.stat().st_size:
        return cached

    from voxframe.render.encode.probe import probe_capabilities
    from voxframe.render.ffpath import run_ffmpeg

    capabilities = probe_capabilities()
    run_ffmpeg(
        capabilities.ffmpeg_path,
        [
            "-loglevel", "error",
            *(["-ss", f"{at:.3f}"] if at is not None else []),
            # One frame is enough for a still and is the right choice for a
            # clip too: the filmstrip shows what the scene opens on.
            "-i", str(source.resolve()),
            "-frames:v", "1",
            "-vf", (
                f"scale='min({THUMBNAIL_PIXELS},iw)':-2:"
                f"force_original_aspect_ratio=decrease"
            ),
            "-q:v", "4",
            "-y", str(cached.resolve()),
        ],
    )

    return cached


def _load_job_plan(context: ApiContext, job_id: str) -> ScenePlan:
    """A job's plan, loaded and sandbox-checked, or a 404."""
    from voxframe.plan.scene_plan import PlanError

    plan_path = context.store.artifact_path(job_id, "plan")
    if plan_path is None or not plan_path.is_file():
        raise HTTPException(status_code=404, detail="No plan for this job.")
    _check_sandbox(context, plan_path)
    try:
        return ScenePlan.load(plan_path)
    except PlanError as exc:
        raise HTTPException(
            status_code=409, detail=f"The plan could not be read: {exc}"
        ) from exc


def _scene_or_404(plan: ScenePlan, index: int) -> PlannedScene:
    if not 0 <= index < len(plan.scenes):
        raise HTTPException(status_code=404, detail="No such scene.")
    return plan.scenes[index]


def _asset_file(context: ApiContext, raw_path: str) -> Path:
    """Resolve an asset path from a plan, which is untrusted (D-122)."""
    try:
        resolved = resolve_within(Path(raw_path), context.allowed_paths)
    except PathOutsideSandbox as exc:
        raise HTTPException(
            status_code=403,
            detail="That image is outside the allowed directories.",
        ) from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Image file is missing.")
    return resolved


def _preview_track(job: Job, request: MixPreview, context: ApiContext) -> Path:
    """The track a preview plays in place of the video's music (D-184)."""
    if request.music_upload_id:
        return _upload_audio(context, request.music_upload_id)
    kept = job.summary.get("kept_track")
    plan_path = context.store.artifact_path(job.id, "plan")
    current = ""
    if plan_path is not None and plan_path.is_file():
        current = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8")).music_path
    path = Path(current or (str(kept.get("path", "")) if isinstance(kept, dict) else ""))
    if not path.is_file():
        raise HTTPException(status_code=409, detail="This video has no track of yours to hear.")
    return path


def _with_track(stems: Any, track: Path, request: MixPreview, context: ApiContext) -> Any:
    """The stems with ``track`` as the music, measured only where the preview plays.

    The bed is the same looped, faded track a render makes, and kept, so a
    second preview of it is quick.
    """
    import dataclasses

    import soundfile as sf

    from voxframe.render.audio.mixdown import speech_levels
    from voxframe.render.audio.music import MusicSettings
    from voxframe.render.compose.from_plan import _simple_bed
    from voxframe.render.encode.probe import probe_capabilities

    sound = context.settings.cache_path / "sound"
    sound.mkdir(parents=True, exist_ok=True)
    try:
        bed = _simple_bed(MusicSettings(path=track), stems.video_end, probe_capabilities(), sound)
    except Exception as exc:  # ffmpeg refused the file: say so, never a 500
        log.warning("api.preview_track_failed", error=str(exc))
        raise HTTPException(status_code=422, detail="That track could not be played.") from exc

    window = (request.start, request.start + request.seconds)

    def measured(spans: tuple[Any, ...], voice: Path | None) -> tuple[Any, ...]:
        if voice is None or not spans:
            return spans
        heard = [s for s in spans if s.end > window[0] and s.start < window[1]]
        fresh = {
            (s.start, s.end): s
            for s in speech_levels([(s.start, s.end) for s in heard], voice, bed, sf)
        }
        return tuple(fresh.get((s.start, s.end), s) for s in spans)

    return dataclasses.replace(
        stems,
        music=bed,
        spans=measured(stems.spans, stems.voice),
        spans_polished=measured(stems.spans_polished, stems.voice_polished),
        joins=(),
        music_groups=(),
        music_levels=(),
        group_power=(),
        play_groups=(),
    )


def _music_state(job: Job, plan: ScenePlan) -> dict[str, Any]:
    """The video's music choice, as the Sound card shows and changes it (D-179)."""
    from voxframe.music.score import styles

    kept = job.summary.get("kept_track")
    track = plan.music_path or (kept.get("path") if isinstance(kept, dict) else "")
    credit = plan.music_credit if plan.music_path else (
        kept.get("credit", "") if isinstance(kept, dict) else ""
    )
    return {
        "choice": "own" if plan.music_path else "score" if plan.score else "none",
        "style": plan.score.style if plan.score else None,
        "intensity": plan.score.intensity if plan.score else 0.0,
        "seed": plan.score.seed if plan.score else None,
        "track_name": Path(track).name if track else None,
        "track_credit": credit or "",
        "styles": [
            {"name": s.name, "label": s.label, "description": s.description}
            for s in styles().values()
        ],
        "score_ready": _score_summary()["ready"],
    }


def _score_summary() -> dict[str, Any]:
    """The generated score's styles, and whether its sounds are here (D-176)."""
    from voxframe.music.score import samples_dir, styles
    from voxframe.music.score.instruments import SampleLibrary, SamplesMissing

    try:
        SampleLibrary(samples_dir()).check()
        ready, reason = True, ""
    except SamplesMissing:
        ready = False
        reason = "The score's instrument sounds are not installed on this computer yet."
    return {
        "ready": ready,
        "reason": reason,
        "styles": [
            {"name": s.name, "label": s.label, "description": s.description}
            for s in styles().values()
        ],
    }


def _music_component_summary() -> dict[str, Any]:
    """Whether fitting music to speech needs a one-time download first (D-172)."""
    from voxframe.components import music_manifest, music_ready

    manifest = music_manifest()
    return {
        "ready": music_ready(),
        "download_mb": round(manifest.megabytes) if manifest is not None else None,
    }


def _mix_state(job: Job, mix: AudioMix, context: ApiContext) -> dict[str, Any]:
    """What the mix controls show: settings, choices, and the last checks."""
    from voxframe.plan.audio_mix import LOUDNESS_TARGETS, MIN_COMFORTABLE_MARGIN_DB
    from voxframe.render.audio.mixdown import Stems
    from voxframe.render.compose.from_plan import stems_path

    video = context.store.artifact_path(job.id, "video")
    plan = _load_job_plan(context, job.id)
    stems_file = stems_path(video) if video is not None else None
    polish: dict[str, Any] | None = None
    if stems_file is not None and stems_file.is_file():
        try:
            polish = Stems.load(stems_file).polish
        except (OSError, ValueError, TypeError, KeyError):
            polish = None
    return {
        "audio_mix": mix.model_dump(mode="json"),
        "destinations": [
            {
                "id": key.value,
                "label": target.label,
                "lufs": target.lufs,
                "true_peak": target.true_peak,
            }
            for key, target in LOUDNESS_TARGETS.items()
        ],
        "too_close": mix.too_close,
        "comfortable_margin_db": MIN_COMFORTABLE_MARGIN_DB,
        "has_music": bool(plan.music_path or plan.score),
        "music": _music_state(job, plan),
        "can_preview": stems_file is not None and stems_file.is_file(),
        "last_check": job.summary.get("sound"),
        # What voice polish measured and did (D-173); None before the first
        # update with it, or when it could not be made.
        "polish": polish,
        # Music already in the recording: a track on top may clash with it.
        "music_in_recording": bool(polish and polish.get("music_in_recording")),
    }


def _editable_plan(
    context: ApiContext, job_id: str
) -> tuple[Job, Path, ScenePlan]:
    """The job, its plan path and its plan, if the plan may be edited now.

    Editing is refused while the job is rendering: the renderer is reading the
    plan, and changing it underneath would render half of each version.
    """
    job = context.store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job.")
    if not job.state.is_terminal:
        raise HTTPException(
            status_code=409,
            detail="Wait for the render to finish before changing scenes.",
        )
    plan = _load_job_plan(context, job_id)
    plan_path = context.store.artifact_path(job_id, "plan")
    assert plan_path is not None  # _load_job_plan checked
    return job, plan_path, plan


def _file_or_range(request: Request, path: Path) -> Response:
    """A file, or the byte range the browser asked for (D-182).

    A video player seeks by asking for byte ranges; a server that ignores them
    leaves the video stuck at its start. This Starlette's FileResponse does
    not answer them, so ranges are answered here, without a new package.
    """
    import mimetypes
    import re

    size = path.stat().st_size
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", request.headers.get("range", "").strip())
    if match is None or match.groups() == ("", ""):
        whole = FileResponse(path, filename=path.name)
        whole.headers["accept-ranges"] = "bytes"
        return whole
    first, last = match.groups()
    if not first:  # the last N bytes
        start, end = max(0, size - int(last)), size - 1
    else:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        return Response(status_code=416, headers={"content-range": f"bytes */{size}"})

    def chunks() -> Iterator[bytes]:
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = source.read(min(1 << 16, remaining))
                if not data:
                    return
                remaining -= len(data)
                yield data

    return StreamingResponse(
        chunks(),
        status_code=206,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        headers={
            "content-range": f"bytes {start}-{end}/{size}",
            "content-length": str(end - start + 1),
            "accept-ranges": "bytes",
            "content-disposition": f'attachment; filename="{path.name}"',
        },
    )


def _save_plan(plan: ScenePlan, path: Path, label: str = "a change") -> History:
    """Write a plan atomically, so an interrupted save cannot corrupt it, and keep
    the version so the edit can be undone (D-182).

    The plan is the only record of the person's edits; half a file would lose
    all of them rather than the last one.
    """
    history = PlanHistory(path)
    history.begin()
    temporary = path.with_suffix(path.suffix + ".partial")
    plan.save(temporary)
    replace_retrying(temporary, path)
    return history.record(label)


def _changed_scenes(plan_path: Path) -> tuple[list[int], list[int]]:
    """Scenes that differ from what the video shows, and those whose picture does.

    The studio previews a changed picture in the player; any change is marked.
    """
    rendered = PlanHistory(plan_path).rendered_version()
    if rendered is None or not rendered.is_file():
        return [], []
    try:
        now, shown = ScenePlan.load(plan_path), ScenePlan.load(rendered)
    except Exception:
        return [], []

    def looks(scene: Any) -> tuple[Any, ...]:
        return (
            # What is on screen: the speaker (D-192), else the picture.
            "speaker" if now.shows_speaker(scene) else (scene.asset.id if scene.asset else None),
            scene.layout,
            scene.motion,
            scene.caption_text,
            scene.caption_animation,
            scene.emphasis,
            scene.card_kind,
            scene.card_text,
            scene.start_frame,
            scene.end_frame,
        )

    changed, pictures = [], []
    for position, scene in enumerate(now.scenes):
        before = shown.scenes[position] if position < len(shown.scenes) else None
        if before is None or looks(scene) != looks(before):
            changed.append(scene.index)
        if before is None or looks(scene)[:2] != looks(before)[:2]:
            pictures.append(scene.index)
    return changed, pictures


def _history_state(plan_path: Path) -> dict[str, Any]:
    state = PlanHistory(plan_path).state()
    changed, pictures = _changed_scenes(plan_path)
    return {
        "can_undo": state.can_undo,
        "can_redo": state.can_redo,
        "pending": state.pending,
        "undo_label": state.undo_label,
        "redo_label": state.redo_label,
        "changed_scenes": changed,
        "changed_pictures": pictures,
        "captions_changed": _captions_changed(plan_path),
    }


def _captions_changed(plan_path: Path) -> bool:
    """Whether the whole video's caption choices differ from the video's."""
    rendered = PlanHistory(plan_path).rendered_version()
    if rendered is None or not rendered.is_file():
        return False
    try:
        return ScenePlan.load(plan_path).captions != ScenePlan.load(rendered).captions
    except Exception:
        return False


def _plan_renderer(context: ApiContext) -> Callable[[Job], None]:
    """Work that renders a job's existing plan, as edited."""

    def work(job: Job) -> None:
        from voxframe.jobs.pipeline import render_plan
        from voxframe.render.encode.probe import probe_capabilities

        plan_path = context.store.artifact_path(job.id, "plan")
        video_path = context.store.artifact_path(job.id, "video")
        if plan_path is None:
            raise RuntimeError("This job has no plan to render.")
        if video_path is None:
            video_path = plan_path.with_suffix("").with_suffix(".mp4")

        def progress(stage: Stage, message: str, fraction: float | None) -> None:
            if job.cancel_requested:
                raise RenderCancelled
            context.store.record_progress(job, stage, message, fraction)

        try:
            quality = QualityPreset(job.options.get("quality", "standard"))
        except ValueError:
            quality = QualityPreset.STANDARD

        outcome = render_plan(
            plan_path,
            video_path,
            context.settings,
            probe_capabilities(),
            quality=quality,
            height=int(job.options.get("height", 720)),
            progress=progress,
            studio_copy=True,
        )
        _record_outcome(context, job, outcome)

    return work


def _library_has_assets(path: Path) -> bool:
    """Whether a library exists **and** has at least one asset in it.

    Existence alone was the old test, and it was wrong: the matcher refuses an
    empty library outright, so a first-run user whose library directory merely
    existed got a failed render instead of a video (D-126).

    Checked without creating anything: a missing directory is answered from the
    filesystem, never by opening a database that would then exist.
    """
    if not (path / "library.db").is_file():
        return False
    try:
        from voxframe.library.db import AssetLibrary

        return bool(AssetLibrary(path).all_assets())
    except Exception:
        return False


def _new_upload_id() -> str:
    """An unguessable id for an upload."""
    import uuid

    return uuid.uuid4().hex


def _check_sandbox(context: ApiContext, path: Path) -> None:
    """Refuse to serve a path outside the allowed directories.

    The response says only that it was refused. The rejected path goes to the
    log, not to the client: echoing it back tells a caller the absolute layout
    of the machine, which is information a refusal should not hand over.
    """
    try:
        resolve_within(path, context.allowed_paths)
    except PathOutsideSandbox as exc:
        log.warning("api.path_rejected", path=str(path))
        raise HTTPException(
            status_code=403, detail="That file is outside the allowed directories."
        ) from exc


def _upload_audio(context: ApiContext, upload_id: str) -> Path:
    """Find an uploaded file by id.

    The id is used as a single path component and the result is sandbox-checked,
    so ``../`` in an id cannot reach outside the uploads directory.
    """
    directory = context.store.root / "uploads" / upload_id
    try:
        resolved = resolve_within(directory, (context.store.root,))
    except PathOutsideSandbox as exc:
        raise HTTPException(status_code=400, detail="Invalid upload id.") from exc

    candidates = sorted(resolved.glob("source.*")) if resolved.is_dir() else []
    if not candidates:
        raise HTTPException(status_code=404, detail="No such upload.")
    return candidates[0]


def _upload_name(stored: Path) -> str:
    """The recording's name as the person knows it: recent videos and saved files show it."""
    record = stored.parent / "name.txt"
    if record.is_file():
        return _display_name(record.read_text(encoding="utf-8"))
    return stored.name


def _display_name(name: str) -> str:
    """A filename made safe to use as one: letters, digits and a few marks."""
    import re

    stem, suffix = Path(name).stem, Path(name).suffix.lower()
    cleaned = re.sub(r"[^\w .()-]+", "", stem, flags=re.UNICODE).strip(" .")[:80]
    return f"{cleaned or 'music'}{suffix}"


def _named_for_credits(stored: Path) -> Path:
    """The upload under the name the person gave it, for the credits (D-148).

    Uploads are stored as ``source.*``; a track with no stated credit is
    credited by filename (D-091), and "Music: source.mp3" would name nothing.
    A copy under the recorded name sits beside it, inside the sandbox.
    """
    record = stored.parent / "name.txt"
    if not record.is_file():
        return stored
    name = _display_name(record.read_text(encoding="utf-8"))
    if Path(name).suffix != stored.suffix.lower():
        name = Path(name).stem + stored.suffix.lower()
    named = stored.parent / "named" / name
    if not named.is_file():
        import shutil

        named.parent.mkdir(exist_ok=True)
        shutil.copyfile(stored, named)
    return named


def _job_options(
    request: RenderRequest, audio: Path, context: ApiContext
) -> JobOptions:
    """Translate a request into pipeline options, validating as we go."""
    try:
        aspect = AspectRatio(request.aspect)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Unknown aspect {request.aspect!r}."
        ) from exc

    try:
        quality = QualityPreset(request.quality)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Unknown quality {request.quality!r}."
        ) from exc

    # Decided here, from the person's saved consent and configured keys --
    # never taken from the request, so a page cannot switch on network access
    # the person did not agree to (D-116, D-132).
    source_imagery = request.use_library and sourcing_active(
        context.settings, load_preferences()
    )

    library = context.settings.library_path if request.use_library else None
    if library is not None and not _library_has_assets(library):
        # Not an error: a first-run user has no library -- or has an EMPTY one,
        # which is easy to end up with because opening a library creates its
        # database. Either way the render goes ahead with plain backgrounds and
        # says why, rather than failing (owner's decision 6, D-126).
        library = None

    music = None
    if request.music_upload_id:
        from voxframe.render.audio.music import MusicSettings

        # By id, like the recording: the page never names a path (D-115).
        music = MusicSettings(
            path=_named_for_credits(_upload_audio(context, request.music_upload_id)),
            credit=" ".join(request.music_credit.split()),
        )

    score = None
    if request.score_style:
        from voxframe.music.score import styles
        from voxframe.plan.score_choice import ScoreChoice, new_seed

        if music is not None:
            raise HTTPException(
                status_code=422,
                detail="Choose either your own music or a generated score, not both.",
            )
        if request.score_style not in styles():
            raise HTTPException(
                status_code=422, detail=f"No music style called {request.score_style!r}."
            )
        score = ScoreChoice(style=request.score_style, seed=new_seed())

    output_directory = audio.parent / "output"
    output_directory.mkdir(parents=True, exist_ok=True)

    return JobOptions(
        audio=audio,
        output=output_directory / f"{audio.stem}.mp4",
        aspect=aspect,
        quality=quality,
        height=request.height,
        library=library,
        title=request.title.strip(),
        chapters=request.chapters,
        highlights_seconds=request.highlights_seconds,
        model=request.model,
        language=request.language,
        languages=tuple(request.languages),
        source_imagery=source_imagery,
        footage=request.use_video,
        music=music,
        score=score,
        studio_copy=True,
    )


def _renderer(context: ApiContext, options: JobOptions) -> Callable[[Job], None]:
    """Build the worker function for one job."""

    def work(job: Job) -> None:
        from voxframe.config.style import get_template
        from voxframe.jobs.pipeline import run_pipeline
        from voxframe.render.encode.probe import probe_capabilities

        template = get_template(job.options.get("style") or None)
        caps = probe_capabilities()

        def progress(stage: Stage, message: str, fraction: float | None) -> None:
            if job.cancel_requested:
                raise RenderCancelled
            context.store.record_progress(job, stage, message, fraction)

        # Keys saved in the app are layered under the environment for each
        # render, so a key entered in Settings takes effect on the next video
        # without restarting anything (D-116).
        settings = apply_to_settings(context.settings, load_preferences())
        outcome = run_pipeline(options, settings, template, caps, progress=progress)
        _record_outcome(context, job, outcome)

    return work


def _record_outcome(context: ApiContext, job: Job, outcome: PipelineOutcome) -> None:
    """Attach a finished render's files and facts to its job.

    Shared by the first render and every re-render, so both report the same
    things in the same shape. A successful render has, by definition, put every
    edit into the video, so the pending-edit count goes back to zero.
    """
    from voxframe.plan.editing import edited_scene_count

    artifacts: dict[str, Path] = {"video": outcome.result.video_path}
    if outcome.plan_path:
        artifacts["plan"] = outcome.plan_path
    if outcome.result.srt_path:
        artifacts["srt"] = outcome.result.srt_path
    if outcome.result.vtt_path:
        artifacts["vtt"] = outcome.result.vtt_path
    if outcome.result.studio_path:
        artifacts["studio"] = outcome.result.studio_path

    context.store.record_result(
        job,
        artifacts=artifacts,
        warnings=outcome.warnings,
        summary={
            "width": outcome.result.width,
            "height": outcome.result.height,
            "scenes": outcome.scene_count,
            "matched_scenes": outcome.matched_scenes,
            "illustratable_scenes": outcome.illustratable_scenes,
            "fill_rate": round(outcome.fill_rate, 3),
            "words": outcome.word_count,
            "language": outcome.language,
            "elapsed_seconds": round(outcome.result.elapsed_seconds, 1),
            "realtime_factor": round(outcome.result.realtime_factor, 2),
            "credits": list(outcome.plan.credits()) if outcome.plan else [],
            "edited_scenes": edited_scene_count(outcome.plan) if outcome.plan else 0,
            # What a person can still do in one click, said on the result
            # screen rather than left for them to discover (D-138).
            "close_match_scenes": sum(
                1 for scene in outcome.plan.scenes
                if scene.asset is None and scene.near_misses and not scene.is_card
            ) if outcome.plan else 0,
            "atmospheric_scenes": sum(
                1 for scene in outcome.plan.scenes
                if scene.asset_source == "atmospheric"
            ) if outcome.plan else 0,
            "pending_edits": 0,
            # The sound's measurements and checks, and the settings behind
            # them, for the mix controls (D-171).
            "sound": outcome.result.sound,
            "audio_mix": outcome.plan.audio_mix.model_dump(mode="json") if outcome.plan else None,
            "saved_to": _place_in_videos(context, job, outcome.result.video_path),
        },
    )


def _place_in_videos(context: ApiContext, job: Job, video: Path) -> str | None:
    """Put a finished video in the videos folder, under a readable name (D-156).

    Named after the title, or the recording. A hard link where the disk allows,
    so a long video is not stored twice; a copy otherwise. A re-render replaces
    the same file rather than adding another.
    """
    folder = context.videos_folder
    if folder is None or not video.is_file():
        return None
    import re
    import shutil

    previous = job.summary.get("saved_to")
    if previous and Path(previous).parent == folder:
        target = Path(previous)
    else:
        name = str(job.options.get("title") or "") or Path(job.audio_name).stem
        stem = re.sub(r"[^\w .()-]+", "", name, flags=re.UNICODE).strip(" .")[:80] or "video"
        target = folder / f"{stem}.mp4"
        number = 2
        while target.exists():
            target = folder / f"{stem} ({number}).mp4"
            number += 1
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        try:
            os.link(video, target)
        except OSError:
            shutil.copyfile(video, target)
    except OSError:
        log.warning("api.videos_folder_failed", job=job.id)
        return None
    return str(target)


class RenderCancelled(Exception):
    """Raised inside a worker when the user asked it to stop."""


async def _event_stream(
    context: ApiContext, job_id: str
) -> AsyncIterator[str]:
    """Yield SSE frames for a job until it reaches a terminal state."""
    # No None sentinel: the stream ends when the job reaches a terminal
    # state, checked after every event and on every keepalive. A sentinel
    # would be a second way to end the same loop.
    queue: asyncio.Queue[ProgressEvent] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def listener(event: ProgressEvent) -> None:
        # Called from the render thread, so the hop back onto the event loop is
        # required: asyncio queues are not thread-safe.
        loop.call_soon_threadsafe(queue.put_nowait, event)

    unsubscribe = context.store.subscribe(job_id, listener)

    try:
        for event in context.store.history(job_id):
            yield _sse("progress", event.as_dict())

        job = context.store.get(job_id)
        if job is not None and job.state.is_terminal:
            yield _sse("state", job.snapshot())
            return

        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=KEEPALIVE_SECONDS
                )
            except TimeoutError:
                # A comment frame keeps the connection alive through proxies and
                # sleeping laptops without inventing progress.
                yield ": keepalive\n\n"
                job = context.store.get(job_id)
                if job is not None and job.state.is_terminal:
                    yield _sse("state", job.snapshot())
                    return
                continue

            yield _sse("progress", event.as_dict())

            job = context.store.get(job_id)
            if job is not None and job.state.is_terminal:
                yield _sse("state", job.snapshot())
                return
    finally:
        unsubscribe()


def _sse(event: str, payload: dict[str, Any]) -> str:
    """Format one server-sent event."""
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _library_summary(settings: Settings) -> dict[str, Any]:
    """How many assets the library holds, without failing if it has none."""
    path = settings.library_path
    if not path.exists():
        return {"present": False, "assets": 0, "path": path.name}

    try:
        from voxframe.library.db import AssetLibrary

        return {
            "present": True,
            "assets": len(AssetLibrary(path).all_assets()),
            "path": path.name,
        }
    except Exception as exc:
        return {"present": True, "assets": 0, "path": path.name, "error": str(exc)}


def _sourcing_summary() -> dict[str, Any]:
    """Which sourcing adapters are usable, without revealing any key.

    Mirrors ``voxframe sources``: names and availability only. A key is never
    echoed, not even masked, into an HTTP response.
    """
    try:
        from voxframe.config.settings import get_settings
        from voxframe.sourcing import build_adapters

        settings = get_settings()
        active = sorted(adapter.name for adapter in build_adapters(settings))
        return {"adapters": active, "available": bool(active)}
    except Exception:
        return {"adapters": [], "available": False}


def _probe_duration(path: Path) -> float | None:
    """Audio duration, for the render-time estimate on the upload screen.

    Returns ``None`` rather than raising: a file FFprobe cannot read yet is
    still worth accepting, and the render will report the real error.
    """
    try:
        from voxframe.render.encode.probe import probe_media

        return probe_media(path).duration or None
    except Exception:
        return None


def _probe_has_video(path: Path) -> bool:
    """Whether a recording has a picture of its own to show (D-192).

    ``False`` rather than raising, like the duration: the choice is simply
    not offered, and nothing else depends on it.
    """
    try:
        from voxframe.render.encode.probe import probe_footage

        return probe_footage(path) is not None
    except Exception:
        return False


def build_default_app() -> FastAPI:
    """Build an app with default settings, for ``uvicorn voxframe.api.app:...``."""
    settings = get_settings()
    root = settings.output_path / "web"
    store = JobStore(root)
    return create_app(
        ApiContext(
            settings=settings,
            store=store,
            token=SessionToken(),
            allowed_paths=(root.resolve(), settings.library_path.resolve()),
        )
    )
