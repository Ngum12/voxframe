"""The hook and the pace in the studio, in a real browser (D-199).

Uses the video of the live-captions tests: "Floods hit Douala again this
week", said from 0.3 s to 2.6 s of a 3 s recording, vertical.
"""

from __future__ import annotations

import re

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

__all__ = ["caps", "job", "page", "playwright_api", "server"]  # the fixtures, shared

pytestmark = pytest.mark.browser


def _plan(job):  # type: ignore[no-untyped-def]
    from voxframe.plan.scene_plan import ScenePlan

    return ScenePlan.load(job[2])


def _video_seconds(page) -> float:  # type: ignore[no-untyped-def]
    text = page.locator(".studio-time").inner_text()
    minutes, seconds = re.search(r"/ (\d+):([\d.]+)", text).groups()  # type: ignore[union-attr]
    return int(minutes) * 60 + float(seconds)


def test_cuts_are_found_shown_undone_and_made_by_hand(server, page, job) -> None:  # type: ignore[no-untyped-def]
    handle, _ = server
    _open_studio(page, handle)
    page.locator(".studio-time").click()
    page.keyboard.press("6")  # the sixth tab, by its shortcut
    assert page.get_by_role("tab", name="Hook & pace").get_attribute("aria-selected") == "true"

    assert _video_seconds(page) == pytest.approx(3.0, abs=0.05)
    # The words follow each other closely and the silences at the ends are
    # short: nothing to cut, and the panel says so.
    page.get_by_role("button", name="Find jump cuts").click()
    page.get_by_text("Nothing to cut").wait_for(timeout=10_000)

    # A stretch cut by hand: "again", said from 1.55 s to 1.9 s.
    _show(page, 1.5)
    page.get_by_role("button", name=re.compile("Mark the start")).click()
    _show(page, 1.95)
    page.get_by_role("button", name="Cut it").click()
    cuts = page.get_by_role("list", name="Cuts")
    cuts.get_by_text("Your cut").wait_for(timeout=10_000)
    (manual,) = _plan(job).pace.cuts
    assert manual.kind.value == "manual" and manual.start == pytest.approx(1.5, abs=0.1)
    page.locator(".studio-status").get_by_text("not yet in the video").wait_for(timeout=10_000)
    # The player's clock is the video's, shorter now; the timeline marks the
    # jump, and "again" is gone from it.
    page.wait_for_function(
        "() => /\\/ 0:02\\.[0-6]/.test(document.querySelector('.studio-time').textContent)",
        timeout=10_000,
    )
    page.locator(".timeline-cut").first.wait_for(state="attached", timeout=10_000)
    words = page.locator(".timeline-word").all_inner_texts()
    assert "again" not in words and "this" in words

    # Put back, on its own.
    cuts.get_by_role("checkbox").click()  # made once saved
    page.wait_for_function(
        "() => /\\/ 0:03/.test(document.querySelector('.studio-time').textContent)", timeout=10_000
    )
    assert [c.on for c in _plan(job).pace.cuts] == [False]
    assert page.locator(".timeline-cut").count() == 0
    assert page._voxframe_errors == []


def test_the_hook_title_is_drawn_live(server, page, job) -> None:  # type: ignore[no-untyped-def]
    handle, _ = server
    _open_studio(page, handle)
    page.get_by_role("tab", name="Hook & pace").click()
    before = _show(page, 0.5)
    page.get_by_label("Hook title").fill("FLOODS AGAIN")
    for _ in range(50):
        if _plan(job).hook_title == "FLOODS AGAIN":
            break
        page.wait_for_timeout(200)
    assert _plan(job).hook_title == "FLOODS AGAIN"
    page.wait_for_timeout(1500)  # the live document, redrawn
    after = _show(page, 0.5)
    # Big white letters in the top third, over the first seconds.
    top = slice(0, after.shape[0] // 3)
    white = lambda f: (f[..., 0] > 230) & (f[..., 1] > 230) & (f[..., 2] > 230)  # noqa: E731
    assert white(after[top]).sum() > white(before[top]).sum() + 200
    assert page._voxframe_errors == []
