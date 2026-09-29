"""Build the Windows installer (D-158).

One ``Voxframe-<version>-Windows-setup.exe``: its own Python, every library
Voxframe needs, FFmpeg, and a Start-menu shortcut that opens the app. The models
are not inside; the Getting-ready screen downloads them (D-157).

Made with pynsist (MIT) and NSIS (zlib licence), both build tools that are not
shipped. Steps:

1. Build Voxframe's wheel from this tree.
2. Download every dependency as a Windows wheel for the bundled Python version
   -- PyTorch from PyPI, which for Windows is the CPU build.
3. Download FFmpeg (gyan.dev "essentials" build, GPLv3), pinned to one version
   and checked against a pinned SHA-256, so the source attached to the release
   (scripts/ffmpeg_sources.py, D-162) is the source of what ships. It ships as
   a separate program beside Voxframe with its licence and a source notice, the
   same arm's-length arrangement as the Docker image (D-113); see THIRD_PARTY_LICENSES.
4. Write the pynsist configuration and build the installer.
5. Run the release key check over everything the installer contains, before
   compression hides it (D-149).

    python scripts/build_windows_installer.py --nsis path/to/makensis-folder
        [--pynsist path/to/python-with-pynsist]

The installer is written to ``build/windows/`` (gitignored). It is not signed
(owner's decision for v1); see docs/install-windows.md for what that means.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORK = REPO_ROOT / "build" / "windows"
FFMPEG_VERSION = "9.0.2"
FFMPEG_ZIP = (
    f"https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-{FFMPEG_VERSION}-essentials_build.zip"
)
FFMPEG_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
FFMPEG_LIBRARIES = "https://www.gyan.dev/ffmpeg/builds/#libraries"
RELEASES = "https://github.com/Ngum12/voxframe/releases"


def step(title: str) -> None:
    print(f"\n== {title} ==", flush=True)


def version() -> str:
    for line in (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    raise SystemExit("no version in pyproject.toml")


def build_wheel() -> Path:
    step("Voxframe wheel")
    out = WORK / "voxframe-wheel"
    shutil.rmtree(out, ignore_errors=True)
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
        cwd=REPO_ROOT, check=True, capture_output=True,
    )
    wheel = next(out.glob("voxframe-*.whl"))
    print(f"  {wheel.name}")
    return wheel


def download_wheels(wheel: Path, python_version: str) -> Path:
    step("dependencies, as Windows wheels")
    wheels = WORK / "wheels"
    shutil.rmtree(wheels, ignore_errors=True)
    wheels.mkdir(parents=True)
    major_minor = ".".join(python_version.split(".")[:2])
    subprocess.run(
        [
            sys.executable, "-m", "pip", "download", "--quiet",
            f"{wheel}[app]",
            "--dest", str(wheels),
            "--only-binary=:all:",
            "--platform", "win_amd64",
            "--python-version", major_minor,
            "--implementation", "cp",
        ],
        check=True,
    )
    total = sum(path.stat().st_size for path in wheels.iterdir()) / 1_000_000
    print(f"  {len(list(wheels.iterdir()))} wheels, {total:.0f} MB")
    return wheels


def download_ffmpeg() -> Path:
    step("FFmpeg")
    folder = WORK / "ffmpeg"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    archive = WORK / "ffmpeg.zip"
    urllib.request.urlretrieve(FFMPEG_ZIP, archive)
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != FFMPEG_SHA256:
        raise SystemExit(f"FFmpeg download does not match its pinned SHA-256 ({actual})")
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            name = Path(member).name
            if name in {"ffmpeg.exe", "ffprobe.exe", "LICENSE", "README.txt"}:
                (folder / name).write_bytes(bundle.read(member))
    (folder / "LICENSE").rename(folder / "LICENSE-FFmpeg.txt")
    (folder / "SOURCE.txt").write_text(
        f"""FFmpeg {FFMPEG_VERSION}, "essentials" build by gyan.dev
===================================================

This folder holds FFmpeg, a separate program that Voxframe runs to make video.
It is licensed under the GNU General Public License, version 3 (LICENSE-FFmpeg.txt).
Voxframe itself is licensed under the Apache License 2.0 and carries no GPL
obligation: it runs FFmpeg as a separate process and does not link to it.

Where this build came from: {FFMPEG_ZIP}
Its SHA-256, checked when this installer was built: {FFMPEG_SHA256}

Source code
-----------
The complete source of FFmpeg {FFMPEG_VERSION}, the version in this folder, is
attached to the same GitHub release as this installer (ffmpeg-{FFMPEG_VERSION}.tar.xz),
with a list of the libraries compiled into this build and where the build
provider publishes their source (ffmpeg-{FFMPEG_VERSION}-libraries.txt):
{RELEASES}/tag/v{version()}

