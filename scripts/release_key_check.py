"""Fail a release if any key from this machine appears in what would be shipped.

The owner's rule (D-113, decision 3): their personal keys must never be
included in any package, installer, build or fixture, and a release-time check
must fail if any key-like value from ``.env`` appears in built artifacts.

What counts as a key: every value in ``.env`` whose variable name contains KEY,
TOKEN, SECRET or PASSWORD, and every key saved through the app's Settings
screen. Each is searched for as written and base64-encoded, in files, and in
the members of wheels, zips and tarballs.

**Nothing is ever printed but a variable's name and its last four characters.**
A check that leaked the key into a CI log would defeat itself.

    python scripts/release_key_check.py dist/
    python scripts/release_key_check.py dist/ src/voxframe/api/static

Exits 1 if anything is found, 2 if there were no keys to look for (so a CI run
without the secrets does not pass silently -- pass ``--allow-no-keys`` there).
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
import tarfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Variable names that hold secrets.
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)

#: Shorter values are too likely to occur by chance to mean anything.
MIN_SECRET_LENGTH = 8


@dataclass(frozen=True)
class Secret:
    name: str
    value: str

    @property
    def masked(self) -> str:
        return f"{self.name} (…{self.value[-4:]})"

    def forms(self) -> tuple[bytes, ...]:
        """The value as it could appear: raw, and base64-encoded."""
        raw = self.value.encode("utf-8")
        encoded = base64.b64encode(raw).rstrip(b"=")
        return (raw, encoded)


@dataclass(frozen=True)
class Finding:
    where: str
    secret: Secret


def env_secrets(env_file: Path) -> list[Secret]:
    """Key-like values from a ``.env`` file."""
    if not env_file.is_file():
        return []
    found = []
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if _SECRET_NAME.search(name) and len(value) >= MIN_SECRET_LENGTH:
            found.append(Secret(name, value))
    return found


def saved_secrets() -> list[Secret]:
    """Keys saved through the app's Settings screen, if any."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from voxframe.config.userprefs import load_preferences
    except ImportError:
        return []
    return [
        Secret(f"saved {name} key", value)
        for name, value in load_preferences().api_keys.items()
        if len(value) >= MIN_SECRET_LENGTH
    ]


def _contents(path: Path) -> Iterator[tuple[str, bytes]]:
    """Every file's bytes under ``path``, looking inside archives."""
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file():
                yield from _contents(child)
        return

    name = path.name.lower()
    try:
        if name.endswith((".whl", ".zip")):
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    yield f"{path}!{member}", archive.read(member)
            return
        if name.endswith((".tar.gz", ".tgz", ".tar")):
            with tarfile.open(path) as archive:
                for member in archive.getmembers():
                    handle = archive.extractfile(member)
                    if handle is not None:
                        yield f"{path}!{member.name}", handle.read()
            return
    except (zipfile.BadZipFile, tarfile.TarError):
        pass
    yield str(path), path.read_bytes()


def scan(paths: list[Path], secrets: list[Secret]) -> list[Finding]:
    findings = []
    for path in paths:
        for where, data in _contents(path):
            for secret in secrets:
                if any(form in data for form in secret.forms()):
                    findings.append(Finding(where, secret))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="+", help="artifacts or directories")
    parser.add_argument("--env", type=Path, default=REPO_ROOT / ".env")
    parser.add_argument("--no-saved", action="store_true", help="skip keys saved in the app")
    parser.add_argument("--allow-no-keys", action="store_true")
    arguments = parser.parse_args(argv)

    secrets = env_secrets(arguments.env)
    if not arguments.no_saved:
        secrets += saved_secrets()
    if not secrets:
        print("No keys found to look for.")
        return 0 if arguments.allow_no_keys else 2

    missing = [path for path in arguments.paths if not path.exists()]
    if missing:
        print(f"Nothing to check at: {', '.join(map(str, missing))}")
        return 2

    findings = scan(arguments.paths, secrets)
    if findings:
        print("KEYS FOUND in what would be shipped:")
        for finding in findings:
            print(f"  {finding.where}: {finding.secret.masked}")
        return 1
    print(f"Checked for {len(secrets)} key(s): none found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
