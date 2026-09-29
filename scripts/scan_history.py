"""Scan the entire git history before the repository or a release goes public.

The owner's rule (D-152): before any public release or making the repository
public, scan every commit -- not only the current tree -- for API keys,
key-like strings, personal recordings and anything else that should never be
public, and report findings without printing any secret.

Three checks:

1. **gitleaks** (MIT) over every commit, with secrets redacted. The two
   made-up keys the redaction tests plant are listed in ``.gitleaksignore``.
2. **This machine's own keys**, exactly: every key-like value in ``.env`` and
   every key saved in the app, raw and base64-encoded, in every version of
   every file ever committed and in every commit message. gitleaks has no rules
   for Pexels or Pixabay keys; this does not need any.
3. **Files that should never be public**, by path, in any commit: recordings
   and video (other than the one committed excerpt, D-151), databases,
   ``.env`` files, credentials, private and generated folders, large files.

Nothing is printed but paths, rule names, commit ids, a variable's name and the
last four characters of a key.

    python scripts/scan_history.py --gitleaks path/to/gitleaks

Exits 1 if anything is found.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_key_check import env_secrets, saved_secrets  # noqa: E402

ALLOWED_MEDIA = {"samples/public/en_sonnet_january_45s.wav"}
MEDIA = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus",
         ".mp4", ".mov", ".mkv", ".webm", ".m4v")
LARGE_BYTES = 5_000_000


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", check=True,
    ).stdout


def every_file_version() -> dict[str, tuple[str, int]]:
    """Each distinct blob ever committed: sha -> (path, size)."""
    blobs: dict[str, tuple[str, int]] = {}
    for rev in git("rev-list", "--all").split():
        for line in git("ls-tree", "-r", "-l", rev).splitlines():
            meta, path = line.split("\t", 1)
            _, kind, sha, size = meta.split()
            if kind == "blob":
                blobs[sha] = (path, int(size) if size.isdigit() else 0)
    return blobs


def check_gitleaks(binary: Path) -> list[str]:
    with tempfile.TemporaryDirectory() as work:
        report = Path(work) / "report.json"
        subprocess.run(
            [str(binary), "git", "--redact=100", "--no-banner", "--log-level", "error",
             "--report-format", "json", "--report-path", str(report), str(REPO_ROOT)],
            check=False,
        )
        findings = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else []
    return [
        f"gitleaks {item['RuleID']}: {item['File']}:{item['StartLine']} in {item['Commit'][:7]}"
        for item in findings
    ]


def check_own_keys(
    blobs: dict[str, tuple[str, int]], env_file: Path
) -> tuple[int, list[str]]:
    secrets = env_secrets(env_file) + saved_secrets()
    found = []
    for sha, (path, _) in blobs.items():
        data = subprocess.run(
            ["git", "cat-file", "-p", sha], cwd=REPO_ROOT, capture_output=True, check=True
        ).stdout
        found += [
            f"key {secret.masked} in {path}"
            for secret in secrets
            if any(form in data for form in secret.forms())
        ]
    messages = subprocess.run(
        ["git", "log", "--all", "--format=%B"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout
    found += [
        f"key {secret.masked} in a commit message"
        for secret in secrets
        if any(form in messages for form in secret.forms())
    ]
    return len(secrets), found


def check_paths(blobs: dict[str, tuple[str, int]]) -> list[str]:
    found = []
    for path, size in sorted(set(blobs.values())):
        low = path.lower()
        name = low.rsplit("/", 1)[-1]
        if low.endswith(MEDIA) and path not in ALLOWED_MEDIA:
            found.append(f"recording or video: {path}")
        elif low.endswith((".db", ".sqlite", ".sqlite3")):
            found.append(f"database: {path}")
        elif name.startswith(".env") and name != ".env.example":
            found.append(f"environment file: {path}")
        elif low.endswith((".pem", ".key", ".p12", ".pfx")) or name in {"id_rsa", "config.json"}:
            found.append(f"credential or config: {path}")
        elif low.startswith(("samples/private/", "demo_output/", "library/")) and name not in {
            "readme.md", ".gitignore"
        }:
            found.append(f"private or generated: {path}")
        elif size > LARGE_BYTES:
            found.append(f"large file ({size / 1e6:.1f} MB): {path}")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gitleaks", type=Path, default=None)
    parser.add_argument(
        "--env", type=Path, default=REPO_ROOT / ".env",
        help="the .env holding the keys to look for; a clean copy of the repository "
        "has none of its own, and must never be given one",
    )
    arguments = parser.parse_args()

    binary = arguments.gitleaks or (Path(found) if (found := shutil.which("gitleaks")) else None)
    if binary is None or not binary.exists():
        print("gitleaks not found: install it (MIT) or pass --gitleaks.")
        return 2

    blobs = every_file_version()
    commits = len(git("rev-list", "--all").split())
    print(f"{commits} commits, {len(blobs)} distinct file versions")

    leaks = check_gitleaks(binary)
    print(f"\ngitleaks: {len(leaks)} finding(s)", *leaks, sep="\n  ")
    count, own = check_own_keys(blobs, arguments.env)
    if count == 0:
        print("\nno keys to look for: pass --env")
        return 2
    print(f"\nthis machine's {count} key(s): {len(own)} finding(s)", *own, sep="\n  ")
    paths = check_paths(blobs)
    print(f"\nfiles that should not be public: {len(paths)} finding(s)", *paths, sep="\n  ")

    total = len(leaks) + len(own) + len(paths)
    print(f"\n{'NOTHING FOUND' if total == 0 else f'{total} FINDING(S)'}")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