The build provider's own source links:
- FFmpeg {FFMPEG_VERSION}: https://ffmpeg.org/releases/ffmpeg-{FFMPEG_VERSION}.tar.xz
- The libraries compiled in, each linked to its project: {FFMPEG_LIBRARIES}
- README.txt in this folder: the build's configuration and every library's version
""",
        encoding="utf-8",
    )
    archive.unlink()
    print(f"  FFmpeg {FFMPEG_VERSION}, SHA-256 verified")
    return folder


def write_config(wheels: Path, ffmpeg: Path, python_version: str, name: str) -> Path:
    step("installer configuration")
    config = WORK / "installer.cfg"

    def rel(path: Path) -> str:
        return os.path.relpath(path, WORK).replace("\\", "/")

    config.write_text(
        f"""[Application]
name=Voxframe
version={version()}
publisher=Ngum12
entry_point=voxframe.launcher:main
icon={rel(REPO_ROOT / "src" / "voxframe" / "assets" / "icon" / "voxframe.ico")}
console=false
license_file={rel(REPO_ROOT / "LICENSE")}

[Python]
version={python_version}
bitness=64
include_msvcrt=true

[Include]
local_wheels={rel(wheels)}/*.whl
packages=tkinter
    _tkinter
files={rel(ffmpeg)} > $INSTDIR
    {rel(REPO_ROOT / "THIRD_PARTY_LICENSES")} > $INSTDIR

[Build]
directory={rel(WORK / "nsis")}
installer_name={name}
""",
        encoding="utf-8",
    )
    return config


#: pynsist's multi-user support overwrites the folder given with NSIS's standard
#: ``/D=``, so a silent ``/S /D=<folder>`` install went to the default folder
#: (found by installing it, D-158). pynsist's own ``/INSTDIR=`` still works;
#: this makes ``/D=`` work too, by remembering it before the multi-user setup
#: runs. ``$INSTDIR`` is empty at that point unless ``/D=`` set it.
_ONINIT = "Function .onInit\n"
_REMEMBER_D = (
    "Function .onInit\n"
    "  ; /D=<folder> sets $INSTDIR before this runs; keep it (D-158).\n"
    "  StrCpy $cmdLineInstallDir $INSTDIR\n"
)


def build(config: Path, pynsist_python: Path, nsis: Path) -> Path:
    step("installer")
    subprocess.run(
        [str(pynsist_python), "-m", "nsist", "--no-makensis", str(config)], cwd=WORK, check=True
    )
    script = WORK / "nsis" / "installer.nsi"
    text = script.read_text(encoding="utf-8")
    if text.count(_ONINIT) != 1 or "StrCpy $cmdLineInstallDir $1" not in text:
        raise SystemExit("pynsist's installer script has changed; review the /D= patch")
    script.write_text(text.replace(_ONINIT, _REMEMBER_D), encoding="utf-8")
    subprocess.run([str(nsis / "makensis.exe"), "/V2", str(script)], check=True)
    installer = next((WORK / "nsis").glob("*.exe"))
    print(f"  {installer.relative_to(REPO_ROOT)}  {installer.stat().st_size / 1_000_000:.0f} MB")
    return installer


def key_check() -> None:
    step("key check")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "release_key_check.py"),
         str(WORK / "nsis"), str(WORK / "ffmpeg")],
        capture_output=True, text=True, check=False,
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        raise SystemExit("The key check failed. The installer may not be published.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nsis", type=Path, required=True, help="folder holding makensis.exe")
    parser.add_argument("--pynsist", type=Path, default=Path(sys.executable),
                        help="a Python with pynsist installed")
    parser.add_argument("--no-key-check", action="store_true",
                        help="for CI only; the key check runs locally before tagging")
    arguments = parser.parse_args()

    if platform.system() != "Windows":
        raise SystemExit("The Windows installer is built on Windows.")
    # pynsist copies tkinter from the Python that runs it, so that Python's
    # version is the one bundled.
    python_version = subprocess.run(
        [str(arguments.pynsist), "-c", "import platform; print(platform.python_version())"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    WORK.mkdir(parents=True, exist_ok=True)
    wheel = build_wheel()
    wheels = download_wheels(wheel, python_version)
    shutil.copy(wheel, wheels)
    ffmpeg = download_ffmpeg()
    name = f"Voxframe-{version()}-Windows-setup.exe"
    config = write_config(wheels, ffmpeg, python_version, name)
    installer = build(config, arguments.pynsist, arguments.nsis)
    if arguments.no_key_check:
        step("key check")
        print("skipped here: run locally before tagging (docs/RELEASING.md)")
    else:
        key_check()
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    print(f"\n{installer.name}\nSHA-256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
