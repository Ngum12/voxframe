"""No subprocess may open a console window on Windows (D-165).

The installed app runs under ``pythonw``, with no console. Windows gives every
console program such a process starts -- FFmpeg, FFprobe -- a console window of
its own: v0.1.0 flashed one for every FFmpeg call in a render. Each call must
pass ``creationflags=NO_WINDOW``; these tests read the source, so a new call
without it fails here rather than on someone's screen.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from voxframe.processes import NO_WINDOW

SOURCE = Path(__file__).resolve().parents[2] / "src" / "voxframe"

#: Every way to start a process from Python's standard library.
_STARTERS = {
    "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "subprocess.getoutput", "subprocess.getstatusoutput",
    "os.system", "os.popen", "os.execv", "os.execvp", "os.spawnv", "os.spawnl",
    "os.posix_spawn", "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell",
}
#: The ones that take ``creationflags``; the rest cannot, so must not be used.
_WITH_FLAGS = {
    "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output",
}


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    return ""


def _process_calls() -> list[tuple[Path, int, str, ast.Call]]:
    found = []
    for path in sorted(SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and (name := _name(node.func)) in _STARTERS:
                found.append((path.relative_to(SOURCE), node.lineno, name, node))
    return found


def test_there_are_process_calls_to_check() -> None:
    """Guards the guard: an empty scan would pass whatever the code does."""
    assert len(_process_calls()) >= 5


def test_every_process_call_hides_its_window() -> None:
    offenders = []
    for path, line, name, call in _process_calls():
        if name not in _WITH_FLAGS:
            offenders.append(f"{path}:{line} {name} cannot hide its console window")
            continue
        flags = next((k.value for k in call.keywords if k.arg == "creationflags"), None)
        if flags is None or _name(flags) != "NO_WINDOW":
            offenders.append(f"{path}:{line} {name} without creationflags=NO_WINDOW")

    assert not offenders, "\n".join(offenders)


@pytest.mark.skipif(sys.platform != "win32", reason="the flag exists only on Windows")
def test_the_flag_is_windows_no_window() -> None:
    assert NO_WINDOW == subprocess.CREATE_NO_WINDOW == 0x08000000


@pytest.mark.skipif(sys.platform == "win32", reason="other platforms")
def test_elsewhere_the_flag_is_nothing() -> None:
    assert NO_WINDOW == 0


def test_ffmpeg_really_gets_the_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The runner every FFmpeg call goes through (D-020), at run time."""
    from voxframe.render.ffpath import runner

    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    runner.run_ffmpeg("ffmpeg", ["-version"], cwd=tmp_path)

    assert seen.get("creationflags") == NO_WINDOW
