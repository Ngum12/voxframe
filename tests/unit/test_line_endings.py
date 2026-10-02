"""No change alters a file's line endings (the D-177 commit did, by accident).

A script that rewrites a file on Windows can turn LF into CRLF, or the other
way round, and the whole file then shows as changed: its real edit hides in
the diff, and history blames every line on it. ``.gitattributes`` makes new
text files LF and keeps the files that were CRLF exactly as they are; this
test checks the result: for every file a commit would contain, the line
endings Git would store are the same kind (LF, CRLF or mixed) as on the main
branch. New files must be LF unless an attribute says otherwise.

Skipped outside a Git checkout (a source archive has no history to compare).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _git(*args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=REPO, input=data, capture_output=True, check=True
    ).stdout


def _style(data: bytes) -> str:
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if not crlf:
        return "LF"
    return "CRLF" if not lf else "mixed"


def _reference() -> str:
    """The main branch where it differs from HEAD (a feature branch), else HEAD."""
    for branch in ("master", "main"):
        try:
            return _git("merge-base", "HEAD", branch).decode().strip()
        except subprocess.CalledProcessError:
            continue
    return "HEAD"


def _attributes(names: list[str]) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {name: {} for name in names}
    output = _git(
        "check-attr",
        "-z",
        "--stdin",
        "text",
        "eol",
        "binary",
        data="\0".join(names).encode() + b"\0",
    )
    parts = output.decode("utf-8", "replace").split("\0")
    for name, attribute, value in zip(parts[0::3], parts[1::3], parts[2::3], strict=False):
        if name in found:
            found[name][attribute] = value
    return found


def _stored(data: bytes, attributes: dict[str, str]) -> bytes:
    """What Git would store for these bytes: eol-normalised text becomes LF.

    ``-text`` wins over an ``eol`` inherited from the file's type: Git then
    stores the bytes exactly as they are.
    """
    if attributes.get("text") == "unset":
        return data
    if attributes.get("text") == "set" or attributes.get("eol") in ("lf", "crlf"):
        return data.replace(b"\r\n", b"\n")
    return data


@pytest.fixture(scope="module")
def changes() -> list[str]:
    try:
        _git("rev-parse", "--is-inside-work-tree")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("not a Git checkout")
    reference = _reference()
    tracked = [n for n in _git("ls-files", "-z").decode().split("\0") if n]
    untracked = [
        n
        for n in _git("ls-files", "-z", "--others", "--exclude-standard").decode().split("\0")
        if n
    ]
    names = [n for n in tracked + untracked if (REPO / n).is_file()]
    before = {
        line.split(b"\t", 1)[1].decode(): line.split()[2].decode()
        for line in _git("ls-tree", "-r", "-z", reference).split(b"\0")
        if line
    }
    attributes = _attributes(names)
    problems = []
    for name in names:
        attrs = attributes[name]
        if attrs.get("binary") == "set" or (attrs.get("text") == "unset" and name not in before):
            continue
        data = (REPO / name).read_bytes()
        if b"\0" in data[:8000]:
            continue  # binary
        now = _style(_stored(data, attrs))
        if name in before:
            was = _style(_git("cat-file", "blob", before[name]))
            if now != was:
                problems.append(f"{name}: {was} on the main branch, {now} now")
        elif now != "LF":
            problems.append(f"{name}: a new file with {now} line endings (new files are LF)")
    return problems


def test_no_file_changes_its_line_endings(changes: list[str]) -> None:
    assert not changes, (
        "These files' line endings would change; rewrite them with their original "
        "endings (the edit itself is fine):\n  " + "\n  ".join(changes)
    )


def test_the_style_is_read_correctly() -> None:
    assert _style(b"a\nb\n") == "LF"
    assert _style(b"a\r\nb\r\n") == "CRLF"
    assert _style(b"a\r\nb\n") == "mixed"
    assert _stored(b"a\r\n", {"text": "set", "eol": "lf"}) == b"a\n"
    assert _stored(b"a\r\n", {"text": "unset"}) == b"a\r\n"
    assert _stored(b"a\r\n", {"text": "unset", "eol": "lf"}) == b"a\r\n"
