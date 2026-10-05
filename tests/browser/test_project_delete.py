"""Cancel, recover from an error, then delete a project from Recent videos."""
from __future__ import annotations

# Fixtures intentionally imported for pytest.
# ruff: noqa: F811
import pytest

from tests.browser.first_run import to_upload_screen
from tests.browser.test_music_library import _job
from tests.browser.test_use_my_video import _home, caps, page, server  # noqa: F401

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("width", [1440, 320])
def test_delete_is_explicit_and_keeps_other_projects(server, page, caps, width):
    handle, work = server
    job, plan = _job(handle, caps, f"Delete my long recording project at {width}px.mp4")
    other, _ = _job(handle, caps, f"Keep this project at {width}px.mp4")
    original = work / f"original-{width}.mp4"
    original.write_bytes(b"original recording outside the project")
    page.set_viewport_size({"width": width, "height": 800})
    _home(page, handle)
    entry = page.locator(".recent-entry").filter(has_text=job.audio_name)
    delete = entry.get_by_role("button", name=f"Delete project: {job.audio_name}", exact=True)
    delete.click()
    dialog = page.get_by_role("dialog", name="Delete this project?")
    dialog.wait_for()
    assert dialog.get_by_role("button", name="Cancel", exact=True).evaluate("e => e === document.activeElement")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(work / f"delete-confirmation-{width}.png"), full_page=True)
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    assert not dialog.is_visible() and handle.store.get(job.id) is not None
    assert delete.evaluate("e => e === document.activeElement")
    delete.click()
    page.keyboard.press("Escape")
    assert not dialog.is_visible() and plan.exists()

    def unavailable(route):
        route.fulfill(status=500, content_type="application/json",
                      body='{"detail":"The project could not be deleted. Try again."}')

    page.route(f"**/api/jobs/{job.id}", unavailable)
    delete.click()
    dialog.get_by_role("button", name="Delete project", exact=True).click()
    dialog.get_by_role("alert").get_by_text("The project could not be deleted. Try again.").wait_for()
    assert handle.store.get(job.id) is not None and plan.exists()
    page.unroute(f"**/api/jobs/{job.id}", unavailable)
    dialog.get_by_role("button", name="Delete project", exact=True).click()
    page.get_by_text("Project deleted. Your original recording, library assets and separately saved videos were kept.", exact=True).wait_for()
    assert not dialog.is_visible() and entry.count() == 0
    assert handle.store.get(job.id) is None and not plan.parent.exists()
    assert original.read_bytes() == b"original recording outside the project"
    page.reload(wait_until="networkidle")
    to_upload_screen(page)
    assert page.get_by_text(job.audio_name, exact=True).count() == 0
    page.get_by_text(other.audio_name, exact=True).click()
    page.get_by_role("tab", name="Scenes", exact=True).wait_for()
    assert handle.store.artifact_path(other.id, "video").is_file()
    assert page._voxframe_errors == []
