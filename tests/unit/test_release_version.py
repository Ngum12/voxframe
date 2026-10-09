"""The downloadable release, package and studio identify the same version."""
import json
import tomllib
from pathlib import Path

from voxframe import __version__


def test_version_agrees_across_python_web_lock_and_release_notes():
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    package = json.loads((root / "web/package.json").read_text())
    lock = json.loads((root / "web/package-lock.json").read_text())
    assert project["project"]["version"] == __version__
    assert package["version"] == lock["version"] == lock["packages"][""]["version"] == __version__
    notes = (root / ".github/release-notes" / f"v{__version__}.md").read_text()
    assert notes.startswith(f"# VoxFrame {__version__}")
