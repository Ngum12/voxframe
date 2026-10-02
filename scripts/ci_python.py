"""Put the Mac app's own Python on the PATH of a macOS CI runner (D-195).

The Mac app carries python-build-standalone's CPython (build_macos_app.py),
whose sqlite3 loads extensions, as the image library's similarity search
needs (sqlite-vec). The Python GitHub's macOS runners provide cannot, so the
library's tests failed there although the app works. The checks therefore
run on the Python the app ships: the same file, checked against the same
SHA-256.

    python scripts/ci_python.py --github
"""

from __future__ import annotations

import argparse
import os
import sys
import tarfile
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))


def main() -> int:
    import build_macos_app as mac
    from ci_ffmpeg import _download

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, default=Path.home() / "python-ci")
    parser.add_argument("--github", action="store_true", help="put it first on the job's PATH")
    args = parser.parse_args()

    destination = args.dest.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        archive = _download(mac.PYTHON_URL, Path(scratch) / "python.tar.gz", mac.PYTHON_SHA256)
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter="tar")  # holds one folder: python/
    binaries = destination / "python" / "bin"
    # The workflow says "python"; a standalone build may only have python3.
    if not (binaries / "python").exists():
        (binaries / "python").symlink_to("python3")
    print(binaries)
    if args.github:
        with Path(os.environ["GITHUB_PATH"]).open("a", encoding="utf-8") as path_file:
            path_file.write(f"{binaries}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
