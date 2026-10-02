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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from component_manifest import split_music, write_music_manifest

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
    """The app's wheels, and the music component's manifest (D-172).

    The app and the music-fitting libraries are resolved *together*, so the
    versions they share (numpy above all) agree. The installer then bundles
    only the app's share; the rest become ``components/music.json``, fetched
    the first time someone uses their own music track.
    """
    step("dependencies, as Windows wheels")
    wheels = WORK / "wheels"
    music = WORK / "music-wheels"
    for folder in (wheels, music):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
    major_minor = ".".join(python_version.split(".")[:2])
    target = [
        "--only-binary=:all:",
        "--platform", "win_amd64",
        "--python-version", major_minor,
        "--implementation", "cp",
    ]
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "--quiet", f"{wheel}[app,music]",
         "--dest", str(wheels), *target],
        check=True,
    )
    alone = WORK / "app-alone"
    shutil.rmtree(alone, ignore_errors=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "--quiet", f"{wheel}[app]",
         "--dest", str(alone), *target],
        check=True,
    )
    split_music(wheels, alone, music)
    shutil.rmtree(alone, ignore_errors=True)
    write_music_manifest(music, WORK / "components" / "music.json")

    total = sum(path.stat().st_size for path in wheels.iterdir()) / 1_000_000
    left = sum(path.stat().st_size for path in music.iterdir()) / 1_000_000
    print(f"  {len(list(wheels.iterdir()))} wheels, {total:.0f} MB in the installer")
    print(f"  {len(list(music.iterdir()))} music-fitting wheels, {left:.0f} MB on demand")
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
    {rel(WORK / "components")} > $INSTDIR
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


#: What ``_tkinter`` needs beside it, from the Python that runs pynsist. pynsist
#: copies ``_tkinter.pyd`` and the ``tkinter`` package but not these, so v0.1.0
#: could not open its "Voxframe is running" window and ran with nothing on
#: screen (D-164). ``zlib1.dll`` is a dependency of ``tcl86t.dll``.
TK_DLLS = ("tcl86t.dll", "tk86t.dll", "zlib1.dll")
TK_LIBRARIES = ("tcl8.6", "tk8.6")


def add_tk(pynsist_python: Path, pkgs: Path) -> None:
    """Put the Tcl/Tk DLLs and script libraries beside ``_tkinter.pyd``.

    The launcher points Tcl at ``pkgs/lib`` (``prepare_tk``).
    """
    base = Path(
        subprocess.run(
            [str(pynsist_python), "-c", "import sys; print(sys.base_prefix)"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    )
    if not (pkgs / "_tkinter.pyd").is_file():
        raise SystemExit("pynsist did not copy _tkinter.pyd; check [Include] packages")
    for name in TK_DLLS:
        shutil.copy2(base / "DLLs" / name, pkgs / name)
    for name in TK_LIBRARIES:
        target = pkgs / "lib" / name
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(base / "tcl" / name, target)
    print(f"  Tcl/Tk from {base}")


#: Where each section begins, in pynsist's template; the running-app check goes
#: first in both, before any file is touched.
_INSTALL_SECTION = 'Section "!${PRODUCT_NAME}" sec_app\n'
_UNINSTALL_SECTION = 'Section "Uninstall"\n'

#: The check itself, once for the installer and once for the uninstaller.
#: A running Voxframe holds its ``pythonw.exe`` open, and Windows refuses to
#: open a running program for writing -- so that is the test, with no process
#: listing and no console window. Replacing files under a running app fails
#: partway, or leaves a mix of two versions; v0.1.1 did not check (D-168).
_NOT_RUNNING = r"""
!macro VOXFRAME_NOT_RUNNING un
Function ${un}VoxframeNotRunning
  IfFileExists "$INSTDIR\Python\pythonw.exe" 0 free
  check:
    ClearErrors
    FileOpen $0 "$INSTDIR\Python\pythonw.exe" a
    IfErrors running
    FileClose $0
    Goto free
  running:
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
      "Voxframe is running.$\r$\n$\r$\nClick Quit in its small window, then click Retry." \
      /SD IDCANCEL IDRETRY check
    SetErrorLevel 3
    Abort "Voxframe is running. Quit it, then try again."
  free:
    Return
FunctionEnd
!macroend
!insertmacro VOXFRAME_NOT_RUNNING ""
!insertmacro VOXFRAME_NOT_RUNNING "un."
"""


def patch_script(text: str) -> str:
    """pynsist's installer script, with Voxframe's two changes.

    - ``/D=<folder>`` is kept (D-158).
    - The installer and the uninstaller both stop, with a message, while
      Voxframe is running (D-168).

    Raises:
        SystemExit: If pynsist's template has changed so a patch would not
            land where it must.
    """
    if (
        text.count(_ONINIT) != 1
        or "StrCpy $cmdLineInstallDir $1" not in text
        or text.count(_INSTALL_SECTION) != 1
        or text.count(_UNINSTALL_SECTION) != 1
    ):
        raise SystemExit("pynsist's installer script has changed; review patch_script")
    text = text.replace(_ONINIT, _REMEMBER_D)
    text = text.replace(_INSTALL_SECTION, _INSTALL_SECTION + "  Call VoxframeNotRunning\n")
    text = text.replace(_UNINSTALL_SECTION, _UNINSTALL_SECTION + "  Call un.VoxframeNotRunning\n")
    return text + _NOT_RUNNING


def build(config: Path, pynsist_python: Path, nsis: Path) -> Path:
    step("installer")
    subprocess.run(
        [str(pynsist_python), "-m", "nsist", "--no-makensis", str(config)], cwd=WORK, check=True
    )
    add_tk(pynsist_python, WORK / "nsis" / "pkgs")
    script = WORK / "nsis" / "installer.nsi"
    script.write_text(patch_script(script.read_text(encoding="utf-8")), encoding="utf-8")
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
