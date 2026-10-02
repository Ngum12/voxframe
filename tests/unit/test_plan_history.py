"""Undo and redo for every edit to a plan (D-182)."""

from __future__ import annotations

from pathlib import Path

from voxframe.api.plan_history import MAX_VERSIONS, PlanHistory


def _edit(plan: Path, history: PlanHistory, text: str, label: str) -> None:
    history.begin()
    plan.write_text(text, encoding="utf-8")
    history.record(label)


def test_undo_and_redo_restore_each_version_exactly(tmp_path: Path) -> None:
    plan = tmp_path / "talk.plan.json"
    plan.write_text("rendered", encoding="utf-8")
    history = PlanHistory(plan)

    _edit(plan, history, "picture", "Scene 3: picture")
    _edit(plan, history, "caption", "Scene 4: caption")

    state = history.undo()
    assert plan.read_text(encoding="utf-8") == "picture"
    assert state is not None and state.pending == 1 and state.redo_label == "Scene 4: caption"
    history.undo()
    assert plan.read_text(encoding="utf-8") == "rendered"
    assert history.state().pending == 0 and not history.state().can_undo
    assert history.undo() is None  # nothing earlier
    history.redo()
    history.redo()
    assert plan.read_text(encoding="utf-8") == "caption"
    assert history.redo() is None


def test_a_new_edit_after_undo_drops_the_redo_branch(tmp_path: Path) -> None:
    plan = tmp_path / "p.json"
    plan.write_text("0", encoding="utf-8")
    history = PlanHistory(plan)
    _edit(plan, history, "a", "a")
    _edit(plan, history, "b", "b")
    history.undo()

    _edit(plan, history, "c", "c")

    assert not history.state().can_redo
    history.undo()
    assert plan.read_text(encoding="utf-8") == "a"


def test_pending_counts_from_what_the_video_shows(tmp_path: Path) -> None:
    plan = tmp_path / "p.json"
    plan.write_text("0", encoding="utf-8")
    history = PlanHistory(plan)
    _edit(plan, history, "a", "a")
    _edit(plan, history, "b", "b")
    history.rendered()  # "Update video": the video now shows "b"

    assert history.state().pending == 0
    history.undo()
    assert history.state().pending == 1  # undoing past the video is a change again
    history.redo()
    assert history.state().pending == 0


def test_old_versions_are_dropped_but_never_the_files_in_use(tmp_path: Path) -> None:
    plan = tmp_path / "p.json"
    plan.write_text("0", encoding="utf-8")
    history = PlanHistory(plan)
    history.rendered()
    for n in range(MAX_VERSIONS + 15):
        _edit(plan, history, str(n), str(n))
    history.rendered()
    for n in range(10):
        _edit(plan, history, f"x{n}", f"x{n}")

    files = list(history.folder.glob("v*.json"))
    assert len(files) <= MAX_VERSIONS + 15  # bounded
    for _ in range(10):
        history.undo()
    assert plan.read_text(encoding="utf-8") == str(MAX_VERSIONS + 14)
    assert history.state().pending == 0


def test_a_fresh_plan_has_no_history(tmp_path: Path) -> None:
    plan = tmp_path / "p.json"
    plan.write_text("0", encoding="utf-8")

    state = PlanHistory(plan).state()

    assert not state.can_undo and not state.can_redo and state.pending == 0
