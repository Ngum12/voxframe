"""Guard against process-wide working-directory changes in render code.

``filter_path_context`` handles awkward paths by running FFmpeg from the file's
directory (D-021). The tempting shortcut is ``os.chdir`` before
``subprocess.run``. That must never appear: from Phase 8 the API runs concurrent
render jobs in one process, and ``os.chdir`` is process-global and not
thread-safe, so one job would silently corrupt another's relative paths. The
resulting failures would be timing-dependent and effectively unreproducible.

The correct mechanism is ``subprocess.run(cwd=...)``, which sets the *child*
process's directory only.

These tests parse the AST rather than grepping, so they cannot be fooled by the
string "chdir" in a comment or docstring, and they catch aliased imports such as
``from os import chdir``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RENDER_ROOT = Path(__file__).resolve().parents[2] / "src" / "voxframe"

#: Functions that mutate process-wide state and must not appear in render code.
FORBIDDEN_CALLS = {
    ("os", "chdir"),
    ("os", "fchdir"),
    ("pathlib", "Path", "cwd"),  # reading cwd implies depending on it
}


def _python_files() -> list[Path]:
    return sorted(RENDER_ROOT.rglob("*.py"))


def _describe(node: ast.AST) -> str:
    """Render a dotted name from an attribute or name node."""
    parts: list[str] = []
    current: ast.AST | None = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


class TestNoChdir:
    def test_no_chdir_in_render_code(self) -> None:
        """os.chdir anywhere in the package is a concurrency bug (D-021)."""
        offenders: list[str] = []

        for path in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = _describe(node.func)
                if name in {"os.chdir", "chdir", "os.fchdir"}:
                    rel = path.relative_to(RENDER_ROOT.parent.parent)
                    offenders.append(f"{rel}:{node.lineno} calls {name}()")

        assert not offenders, (
            "Process-wide directory changes found in render code:\n"
            + "\n".join(f"  {o}" for o in offenders)
            + "\n\nUse subprocess.run(cwd=...) instead, via "
            "voxframe.render.ffpath.run_ffmpeg. os.chdir is process-global and "
            "not thread-safe; concurrent render jobs (Phase 8) would corrupt "
            "each other's relative paths. See DECISIONS.md D-021."
        )

    def test_chdir_not_imported(self) -> None:
        """`from os import chdir` would evade a call-site check by name."""
        offenders: list[str] = []

        for path in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "os":
                    for alias in node.names:
                        if alias.name in {"chdir", "fchdir"}:
                            rel = path.relative_to(RENDER_ROOT.parent.parent)
                            offenders.append(f"{rel}:{node.lineno} imports {alias.name}")

        assert not offenders, (
            "Directory-changing functions imported:\n" + "\n".join(f"  {o}" for o in offenders)
        )


class TestSubprocessDiscipline:
    def test_subprocess_calls_are_centralised(self) -> None:
        """FFmpeg invocation belongs in runner.py and probe.py only.

        Concentrating it keeps the cwd contract enforceable in one place. A new
        call site elsewhere would bypass run_ffmpeg and could reintroduce the
        chdir shortcut.
        """
        # api/desktop.py runs the platform's own "open this folder" command
        # (open, xdg-open), not FFmpeg (D-156).
        allowed = {"render/ffpath/runner.py", "render/encode/probe.py", "api/desktop.py"}
        offenders: list[str] = []

        for path in _python_files():
            rel = path.relative_to(RENDER_ROOT).as_posix()
            if rel in allowed:
                continue

            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = _describe(node.func)
                    if name.startswith("subprocess."):
                        offenders.append(f"{rel}:{node.lineno} calls {name}()")

        assert not offenders, (
            "subprocess used outside the approved modules:\n"
            + "\n".join(f"  {o}" for o in offenders)
            + f"\n\nUse voxframe.render.ffpath.run_ffmpeg. Approved: {sorted(allowed)}"
        )

    def test_runner_passes_cwd_to_child(self) -> None:
        """The cwd argument must reach subprocess.run, not os.chdir."""
        source = (RENDER_ROOT / "render" / "ffpath" / "runner.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _describe(node.func) == "subprocess.run"
        ]
        assert len(calls) == 1, f"expected exactly one subprocess.run, found {len(calls)}"

        keywords = {kw.arg for kw in calls[0].keywords}
        assert "cwd" in keywords, "run_ffmpeg must pass cwd to the child process"


class TestRunnerBehaviour:
    """Behavioural counterpart to the static checks above.

    Uses a stand-in script rather than real FFmpeg: these assert the runner's
    own contract (cwd isolation, error reporting), which holds whether or not
    FFmpeg is installed.
    """

    @staticmethod
    def _fake_ffmpeg(tmp_path: Path, body: str) -> str:
        """Write a script that stands in for ffmpeg.

        It must tolerate the ``-hide_banner`` flag the runner prepends, which
        is why the interpreter cannot be invoked directly.
        """
        import sys
        import textwrap

        script = tmp_path / "fake_ffmpeg.py"
        script.write_text(textwrap.dedent(body), encoding="utf-8")

        if sys.platform == "win32":
            launcher = tmp_path / "fake_ffmpeg.cmd"
            lines = ["@echo off", f'"{sys.executable}" "{script}" %*', ""]
            launcher.write_text("\r\n".join(lines), encoding="utf-8", newline="")
        else:
            launcher = tmp_path / "fake_ffmpeg.sh"
            lines = ["#!/bin/sh", f'exec "{sys.executable}" "{script}" "$@"', ""]
            launcher.write_text("\n".join(lines), encoding="utf-8")
            launcher.chmod(0o755)

        return str(launcher)

    def test_cwd_reaches_child_without_moving_parent(self, tmp_path: Path) -> None:
        """The whole point of D-021: the child moves, the parent does not."""
        import os

        from voxframe.render.ffpath.runner import run_ffmpeg

        fake = self._fake_ffmpeg(tmp_path, "import os; print(os.getcwd())")
        target = tmp_path / "workdir"
        target.mkdir()

        before = Path.cwd()
        result = run_ffmpeg(fake, ["-i", "in.mp4"], cwd=target, check=False)

        assert Path.cwd() == before, "parent process directory changed"
        assert os.path.realpath(result.stdout.strip()) == os.path.realpath(target)

    def test_no_cwd_inherits_parent_directory(self, tmp_path: Path) -> None:
        import os

        from voxframe.render.ffpath.runner import run_ffmpeg

        fake = self._fake_ffmpeg(tmp_path, "import os; print(os.getcwd())")
        result = run_ffmpeg(fake, ["-i", "in.mp4"], check=False)

        assert os.path.realpath(result.stdout.strip()) == os.path.realpath(Path.cwd())

    def test_concurrent_calls_do_not_interfere(self, tmp_path: Path) -> None:
        """Two jobs with different cwds must not affect each other (Phase 8).

        This is the scenario os.chdir would break: with a process-global
        directory change, whichever thread ran last would win and the other
        would resolve its paths in the wrong place.
        """
        import os
        from concurrent.futures import ThreadPoolExecutor

        from voxframe.render.ffpath.runner import run_ffmpeg

        fake = self._fake_ffmpeg(tmp_path, "import os; print(os.getcwd())")
        dirs = []
        for i in range(4):
            d = tmp_path / f"job{i}"
            d.mkdir()
            dirs.append(d)

        def job(d: Path) -> str:
            return run_ffmpeg(fake, ["-i", "in.mp4"], cwd=d, check=False).stdout.strip()

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(job, dirs))

        for expected, actual in zip(dirs, results, strict=True):
            assert os.path.realpath(actual) == os.path.realpath(expected)

    def test_failure_includes_stderr(self, tmp_path: Path) -> None:
        """FFmpeg's stderr is usually the only clue; it must not be swallowed."""
        from voxframe.render.ffpath.runner import FFmpegError, run_ffmpeg

        fake = self._fake_ffmpeg(
            tmp_path,
            "import sys; sys.stderr.write('Invalid argument\\n'); sys.exit(3)",
        )

        with pytest.raises(FFmpegError) as exc:
            run_ffmpeg(fake, ["-i", "missing.mp4"])

        assert exc.value.returncode == 3
        assert "Invalid argument" in str(exc.value)
        assert "Command:" in str(exc.value)

    def test_check_false_returns_instead_of_raising(self, tmp_path: Path) -> None:
        from voxframe.render.ffpath.runner import run_ffmpeg

        fake = self._fake_ffmpeg(tmp_path, "import sys; sys.exit(1)")
        result = run_ffmpeg(fake, [], check=False)

        assert not result.ok
        assert result.returncode == 1
