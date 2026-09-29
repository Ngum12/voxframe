"""Build the macOS app for Apple Silicon: an early, unsigned build (D-161).

Runs on a GitHub Actions macOS runner (free for public repositories). Needs no
secrets and no Apple account. Produces ``Voxframe-<version>-macOS-arm64.dmg``
holding ``Voxframe.app``:

    Voxframe.app/Contents/
      Info.plist
      MacOS/Voxframe            a two-line script that starts the launcher
      Resources/python/         Python 3.12, relocatable (python-build-standalone)
      Resources/ffmpeg/         FFmpeg 9.0.2, static, GPL, with its licence notes
      Resources/voxframe.icns

Every download is pinned and checked against a SHA-256 recorded here. The app
is **ad-hoc signed** -- free, no identity -- because Apple Silicon runs no
native code without at least that; to Gatekeeper it is still an unidentified
developer's app, and docs/install-mac.md says how to open it. Before packing,
the bundle is tested: its own Python imports Voxframe and the models' libraries,
finds its own FFmpeg with libass, serves the app, and renders the 45-second
sonnet with the Whisper base model.

    python scripts/build_macos_app.py [--no-test]
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORK = REPO_ROOT / "build" / "macos"

PYTHON_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/20260924/"
    "cpython-3.12.14%2B20260924-aarch64-apple-darwin-install_only.tar.gz"
)
PYTHON_SHA256 = "9763f43db2481a6af36af82ec40302aab7a73632f880129d07a6e81aec846277"

FFMPEG_VERSION = "9.0.2"
FFMPEG_BASE = "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2"
FFMPEG_FILES = {
    "ffmpeg.zip": "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924",
    "ffprobe.zip": "fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6",
}
#: The build's own list of the libraries compiled in, with their versions.
FFMPEG_VERSIONS_SHA256 = "fc92572e752e09b20e7ff28d64fb7096f7bc1a0d589b2e40f17ac91b7fd0ca61"
FFMPEG_BUILD_SCRIPT = "https://git.martin-riedl.de/ffmpeg/build-script"
RELEASES = "https://github.com/Ngum12/voxframe/releases"
#: ffmpeg.martin-riedl.de refuses Python's default User-Agent (HTTP 403).
USER_AGENT = "voxframe-release (+https://github.com/Ngum12/voxframe)"
GPL3_URL = "https://www.gnu.org/licenses/gpl-3.0.txt"

SONNET = REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav"


def step(title: str) -> None:
    print(f"\n== {title} ==", flush=True)


def version() -> str:
    for line in (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    raise SystemExit("no version in pyproject.toml")


def fetch(url: str, target: Path, sha256: str) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:
        target.write_bytes(response.read())
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != sha256:
        raise SystemExit(f"{target.name} does not match its pinned SHA-256 (got {actual})")
    return target


def build_wheel() -> Path:
    step("Voxframe wheel")
    out = WORK / "wheel"
    shutil.rmtree(out, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
                   cwd=REPO_ROOT, check=True, capture_output=True)
    return next(out.glob("voxframe-*.whl"))


def assemble(wheel: Path) -> Path:
    app = WORK / "Voxframe.app"
    shutil.rmtree(app, ignore_errors=True)
    contents = app / "Contents"
    resources = contents / "Resources"
    (contents / "MacOS").mkdir(parents=True)
    resources.mkdir()

    step("Python")
    archive = fetch(PYTHON_URL, WORK / "python.tar.gz", PYTHON_SHA256)
    with tarfile.open(archive) as bundle:
        bundle.extractall(resources, filter="tar")  # holds one folder: python/
    archive.unlink()
    python = resources / "python" / "bin" / "python3"
    shown = subprocess.run([str(python), "--version"], capture_output=True, text=True, check=True)
    print(f"  {shown.stdout.strip()}")

    step("Voxframe and its libraries")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-cache-dir", "--quiet", f"{wheel}[app]"],
        check=True,
    )

    step("FFmpeg")
    ffmpeg = resources / "ffmpeg"
    ffmpeg.mkdir()
    for name, sha256 in FFMPEG_FILES.items():
        with zipfile.ZipFile(fetch(f"{FFMPEG_BASE}/{name}", WORK / name, sha256)) as bundle:
            bundle.extractall(ffmpeg)
        (WORK / name).unlink()
    for binary in ("ffmpeg", "ffprobe"):
        (ffmpeg / binary).chmod(0o755)
    urllib.request.urlretrieve(GPL3_URL, ffmpeg / "LICENSE-FFmpeg.txt")
    fetch(f"{FFMPEG_BASE}/versions.txt", ffmpeg / "versions.txt", FFMPEG_VERSIONS_SHA256)
    (ffmpeg / "SOURCE.txt").write_text(
        f"""FFmpeg {FFMPEG_VERSION}, static build for macOS (Apple Silicon)
