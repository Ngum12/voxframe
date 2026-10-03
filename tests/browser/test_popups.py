"""Pop-ups in the studio, in a real browser (D-198).

Uses the video of the live-captions tests: "Floods hit Douala again this
week", vertical, with its studio copy as VP9 for Playwright's Chromium.
"""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest

from tests.browser.test_live_captions import (
    _open_studio,
    _show,
    caps,
    job,
    page,
    playwright_api,
    server,
)

__all__ = ["caps", "job", "page", "server"]  # the fixtures, shared

pytestmark = pytest.mark.browser


def _plan(job):  # type: ignore[no-untyped-def]
    from voxframe.plan.scene_plan import ScenePlan

    return ScenePlan.load(job[2])


def _popups_tab(page, handle) -> None:  # type: ignore[no-untyped-def]
    _open_studio(page, handle)
    page.get_by_role("tab", name="Pop-ups").click()


def test_a_suggestion_is_added_and_shown_over_the_player(server, page, job) -> None:  # type: ignore[no-untyped-def]
    handle, _ = server
    _popups_tab(page, handle)
    suggestion = page.locator(".popup-suggestion").filter(has_text="Floods")
    suggestion.wait_for(timeout=15_000)
    suggestion.get_by_role("button", name="Add").click()
    page.locator(".studio-status").get_by_text("not yet in the video").wait_for(timeout=10_000)

    overlays = _plan(job).overlays
    assert [o.sticker for o in overlays] == ["water-wave"]
    # "Floods" is said from 0.3 s: the sticker shows over the player then.
    _show(page, 0.9)
    sticker = page.locator(".popup-layer img")
    sticker.wait_for(timeout=5_000)
    page.wait_for_function(
        "() => [...document.querySelectorAll('.popup-layer img')].some(i => i.naturalWidth > 0)",
        timeout=10_000,
    )
    _show(page, 0.1)
    assert page.locator(".popup-layer img").count() == 0
    assert page._voxframe_errors == []


def test_text_is_drawn_live_and_can_be_dragged(server, page, job) -> None:  # type: ignore[no-untyped-def]
    from PIL import Image

    handle, _ = server
    _popups_tab(page, handle)
    before = _show(page, 1.2)
    page.get_by_role("button", name="Text", exact=True).click()
    page.locator(".popup-editor").wait_for(timeout=10_000)
    page.wait_for_timeout(1200)
    after = _show(page, 1.2)
    # The pill, gold, near the top: drawn by libass from the live document.
    top = slice(0, after.shape[0] // 3)
    gold = lambda f: (f[..., 0] > 200) & (f[..., 1] > 170) & (f[..., 2] < 90)  # noqa: E731
    assert gold(after[top]).sum() > gold(before[top]).sum() + 100

    text = next(o for o in _plan(job).overlays if o.kind.value == "text")
    item = page.locator(".popup-item[data-chosen='true']")
    item.wait_for()
    frame = page.locator(".studio-frame").bounding_box()
    start = item.bounding_box()
    assert frame is not None and start is not None
    page.mouse.move(start["x"] + start["width"] / 2, start["y"] + start["height"] / 2)
    page.mouse.down()
    page.mouse.move(frame["x"] + frame["width"] * 0.3, frame["y"] + frame["height"] * 0.6, steps=6)
    page.mouse.up()
    page.wait_for_timeout(800)
    moved = next(o for o in _plan(job).overlays if o.id == text.id)
    assert moved.x == pytest.approx(0.3, abs=0.05) and moved.y == pytest.approx(0.6, abs=0.05)
    assert Image.open(BytesIO(page.locator(".studio-frame").screenshot())).size[0] > 0
    assert page._voxframe_errors == []


def test_the_progress_bar_is_one_switch(server, page, job) -> None:  # type: ignore[no-untyped-def]
    handle, _ = server
    _popups_tab(page, handle)
    page.get_by_text("A progress bar").click()
    playwright_api.expect(page.get_by_role("checkbox", name="A progress bar")).to_be_checked()
    page.wait_for_timeout(600)
    assert _plan(job).progress_bar
    shot = _show(page, 1.5)
    row = shot[-12:-2]
    gold = (row[..., 0] > 200) & (row[..., 1] > 150) & (row[..., 2] < 100)
    assert np.any(gold)
    assert page._voxframe_errors == []
