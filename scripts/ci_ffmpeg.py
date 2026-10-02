"""Put FFmpeg on the PATH of a CI runner: the build each installer ships (D-195).

The tests should run against the FFmpeg people actually get, not whatever a
runner happens to have (Ubuntu's is 6.1, which rejects options the renderer
relies on). So:

- **Windows:** gyan.dev's FFmpeg essentials build, the one the installer
  bundles, checked against the SHA-256 the installer build pins.
- **macOS (Apple Silicon):** Martin Riedl's static build, the one the Mac app
  bundles, checked the same way.
- **Linux:** no installer ships one, so BtbN's static build from the same
  FFmpeg release branch (9.0) is used. Its file is replaced as the branch
  gets fixes, so it cannot be pinned by checksum; it only runs tests.

    python scripts/ci_ffmpeg.py            # downloads, unpacks, prints the folder
    python scripts/ci_ffmpeg.py --github   # and adds it to the job's PATH
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

LINUX_BRANCH = "9.0"
LINUX_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    f"ffmpeg-n{LINUX_BRANCH}-latest-linux64-gpl-{LINUX_BRANCH}.tar.xz"
)
USER_AGENT = "voxframe-ci (+https://github.com/Ngum12/voxframe)"


def _download(url: str, target: Path, sha256: str | None = None) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)
    if sha256 is not None:
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != sha256:
            raise SystemExit(f"{target.name} does not match its pinned SHA-256 ({digest})")
    return target


def _windows(folder: Path, scratch: Path) -> None:
    import build_windows_installer as windows

    archive = _download(windows.FFMPEG_ZIP, scratch / "ffmpeg.zip", windows.FFMPEG_SHA256)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            if Path(member).name in {"ffmpeg.exe", "ffprobe.exe"}:
                (folder / Path(member).name).write_bytes(bundle.read(member))


def _macos(folder: Path, scratch: Path) -> None:
    import build_macos_app as mac

    for name, sha256 in mac.FFMPEG_FILES.items():
        archive = _download(f"{mac.FFMPEG_BASE}/{name}", scratch / name, sha256)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(folder)
    for binary in ("ffmpeg", "ffprobe"):
        (folder / binary).chmod(0o755)


def _linux(folder: Path, scratch: Path) -> None:
    archive = _download(LINUX_URL, scratch / "ffmpeg.tar.xz")
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            if member.isfile() and Path(member.name).name in {"ffmpeg", "ffprobe"}:
                source = bundle.extractfile(member)
                assert source is not None
                target = folder / Path(member.name).name
                target.write_bytes(source.read())
                target.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, default=Path.home() / "ffmpeg-ci")
    parser.add_argument("--github", action="store_true", help="add it to the job's PATH")
    args = parser.parse_args()

    folder = args.dest.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    system = platform.system()
    with tempfile.TemporaryDirectory() as scratch:
        if system == "Windows":
            _windows(folder, Path(scratch))
        elif system == "Darwin":
            _macos(folder, Path(scratch))
        else:
            _linux(folder, Path(scratch))

    print(folder)
    if args.github:
        with Path(os.environ["GITHUB_PATH"]).open("a", encoding="utf-8") as path_file:
            path_file.write(f"{folder}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
