"""Downloaded profiles remain selectable and update the real library through the UI."""
from __future__ import annotations

import http.client
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api")
pytest.importorskip("fastapi")

from voxframe import model_downloads  # noqa: E402
from voxframe.api.serve import build_server, find_free_port, serve  # noqa: E402
from voxframe.config.settings import Settings  # noqa: E402
from voxframe.config.userprefs import (  # noqa: E402
    UserPreferences,
    load_preferences,
    save_preferences,
)
from voxframe.library import manage  # noqa: E402
from voxframe.library.db import AssetLibrary  # noqa: E402
from voxframe.library.embeddings import EMBEDDING_MODELS  # noqa: E402
from voxframe.models.asset import Asset, AssetKind, LicenseInfo  # noqa: E402


def test_switch_downloaded_profiles_updates_library_and_survives_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VOXFRAME_PROFILE", raising=False)
    monkeypatch.delenv("VOXFRAME_EMBED_MODEL", raising=False)
    save_preferences(UserPreferences(model_profile="lite", sourcing_consent=False))
    settings = Settings(
        library_path=tmp_path / "library", cache_path=tmp_path / "cache",
        output_path=tmp_path / "out", _env_file=None,
    )
    library = AssetLibrary(settings.library_path)
    photo = settings.library_path / "photo.jpg"
    Image.new("RGB", (32, 32), "red").save(photo)
    asset = Asset(
        id="photo", path=photo, kind=AssetKind.IMAGE, sha256="a" * 64,
        width=32, height=32, license=LicenseInfo(name="CC0", author="Ada", source="local"),
    )
    library.add(asset, embedding=[1.0] + [0.0] * 511,
                embed_model="/".join(EMBEDDING_MODELS["lite"][:2]))
    monkeypatch.setattr(model_downloads, "is_ready", lambda need: True)
    monkeypatch.setattr(model_downloads, "_fetch", lambda need: pytest.fail("Unexpected download"))
    entered, release = threading.Event(), threading.Event()

    class Embedder:
        def __init__(self, key: str) -> None:
            self.model_id = "/".join(EMBEDDING_MODELS[key][:2])

        def embed_images(self, paths: list[Path]) -> list[list[float]]:
            entered.set()
            assert release.wait(10)
            for path in paths:
                with Image.open(path) as image:
                    image.verify()
            return [[1.0] + [0.0] * 511 for _ in paths]

    monkeypatch.setattr(manage, "shared_embedder", lambda s: Embedder(s.resolved_embed_model))
    handle = build_server(port=find_free_port("127.0.0.1", 0), settings=settings)
    threading.Thread(target=serve, args=(handle,), daemon=True).start()
    for _ in range(120):
        try:
            connection = http.client.HTTPConnection(handle.host, handle.port, timeout=1)
            connection.request("GET", "/api/health")
            ready = connection.getresponse().status == 200
            connection.close()
            if ready:
                break
        except OSError:
            pass
        time.sleep(0.05)
    else:
        pytest.fail("Server did not start")

    try:
        with playwright_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(handle.url, wait_until="networkidle")
            page.get_by_role("button", name="Settings", exact=True).click()
            page.get_by_role("radio", name="Standard — best accuracy", exact=False).check()
            page.get_by_role("button", name="Use Standard", exact=True).click()
            assert entered.wait(5)
            page.get_by_text("Updating picture matching in your library…", exact=True).wait_for()
            assert page.get_by_role("button", name="Updating library…").is_disabled()
            release.set()
            page.get_by_text("Ready. Everything now runs on this computer.", exact=True).wait_for()
            assert library.embedding_models() == {"/".join(EMBEDDING_MODELS["default"][:2])}
            page.get_by_role("radio", name="Lite — smallest download", exact=False).check()
            page.get_by_role("button", name="Use Lite", exact=True).click()
            page.get_by_text("Ready. Everything now runs on this computer.", exact=True).wait_for()
            assert library.embedding_models() == {"/".join(EMBEDDING_MODELS["lite"][:2])}
            page.reload(wait_until="networkidle")
            page.get_by_role("button", name="Settings", exact=True).click()
            assert page.get_by_role("radio", name="Lite — smallest download", exact=False).is_checked()
            assert load_preferences().model_profile == "lite"
            assert not errors
            browser.close()
    finally:
        release.set()
        handle.store.shutdown()
