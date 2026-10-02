"""The app's look: dark, light, or following the computer (D-185)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from voxframe.api import app as app_module
from voxframe.api.app import ApiContext, create_app
from voxframe.api.security import SessionToken
from voxframe.config.settings import Settings
from voxframe.config.userprefs import load_preferences
from voxframe.jobs.store import JobStore

STYLES = Path(__file__).resolve().parents[2] / "web" / "src" / "styles.css"
LOOPBACK = "http://127.0.0.1:8765"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("VOXFRAME_CONFIG_DIR", str(tmp_path / "config"))
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text(
        '<!doctype html>\n<html lang="en">\n<head><title>Voxframe</title></head><body></body>\n</html>\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "static_root", lambda: static)
    root = tmp_path / "web"
    context = ApiContext(
        settings=Settings(cache_path=tmp_path / "cache", output_path=tmp_path / "out"),
        store=JobStore(root, reap_interval=None),
        token=SessionToken("t"),
        allowed_paths=(root.resolve(),),
    )
    test_client = TestClient(create_app(context), base_url=LOOPBACK)
    test_client.headers.update({"x-voxframe-token": "t"})
    return test_client


def _html_tag(client: TestClient, path: str = "/") -> str:
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(r"<html[^>]*>", response.text)
    assert match
    return match.group(0)


class TestTheSetting:
    def test_it_follows_the_system_until_chosen(self, client: TestClient) -> None:
        assert client.get("/api/settings").json()["theme"] == "system"
        assert "data-theme" not in _html_tag(client)

    @pytest.mark.parametrize("theme", ["dark", "light"])
    def test_a_chosen_look_is_served_from_the_first_frame(self, client: TestClient, theme: str) -> None:
        saved = client.put("/api/settings", json={"theme": theme})

        assert saved.json()["theme"] == theme
        assert load_preferences().theme == theme
        assert f'data-theme="{theme}"' in _html_tag(client)
        assert f'data-theme="{theme}"' in _html_tag(client, "/some/client/route")  # a reload anywhere

    def test_back_to_the_system(self, client: TestClient) -> None:
        client.put("/api/settings", json={"theme": "light"})
        client.put("/api/settings", json={"theme": "system"})

        assert "data-theme" not in _html_tag(client)

    def test_other_settings_leave_it_alone(self, client: TestClient) -> None:
        client.put("/api/settings", json={"theme": "light"})
        client.put("/api/settings", json={"sourcing_consent": False})

        assert client.get("/api/settings").json()["theme"] == "light"

    def test_anything_else_is_refused(self, client: TestClient) -> None:
        response = client.put("/api/settings", json={"theme": "purple"})

        assert response.status_code == 422

    def test_a_hand_edited_file_cannot_inject_markup(self, client: TestClient, tmp_path: Path) -> None:
        config = tmp_path / "config" / "config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('{"theme": "light\\"><script>alert(1)</script>"}', encoding="utf-8")

        assert load_preferences().theme == "system"
        assert "script" not in _html_tag(client)


def _block(css: str, opening: str) -> str:
    start = css.index(opening) + len(opening)
    return css[start : css.index("}", start)]


def test_the_two_light_blocks_are_the_same() -> None:
    """Chosen Light and a light computer must look the same."""
    css = STYLES.read_text(encoding="utf-8")

    chosen = _block(css, ':root[data-theme="light"] {')
    followed = _block(css, ':root:not([data-theme="dark"]) {')

    assert chosen.split() == followed.split()
    assert "color-scheme: light" in chosen
