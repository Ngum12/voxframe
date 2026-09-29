"""Build the release packages, check them, and prove the web app runs from them.

The Phase 8 packaging promise: ``pip install voxframe[app]``,
then ``voxframe web`` -- no Node toolchain, no separate install step. This
checks that promise against the built wheel rather than the source tree:

1. Rebuilds the web app, and stops if the committed bundle was stale -- the
   wheel ships the committed one (D-117).
2. Builds the wheel and the source archive into ``dist/``.
3. Fails if either package holds a file git does not track -- the owner's
   recordings, ``.env``, the library (D-150) -- and runs the release key
   check on both (D-113 decision 3, D-149).
4. Installs the wheel into a fresh virtual environment (dependencies from the
   system, so this does not download torch), starts ``voxframe web`` from a
   directory outside the repository, and checks the page, its script and the
   health route are served from the installed package.

    python scripts/build_release.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST = REPO_ROOT / "dist"
STATIC = REPO_ROOT / "src" / "voxframe" / "api" / "static"


def step(title: str) -> None:
    print(f"\n== {title} ==", flush=True)


def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, **kwargs)  # type: ignore[call-overload]


def rebuild_web() -> None:
    step("web app")
    npm = shutil.which("npm")
    if npm is None:
        print("npm not found: shipping the committed bundle as it is.")
        return
    run([npm, "run", "build"], cwd=REPO_ROOT / "web", capture_output=True)
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--", str(STATIC)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if changed:
        sys.exit(
            "The committed web bundle was out of date with web/src. Commit the "
            "rebuilt bundle in src/voxframe/api/static/ and run this again."
        )
    print("the committed bundle matches web/src")


def build_packages() -> list[Path]:
    step("packages")
    shutil.rmtree(DIST, ignore_errors=True)
    run([sys.executable, "-m", "build", "--outdir", str(DIST)], cwd=REPO_ROOT,
        capture_output=True)
    built = sorted(DIST.iterdir())
    for path in built:
        print(f"  {path.name}  {path.stat().st_size / 1_048_576:.1f} MB")
    return built


#: Files the build writes into the packages, which git never tracks.
_GENERATED = {"PKG-INFO"}


def only_tracked_files(packages: list[Path]) -> None:
    """Fail if a package holds any file git does not track (D-150).

    Everything personal on this machine is untracked: the owner's recordings
    in samples/private/, .env, the library. The first source archive held the
    owner's recording because the build tool honours only the root
    .gitignore. This does not depend on any ignore file.
    """
    import tarfile
    import zipfile

    step("only tracked files")
    tracked = set(
        subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
        ).stdout.decode("utf-8").split("\0")
    )
    strays: list[str] = []
    for package in packages:
        if package.suffix == ".whl":
            with zipfile.ZipFile(package) as archive:
                names = [
                    f"src/{name}" for name in archive.namelist()
                    if ".dist-info/" not in name
                ]
        else:
            with tarfile.open(package) as archive:
                names = [
                    member.name.split("/", 1)[1]
                    for member in archive.getmembers()
                    if member.isfile() and "/" in member.name
                ]
        strays += [
            f"{package.name}: {name}"
            for name in names
            if name not in tracked and Path(name).name not in _GENERATED
        ]
    if strays:
        print("\n".join(f"  {stray}" for stray in strays))
        sys.exit("Untracked files would ship. Nothing may be published.")
    print("every packaged file is tracked in git")


def key_check(paths: list[Path]) -> None:
    step("key check")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "release_key_check.py"),
         *map(str, paths)],
        capture_output=True, text=True, check=False,
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        sys.exit("The release key check failed. Nothing may be published.")


def smoke_test(wheel: Path) -> None:
    step("install and run")
    work = Path(tempfile.mkdtemp(prefix="vf-release-"))
    venv = work / "venv"
    run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)])
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    run([str(python), "-m", "pip", "install", "--quiet", "--no-deps", "--force-reinstall",
         str(wheel)], capture_output=True)

    where = "import voxframe, pathlib; print(pathlib.Path(voxframe.__file__).parent)"
    located = run(
        [str(python), "-c", where],
        cwd=work, capture_output=True,
    ).stdout.strip()
    if str(REPO_ROOT) in located:
        sys.exit(f"voxframe was imported from the repository, not the wheel: {located}")
    print(f"  installed at {located}")

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["VOXFRAME_CONFIG_DIR"] = str(work / "config")
    env["VOXFRAME_OUTPUT_PATH"] = str(work / "out")
    env["VOXFRAME_LIBRARY_PATH"] = str(work / "library")
    server = subprocess.Popen(
        [str(python), "-m", "voxframe.cli.main", "web", "--no-open"],
        cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        url = ""
        deadline = time.monotonic() + 90
        assert server.stdout is not None
        while time.monotonic() < deadline and not url:
            line = server.stdout.readline()
            match = re.search(r"(http://127\.0\.0\.1:\d+)/\?token=", line)
            if match:
                url = match.group(1)
        if not url:
            sys.exit("voxframe web did not start from the installed wheel")

        page = urllib.request.urlopen(f"{url}/").read().decode("utf-8")
        script = re.search(r'src="\.?(/assets/[^"]+\.js)"', page)
        if script is None:
            sys.exit("the installed app served a page with no script")
        urllib.request.urlopen(f"{url}{script.group(1)}").read()
        urllib.request.urlopen(f"{url}/api/health").read()
        print(f"  served the page, {script.group(1)} and /api/health from the wheel")
    finally:
        server.kill()
        server.wait(timeout=15)
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-key-check",
        action="store_true",
        help="for CI only: the key check needs this machine's keys, which never go to "
        "CI, so it runs locally before a release is tagged (docs/RELEASING.md)",
    )
    arguments = parser.parse_args()

    rebuild_web()
    built = build_packages()
    only_tracked_files(built)
    if arguments.no_key_check:
        step("key check")
        print("skipped here: run locally before tagging (docs/RELEASING.md)")
    else:
        key_check(built)
    wheel = next(path for path in built if path.suffix == ".whl")
    smoke_test(wheel)
    print("\nrelease packages built and checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
