"""Gather the source of the FFmpeg builds the installers ship (D-162).

The Windows installer and the macOS app each bundle a third-party FFmpeg build,
which is GPL. Every release carries the corresponding source beside them, so
nobody has to ask for it. For every FFmpeg version the two build scripts pin:

1. FFmpeg's own release tarball from ffmpeg.org, checked against a SHA-256
   pinned here, and its signature file, for anyone who wants to check it
   against FFmpeg's release key themselves.
2. ``ffmpeg-<version>-libraries.txt``: for each build, the libraries compiled
   into it with their versions, taken from the build itself, and where its
   provider publishes their source.

    python scripts/ffmpeg_sources.py --out ffmpeg-source/

Run by the release workflow; its files are attached to the draft release.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parent
#: ffmpeg.martin-riedl.de refuses Python's default User-Agent (HTTP 403).
USER_AGENT = "voxframe-release (+https://github.com/Ngum12/voxframe)"

#: FFmpeg's release tarballs, by version. Each was checked against its
#: signature by FFmpeg's release key (FCF986EA15E6E293A5644F10B4322F04D67658D8)
#: before being pinned. A new FFmpeg version in either build script needs its
#: tarball's SHA-256 added here, or the release stops.
FFMPEG_SOURCE_SHA256 = {
    "9.0.2": "8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e",
}


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch(url: str, target: Path, sha256: str | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:
        data: bytes = response.read()
    if sha256 is not None and hashlib.sha256(data).hexdigest() != sha256:
        raise SystemExit(f"{url} does not match its pinned SHA-256")
    target.write_bytes(data)
    return data


def windows_section(windows: ModuleType, work: Path) -> str:
    archive = work / "windows-ffmpeg.zip"
    fetch(windows.FFMPEG_ZIP, archive, windows.FFMPEG_SHA256)
    with zipfile.ZipFile(archive) as bundle:
        readme = next(m for m in bundle.namelist() if Path(m).name == "README.txt")
        notice = bundle.read(readme).decode("utf-8", errors="replace")
    archive.unlink()
    return f"""Windows installer: FFmpeg {windows.FFMPEG_VERSION}, "essentials" build by gyan.dev
{"=" * 72}

The build:  {windows.FFMPEG_ZIP}
SHA-256:    {windows.FFMPEG_SHA256}

FFmpeg's source: ffmpeg-{windows.FFMPEG_VERSION}.tar.xz, attached to this release.
The libraries compiled in, each linked to its project's source, as gyan.dev
publishes them: {windows.FFMPEG_LIBRARIES}

The build's own notice (README.txt from the package above, also installed
beside ffmpeg.exe), with its full configuration and every library's version:

{notice.strip()}
"""


def macos_section(macos: ModuleType, work: Path) -> str:
    versions = work / "macos-versions.txt"
    text = fetch(f"{macos.FFMPEG_BASE}/versions.txt", versions, macos.FFMPEG_VERSIONS_SHA256)
    versions.unlink()
    return f"""macOS app: FFmpeg {macos.FFMPEG_VERSION}, Apple Silicon static build by Martin Riedl
{"=" * 72}

The build:  {macos.FFMPEG_BASE}/
SHA-256:    {", ".join(f"{name} {sha}" for name, sha in macos.FFMPEG_FILES.items())}

FFmpeg's source: ffmpeg-{macos.FFMPEG_VERSION}.tar.xz, attached to this release.
The build scripts, which download each library's source from its project and
build it: {macos.FFMPEG_BUILD_SCRIPT}
The libraries compiled in, each linked to its project: https://ffmpeg.martin-riedl.de/

The build's own list (versions.txt, published with the build and installed in
Voxframe.app/Contents/Resources/ffmpeg), with its full configuration and every
library's version:

{text.decode("utf-8", errors="replace").strip()}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    windows = load("build_windows_installer")
    macos = load("build_macos_app")
    # Each FFmpeg version the installers ship, and the builds that ship it.
    builds: dict[str, list[tuple[ModuleType, Callable[[ModuleType, Path], str]]]] = {}
    builds.setdefault(windows.FFMPEG_VERSION, []).append((windows, windows_section))
    builds.setdefault(macos.FFMPEG_VERSION, []).append((macos, macos_section))

    for version, sections in builds.items():
        if version not in FFMPEG_SOURCE_SHA256:
            raise SystemExit(f"no pinned SHA-256 for FFmpeg {version}'s source; add it here")
        tarball = f"ffmpeg-{version}.tar.xz"
        base = f"https://ffmpeg.org/releases/{tarball}"
        fetch(base, out / tarball, FFMPEG_SOURCE_SHA256[version])
        fetch(f"{base}.asc", out / f"{tarball}.asc")
        print(f"FFmpeg {version} source, SHA-256 verified: {tarball}")

        parts = [section(script, out) for script, section in sections]
        header = f"""Voxframe {windows.version()}: the libraries compiled into its FFmpeg {version}
{"=" * 72}

FFmpeg is licensed under the GPL (version 3 for these builds). The complete
source of FFmpeg {version} is {tarball} in this release; {tarball}.asc is FFmpeg's
signature for it. Below, for each build, is every library compiled into it, as
the build itself reports, and where its provider publishes their source.

"""
        (out / f"ffmpeg-{version}-libraries.txt").write_text(
            header + "\n\n".join(parts), encoding="utf-8"
        )
        print(f"  ffmpeg-{version}-libraries.txt: {len(parts)} build(s)")


if __name__ == "__main__":
    main()