=============================================================

This folder holds FFmpeg, a separate program that Voxframe runs to make video.
It is licensed under the GNU General Public License, version 3
(LICENSE-FFmpeg.txt). Voxframe itself is licensed under the Apache License 2.0
and carries no GPL obligation: it runs FFmpeg as a separate process and does not
link to it.

Where this build came from: {FFMPEG_BASE}/
Its SHA-256 values, checked when this app was built: {FFMPEG_FILES}

Source code
-----------
The complete source of FFmpeg {FFMPEG_VERSION}, the version in this folder, is
attached to the same GitHub release as this app (ffmpeg-{FFMPEG_VERSION}.tar.xz),
with a list of the libraries compiled into this build and where the build
provider publishes their source (ffmpeg-{FFMPEG_VERSION}-libraries.txt):
{RELEASES}/tag/v{version()}

The build provider's own source links:
- FFmpeg {FFMPEG_VERSION}: https://ffmpeg.org/releases/ffmpeg-{FFMPEG_VERSION}.tar.xz
- The build scripts, which fetch each library's source from its project:
  {FFMPEG_BUILD_SCRIPT}
- The libraries compiled in, each linked to its project: https://ffmpeg.martin-riedl.de/
- versions.txt in this folder: the build's configuration and every library's version
""",
        encoding="utf-8",
    )

    step("app bundle")
    shutil.copy(REPO_ROOT / "src" / "voxframe" / "assets" / "icon" / "voxframe.icns", resources)
    shutil.copy(REPO_ROOT / "LICENSE", resources / "LICENSE")
    shutil.copy(REPO_ROOT / "THIRD_PARTY_LICENSES", resources / "THIRD_PARTY_LICENSES")
    launcher = contents / "MacOS" / "Voxframe"
    launcher.write_text(
        '#!/bin/bash\n'
        '# Start Voxframe with the Python inside this app (D-161).\n'
        'HERE="$(cd "$(dirname "$0")/.." && pwd)"\n'
        'exec "$HERE/Resources/python/bin/python3" -m voxframe.launcher "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    with (contents / "Info.plist").open("wb") as plist:
        plistlib.dump(
            {
                "CFBundleName": "Voxframe",
                "CFBundleDisplayName": "Voxframe",
                "CFBundleIdentifier": "io.github.ngum12.voxframe",
                "CFBundleExecutable": "Voxframe",
                "CFBundleIconFile": "voxframe.icns",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": version(),
                "CFBundleVersion": version(),
                "LSMinimumSystemVersion": "12.0",
                "LSApplicationCategoryType": "public.app-category.video",
                "NSHighResolutionCapable": True,
            },
            plist,
        )
    return app


def test(app: Path) -> None:
    """Run the bundle's own Python against the bundle's own FFmpeg."""
    step("test the app")
    python = app / "Contents" / "Resources" / "python" / "bin" / "python3"
    work = WORK / "test"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    env = {
        "HOME": str(work),
        "PATH": "/usr/bin:/bin",
        "VOXFRAME_CONFIG_DIR": str(work / "config"),
        "LANG": "en_US.UTF-8",
    }
    check = f"""
