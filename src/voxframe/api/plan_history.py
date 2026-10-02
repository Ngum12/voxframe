"""Undo and redo for every edit to a video's plan (D-182).

The studio's Ctrl+Z must undo whatever was changed last: a picture, a
caption, a card, the camera, the music, the mix. Every one of those is saved
to the scene plan the moment it is made, so the history is kept where the
plan is: each saved version beside it, and a pointer to the current one.

- **Saving** an edit drops any versions after the current one (the redo
  branch) and adds the new plan as the newest.
- **Undo and redo** move the pointer and write that version back as the plan.
- **"Changes not yet in the video"** is the distance between the current
  version and the one last rendered, so undoing back to what the video shows
  leaves none, and undoing past it counts as changes again.

Versions are whole plans: small (a 17-minute talk's is under a megabyte) and
simple to restore exactly. The oldest are dropped after MAX_VERSIONS.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

__all__ = ["MAX_VERSIONS", "History", "PlanHistory", "replace_retrying"]

#: How many versions are kept; older ones are dropped, never the rendered one.
MAX_VERSIONS = 60

_lock = threading.Lock()


def replace_retrying(source: Path, target: Path) -> None:
    """Move ``source`` over ``target``, retrying briefly on Windows.

    A virus scanner or the search indexer can hold a just-written file open for
    a moment, and Windows then refuses the replace ("Access is denied"). Measured
    in this module's tests under rapid saves; an edit must never fail for it.
    """
    for attempt in range(40):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.025)


@dataclass(frozen=True)
class History:
    """Where the plan stands in its history, for the studio to show."""

    can_undo: bool
    can_redo: bool
    pending: int
    undo_label: str
    redo_label: str


class PlanHistory:
    """The versions of one plan, in ``<plan>.history/`` beside it."""

    def __init__(self, plan_path: Path) -> None:
        self.plan = plan_path
        self.folder = plan_path.with_name(plan_path.name + ".history")
        self.index_file = self.folder / "index.json"

    # --- the index -----------------------------------------------------------

    def _load(self) -> dict:  # type: ignore[type-arg]
        if self.index_file.is_file():
            loaded: dict = json.loads(self.index_file.read_text(encoding="utf-8"))  # type: ignore[type-arg]
            return loaded
        return {"versions": [], "labels": [], "current": -1, "rendered": -1, "next": 0}

    def _save(self, index: dict) -> None:  # type: ignore[type-arg]
        self.folder.mkdir(parents=True, exist_ok=True)
        partial = self.index_file.with_suffix(".partial")
        partial.write_text(json.dumps(index), encoding="utf-8")
        replace_retrying(partial, self.index_file)

    def _add(self, index: dict, data: bytes, label: str) -> None:  # type: ignore[type-arg]
        name = f"v{index['next']:05d}.json"
        index["next"] += 1
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / name).write_bytes(data)
        index["versions"].append(name)
        index["labels"].append(label)
        index["current"] = len(index["versions"]) - 1

    def _start(self, index: dict) -> None:  # type: ignore[type-arg]
        """The plan as it was before any edit: the first version, as rendered."""
        if index["versions"] or not self.plan.is_file():
            return
        self._add(index, self.plan.read_bytes(), "")
        index["rendered"] = 0

    def _trim(self, index: dict) -> None:  # type: ignore[type-arg]
        while (
            len(index["versions"]) > MAX_VERSIONS
            and index["rendered"] != 0
            and index["current"] > 0
        ):
            (self.folder / index["versions"].pop(0)).unlink(missing_ok=True)
            index["labels"].pop(0)
            index["current"] -= 1
            index["rendered"] -= 1

    def _state(self, index: dict) -> History:  # type: ignore[type-arg]
        current, rendered = index["current"], index["rendered"]
        labels = index["labels"]
        return History(
            can_undo=current > 0,
            can_redo=0 <= current < len(index["versions"]) - 1,
            pending=abs(current - rendered) if rendered >= 0 else max(0, current),
            undo_label=labels[current] if current > 0 else "",
            redo_label=labels[current + 1] if 0 <= current < len(labels) - 1 else "",
        )

    def _restore(self, index: dict) -> None:  # type: ignore[type-arg]
        data = (self.folder / index["versions"][index["current"]]).read_bytes()
        partial = self.plan.with_suffix(self.plan.suffix + ".partial")
        partial.write_bytes(data)
        replace_retrying(partial, self.plan)

    # --- what the app does ----------------------------------------------------------

    def record(self, label: str = "a change") -> History:
        """Note that the plan file was just saved with an edit."""
        with _lock:
            index = self._load()
            if not index["versions"]:
                # Started before history existed: what is on disk now is the
                # edit, so there is nothing earlier to go back to.
                self._add(index, self.plan.read_bytes(), "")
                index["rendered"] = -1
                self._save(index)
                return self._state(index)
            for name in index["versions"][index["current"] + 1 :]:
                (self.folder / name).unlink(missing_ok=True)
            del index["versions"][index["current"] + 1 :]
            del index["labels"][index["current"] + 1 :]
            if index["rendered"] > index["current"]:
                index["rendered"] = -1  # the rendered version was in the dropped branch
            self._add(index, self.plan.read_bytes(), label)
            self._trim(index)
            self._save(index)
            return self._state(index)

    def begin(self) -> None:
        """Before an edit is saved: make sure the unedited plan is the first version."""
        with _lock:
            index = self._load()
            if not index["versions"]:
                self._start(index)
                self._save(index)

    def undo(self) -> History | None:
        with _lock:
            index = self._load()
            if index["current"] <= 0:
                return None
            index["current"] -= 1
            self._restore(index)
            self._save(index)
            return self._state(index)

    def redo(self) -> History | None:
        with _lock:
            index = self._load()
            if not 0 <= index["current"] < len(index["versions"]) - 1:
                return None
            index["current"] += 1
            self._restore(index)
            self._save(index)
            return self._state(index)

    def rendered(self) -> None:
        """The plan as it stands is being rendered: it is what the video will show."""
        with _lock:
            index = self._load()
            if not index["versions"]:
                self._start(index)
            index["rendered"] = index["current"]
            self._save(index)

    def rendered_version(self) -> Path | None:
        """The plan as the video shows it, if the history knows it."""
        with _lock:
            index = self._load()
            rendered = index["rendered"]
            if 0 <= rendered < len(index["versions"]):
                return self.folder / str(index["versions"][rendered])
            return None

    def state(self) -> History:
        with _lock:
            index = self._load()
            if not index["versions"]:
                return History(False, False, 0, "", "")
            return self._state(index)
