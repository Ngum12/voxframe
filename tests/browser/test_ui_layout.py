"""Reachable studio controls and real scrolling, without a transcription model."""
from __future__ import annotations

# Fixtures intentionally imported for pytest.
# ruff: noqa: F811
from pathlib import Path

import pytest

from tests.browser.test_music_library import _job
from tests.browser.test_use_my_video import _home, caps, page, server  # noqa: F401

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def project(server, caps):  # type: ignore[no-untyped-def]
    return _job(server[0], caps, "A long VoxFrame project title to check narrow screens.mp4")[0]


def _open(page, server, project, width=1440, height=900):  # type: ignore[no-untyped-def]
    page.set_viewport_size({"width": width, "height": height})
    _home(page, server[0])
    page.get_by_text(project.audio_name, exact=True).first.click()
    page.get_by_role("tab", name="Scenes", exact=True).wait_for()


def _no_overflow(page):  # type: ignore[no-untyped-def]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate("""() => ({
      tab: document.querySelector('[aria-selected=true]')?.textContent,
      overflow: [...document.querySelectorAll('main *')].filter(e => {
        const r=e.getBoundingClientRect(); return r.right > innerWidth && r.width > 0;
      }).map(e => ({name:e.className, width:e.getBoundingClientRect().width, right:e.getBoundingClientRect().right})).slice(0,12)
    })""")


@pytest.mark.parametrize("size", [(1440, 900), (1024, 600), (768, 1024), (390, 844), (320, 640)])
def test_every_editor_is_reachable_at_each_size(server, page, project, size):  # type: ignore[no-untyped-def]
    _open(page, server, project, *size)
    tabs = page.get_by_role("tablist", name="Edit", exact=True)
    assert tabs.get_by_role("tab").count() == 9
    for tab in tabs.get_by_role("tab").all():
        tab.click()
        assert tab.get_attribute("aria-selected") == "true"
        page.get_by_role("tabpanel").wait_for()
        _no_overflow(page)
        assert page.get_by_role("tabpanel").evaluate("e => e.scrollWidth <= e.clientWidth + 1"), tab.inner_text()
        # A visible tab must also fit inside the tablist, rather than a hidden strip.
        box, rail = tab.bounding_box(), tabs.bounding_box()
        assert box and rail
        assert rail["x"] <= box["x"] and box["x"] + box["width"] <= rail["x"] + rail["width"] + 1
    assert page._voxframe_errors == []


def test_portrait_player_keeps_its_shape_on_a_phone(server, page, caps):  # type: ignore[no-untyped-def]
    from voxframe.config.settings import AspectRatio
    from voxframe.config.style import get_template
    from voxframe.plan.scene_plan import ScenePlan
    from voxframe.render.compose import render_from_plan

    handle, _ = server
    job, path = _job(handle, caps, "Portrait layout.mp4")
    plan = ScenePlan.load(path).model_copy(update={"aspect": AspectRatio.VERTICAL})
    plan.save(path)
    result = render_from_plan(plan, Path(plan.audio_path), get_template(), caps,
                              path.parent / "portrait.mp4", height=240)
    handle.store.record_result(job, artifacts={"plan": path, "video": result.video_path}, warnings=(), summary={})
    _open(page, server, job, 390, 844)
    frame = page.locator(".studio-frame").bounding_box()
    assert frame and frame["height"] > 300
    assert frame["width"] / frame["height"] == pytest.approx(9 / 16, abs=.01)
    _no_overflow(page)
    page.screenshot(path=str(path.parent / "portrait-phone.png"), full_page=True)


def test_desktop_scrolls_the_editor_and_preserves_the_player(server, page, project):  # type: ignore[no-untyped-def]
    _open(page, server, project)
    page.get_by_role("tab", name="Sound", exact=True).click()
    panel = page.get_by_role("tabpanel")
    assert page.evaluate("document.documentElement.scrollHeight <= innerHeight")
    before = page.locator(".studio-player").bounding_box()
    panel.hover()
    page.mouse.wheel(0, 700)
    page.wait_for_function("() => document.querySelector('.studio-tabpanel').scrollTop > 100")
    assert page.evaluate("scrollY") == 0
    assert page.locator(".studio-player").bounding_box() == before
    page.get_by_role("tab", name="Scenes", exact=True).click()
    assert panel.evaluate("e => e.scrollTop") == 0
    scenes = page.get_by_role("tab", name="Scenes", exact=True)
    scenes.focus()
    page.keyboard.press("ArrowDown")
    assert page.get_by_role("tab", name="Style", exact=True).get_attribute("aria-selected") == "true"
    page.keyboard.press("End")
    assert page.get_by_role("tab", name="Export", exact=True).evaluate("e => e === document.activeElement")
    page.keyboard.press("Home")
    assert scenes.evaluate("e => e === document.activeElement")
    assert page._voxframe_errors == []


def test_mobile_uses_page_scroll_and_navigation_starts_at_the_top(server, page, project):  # type: ignore[no-untyped-def]
    _open(page, server, project, 320, 640)
    page.locator(".studio-menu summary").click()
    menu = page.get_by_role("menu").bounding_box()
    assert menu and menu["x"] >= 0 and menu["x"] + menu["width"] <= 320
    page.locator(".studio-menu summary").click()
    page.get_by_role("tab", name="Sound", exact=True).click()
    panel = page.get_by_role("tabpanel")
    panel.hover()
    before = page.evaluate("scrollY")
    page.mouse.wheel(0, 450)
    page.wait_for_function("before => scrollY > before + 100", arg=before)
    assert panel.evaluate("e => e.scrollTop") == 0
    page.get_by_role("tab", name="Captions", exact=True).click()
    page.wait_for_function("() => document.querySelector('.studio-panel').getBoundingClientRect().top >= -1")
    page.get_by_role("button", name="Library", exact=True).click()
    page.wait_for_function("() => scrollY === 0")
    page.locator(".music-library").wait_for()
    page.get_by_text("Save a music track", exact=True).click()
    _no_overflow(page)
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("radiogroup", name="Theme").wait_for()
    page.wait_for_function("() => scrollY === 0")
    key = page.get_by_label("Pexels", exact=True).bounding_box()
    assert key and key["width"] > 180  # Enough room to read and edit a pasted key.
    for theme in ("Dark", "Light"):
        page.get_by_role("radio", name=theme, exact=True).click()
        _no_overflow(page)
    page.get_by_role("button", name="Make a video", exact=True).click()
    page.locator(".hero").wait_for()
    _no_overflow(page)
    page.locator(".skip-link").focus()
    page.keyboard.press("Enter")
    assert page.locator("#main").evaluate("e => e === document.activeElement")
    assert page._voxframe_errors == []


def test_large_saved_panels_and_short_windows_keep_controls_usable(server, page, project):  # type: ignore[no-untyped-def]
    _home(page, server[0])
    page.evaluate("""localStorage.setItem('voxframe.studio.layout.v1', JSON.stringify({
      panel: 720, panelOpen: true, timeline: 260, timelineOpen: true
    }))""")
    _open(page, server, project, 1024, 800)
    player = page.locator(".studio-player").bounding_box()
    assert player and player["width"] > 400 and player["height"] > 100
    _no_overflow(page)
    page.set_viewport_size({"width": 1024, "height": 600})
    player = page.locator(".studio-player").bounding_box()
    assert player and player["width"] > 400 and player["height"] > 100
    page.set_viewport_size({"width": 1024, "height": 400})
    page.keyboard.press("?")
    dialog = page.get_by_role("dialog", name="Keyboard shortcuts")
    dialog.wait_for()
    box = dialog.bounding_box()
    assert box and box["y"] >= 0 and box["y"] + box["height"] <= 400
    dialog.get_by_role("button", name="Close", exact=True).click()
    assert not dialog.is_visible()
    _no_overflow(page)
    page.evaluate("localStorage.removeItem('voxframe.studio.layout.v1')")
    assert page._voxframe_errors == []