import sys, time, threading, urllib.request
from pathlib import Path
import voxframe
from voxframe.config import paths
from voxframe.launcher import _use_bundled_ffmpeg, bundled_ffmpeg
assert "Voxframe.app" in voxframe.__file__, voxframe.__file__
assert not paths.is_source_checkout()
print("imported from", Path(voxframe.__file__).parent)
print("bundled FFmpeg", bundled_ffmpeg())
_use_bundled_ffmpeg()
from voxframe.render.encode.probe import probe_capabilities
caps = probe_capabilities()
assert "Voxframe.app" in caps.ffmpeg_path and "ass" in caps.filters and "libx264" in caps.encoders
print("FFmpeg", caps.version, "with libass and x264")
import torch, faster_whisper, open_clip
print("torch", torch.__version__, "faster-whisper", faster_whisper.__version__)
from voxframe.config.settings import QualityPreset, get_settings
from voxframe.config.style import get_template
from voxframe.jobs.pipeline import JobOptions, run_pipeline
options = JobOptions(
    audio=Path({str(SONNET)!r}), output=Path({str(work / 'sonnet.mp4')!r}),
    quality=QualityPreset.DRAFT, height=480, title="A Calendar of Sonnets", language="en")
settings = get_settings().model_copy(update={{"transcribe_model": "base"}})
out = run_pipeline(options, settings, get_template("documentary"), caps)
print("rendered", out.result.frame_count, "frames,", out.word_count, "words")
import uvicorn
from voxframe.api.serve import build_server
h = build_server()
s = uvicorn.Server(uvicorn.Config(h.app, host=h.host, port=h.port, log_level="warning"))
t = threading.Thread(target=s.run, daemon=True); t.start()
for _ in range(100):
    try:
        health = f"http://{{h.host}}:{{h.port}}/api/health"
        print("server", urllib.request.urlopen(health).read().decode()); break
    except OSError:
        time.sleep(0.1)
s.should_exit = True; t.join(timeout=15)
print("APP OK")
"""
    started = time.monotonic()
    result = subprocess.run([str(python), "-c", check], env=env, cwd=work,
                            capture_output=True, text=True, check=False)
    print("\n".join(line for line in result.stdout.splitlines()))
    if result.returncode != 0 or "APP OK" not in result.stdout:
        print(result.stderr[-4000:])
        raise SystemExit("The app did not pass its test.")
    print(f"  passed in {time.monotonic() - started:.0f}s")
    shutil.rmtree(work, ignore_errors=True)


def package(app: Path) -> Path:
    step("sign (ad hoc) and pack")
    for leftover in app.rglob("__pycache__"):
        shutil.rmtree(leftover, ignore_errors=True)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", str(app)], check=True)
    staging = WORK / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    shutil.copytree(app, staging / "Voxframe.app", symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    dmg = WORK / f"Voxframe-{version()}-macOS-arm64.dmg"
    dmg.unlink(missing_ok=True)
    subprocess.run(["hdiutil", "create", "-volname", "Voxframe", "-srcfolder", str(staging),
                    "-ov", "-format", "UDZO", str(dmg)], check=True, capture_output=True)
    print(f"  {dmg.name}  {dmg.stat().st_size / 1_000_000:.0f} MB")
    print(f"  SHA-256 {hashlib.sha256(dmg.read_bytes()).hexdigest()}")
    return dmg


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-test", action="store_true", help="skip testing the bundle")
    arguments = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("The macOS app is built on an Apple Silicon Mac (a macos-14 runner).")
    WORK.mkdir(parents=True, exist_ok=True)
    app = assemble(build_wheel())
    if not arguments.no_test:
        test(app)
    package(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
