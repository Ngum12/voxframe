"""Every source file must actually be committed.

`.gitignore` carried a bare ``models/`` for downloaded ML weights. Git patterns
without a leading slash match at any depth, so it also matched
``src/voxframe/models/`` — and the entire domain model went untracked for three
phases. A fresh clone could not import Voxframe at all (D-078).

The normal suite cannot catch this: it runs against the working tree, where the
files are present. These tests ask git what it would actually give someone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*arguments: str) -> str:
    """Run a git command in the repository, or skip if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        pytest.skip("git not available")
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"git failed: {exc.stderr.strip()}")
    return result.stdout


@pytest.fixture(scope="module")
def tracked_files() -> set[str]:
    """Every path git tracks, as forward-slash relative strings."""
    output = _git("ls-files")
    if not output.strip():
        pytest.skip("not a git repository")
    return {line.strip() for line in output.splitlines() if line.strip()}


def _source_files() -> list[Path]:
    """Every Python file under src/, excluding caches."""
    return [
        path
        for path in (REPO_ROOT / "src").rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def test_source_tree_is_not_empty() -> None:
    """Guard the guard: an empty scan would make everything below vacuous."""
    assert len(_source_files()) > 20


def test_every_source_file_is_tracked(tracked_files: set[str]) -> None:
    """A file present locally but untracked is invisible to a clone."""
    untracked = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in _source_files()
        if path.relative_to(REPO_ROOT).as_posix() not in tracked_files
    ]

    assert not untracked, (
        "these source files are not committed, so a fresh clone would not "
        f"have them: {untracked}"
    )


def test_every_package_has_a_tracked_init(tracked_files: set[str]) -> None:
    """A missing __init__.py breaks the import in a clone, not here."""
    missing: list[str] = []

    for init in (REPO_ROOT / "src").rglob("__init__.py"):
        if "__pycache__" in init.parts:
            continue
        relative = init.relative_to(REPO_ROOT).as_posix()
        if relative not in tracked_files:
            missing.append(relative)

    assert not missing, f"untracked package inits: {missing}"


def test_tests_are_tracked(tracked_files: set[str]) -> None:
    """An untracked test is one nobody else runs."""
    untracked = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "tests").rglob("test_*.py")
        if "__pycache__" not in path.parts
        and path.relative_to(REPO_ROOT).as_posix() not in tracked_files
    ]

    assert not untracked, f"untracked tests: {untracked}"


def test_no_source_directory_is_ignored() -> None:
    """Catch an over-broad pattern before it hides a whole package.

    ``git check-ignore`` answers the question the working tree cannot: would
    this path be excluded from a clone?
    """
    candidates = [
        path
        for path in _source_files()
        if path.name != "__init__.py"
    ][:200]

    if not candidates:
        pytest.skip("no source files to check")

    relative = [str(path.relative_to(REPO_ROOT)) for path in candidates]

    try:
        result = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            cwd=REPO_ROOT,
            input="\n".join(relative),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        pytest.skip("git not available")

    ignored = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert not ignored, f"these source files match a .gitignore pattern: {ignored}"


def test_gitignore_model_pattern_is_anchored() -> None:
    """The specific pattern that caused D-078.

    ``models/`` matches at any depth; ``/models/`` only at the repository root,
    which is where downloaded weights actually live.
    """
    gitignore = REPO_ROOT / ".gitignore"
    if not gitignore.is_file():
        pytest.skip("no .gitignore")

    lines = [
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert "models/" not in lines, (
        "an unanchored 'models/' also matches src/voxframe/models/; "
        "use '/models/' so it applies only at the repository root (D-078)"
    )


class TestSecretsAreNotCommittable:
    """`.env` holds API keys, so git must never be able to ship it.

    Same approach as the models/ guard: ask git what it would actually hand
    someone, rather than trusting that the file looks absent.
    """

    def test_env_is_ignored(self) -> None:
        """`git check-ignore` succeeds only when the path is ignored."""
        try:
            result = subprocess.run(
                ["git", "check-ignore", "-q", ".env"],
                cwd=REPO_ROOT,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError:
            pytest.skip("git not available")

        assert result.returncode == 0, (
            ".env is NOT gitignored. It holds API keys; add it to .gitignore "
            "before writing any secret into it."
        )

    def test_env_is_not_tracked(self, tracked_files: set[str]) -> None:
        """Ignoring a file does nothing if it was committed earlier."""
        leaked = sorted(
            path
            for path in tracked_files
            if path == ".env" or path.startswith(".env.")
            if path != ".env.example"
        )
        assert not leaked, f"secret-bearing files are committed: {leaked}"

    def test_env_example_holds_no_values(self) -> None:
        """The committed template must never carry a real key."""
        example = REPO_ROOT / ".env.example"
        if not example.is_file():
            pytest.skip("no .env.example")

        offenders: list[str] = []
        for line in example.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            name, _, value = stripped.partition("=")
            # A key-shaped value in the template means someone pasted a real
            # one in while testing.
            if len(value.strip()) > 12 and "KEY" in name.upper():
                offenders.append(name.strip())

        assert not offenders, (
            f"these look like real keys in .env.example: {offenders}"
        )

    def test_no_tracked_file_contains_a_key_shaped_string(
        self, tracked_files: set[str]
    ) -> None:
        """Catch a key pasted into source, a test fixture or a doc.

        Scans committed text files for the two shapes the configured providers
        use: a long alphanumeric run, and Pixabay's ``digits-hex`` form.
        """
        import re

        pixabay_shaped = re.compile(r"\b\d{7,10}-[0-9a-f]{24,}\b")
        long_token = re.compile(r"\b[A-Za-z0-9]{45,}\b")
        # SHA-256 hashes are committed deliberately (sample provenance, D-064)
        # and are all-lowercase hex. A real key mixes case or is not hex, so
        # excluding pure hex keeps the scan useful rather than permanently red.
        pure_hex = re.compile(r"^[0-9a-f]+$")
        # npm lockfiles record a Subresource Integrity hash per package:
        # base64, and therefore key-shaped. These are removed from the text
        # before scanning rather than filtered out of the matches, because
        # base64 contains `/` and `+`, which break `` mid-hash and leave
        # fragments that look like fresh tokens. Excising the whole value is
        # exact; matching around it is not. A key pasted anywhere ELSE in the
        # lockfile is still caught (D-118).
        integrity_value = re.compile(r"sha(256|384|512)-[A-Za-z0-9+/=]+")

        text_suffixes = {".py", ".md", ".toml", ".cfg", ".txt", ".json", ".yml", ".yaml"}
        offenders: list[str] = []

        for relative in sorted(tracked_files):
            path = REPO_ROOT / relative
            if path.suffix.lower() not in text_suffixes or not path.is_file():
                continue
            # This file necessarily contains the patterns it searches for.
            if path.name == Path(__file__).name:
                continue

            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            # A file may hold deliberately key-shaped *fake* values to test
            # redaction. Opting out by marker rather than by filename keeps
            # the exemption visible in the file that claims it.
            if "allow-key-shaped-fixtures" in content:
                continue

            scannable = integrity_value.sub("", content)
            suspicious = [
                token
                for token in long_token.findall(scannable)
                if not pure_hex.match(token)
            ]
            if pixabay_shaped.search(scannable) or suspicious:
                offenders.append(relative)

        assert not offenders, (
            f"these committed files contain key-shaped strings: {offenders}"
        )


#: Media that may be tracked. D-019 keeps sample media out of the repository;
#: this one public-domain excerpt is the owner's exception, so the A/V sync
#: test runs on every machine (D-151). Screenshots in docs/ are not media here.
ALLOWED_TRACKED_MEDIA = {"samples/public/en_sonnet_january_45s.wav"}

_MEDIA_SUFFIXES = (
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".mp4", ".mov", ".mkv", ".webm", ".m4v",
)


def test_only_the_one_excerpt_is_tracked_media(tracked_files: set[str]) -> None:
    """No recording, the owner's or anyone's, is committed but the excerpt."""
    media = {name for name in tracked_files if name.lower().endswith(_MEDIA_SUFFIXES)}

    assert media == ALLOWED_TRACKED_MEDIA


def test_the_excerpt_is_the_recorded_one() -> None:
    import hashlib

    data = (REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav").read_bytes()

    assert hashlib.sha256(data).hexdigest() == (
        "cd927d85f09c3bde1422529afd66488302fea67d264a784f5453342e7fad4b2d"
    )
