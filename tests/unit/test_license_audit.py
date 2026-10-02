"""Verify dependency licenses stay compatible with the project's promise.

Voxframe guarantees that users may redistribute and use it commercially. That
holds only while *every* dependency permits it, so this is enforced rather than
documented and hoped for.

Transitive coverage
-------------------
These tests walk the full transitive closure of Voxframe's runtime
requirements, not only the packages named in ``pyproject.toml``. The Phase 1
audit checked 7 top-level packages; the actual closure is 19, so 12 were
unexamined. A permissively licensed package can pull in a copyleft one, and
that dependency ships to users just the same.

Scope note: the closure is computed from package metadata, not from ``pip
list``. Scanning the environment would report every unrelated package the
developer happens to have installed — during Phase 1 that produced six GPL
false positives from tools with no connection to Voxframe.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, distribution, metadata
from pathlib import Path

import pytest

#: License identifiers permitting free commercial use and redistribution.
ACCEPTABLE = (
    "mit",
    "bsd",
    "apache",
    "isc",
    "psf",
    "python software foundation",
    "mozilla public license 2.0",
    "mpl-2.0",
    "unlicense",
    "cc0",
    "zlib",
    "lgpl",  # weak copyleft; acceptable when dynamically linked (see note 1)
)

#: Strong copyleft or non-commercial markers that would break the promise.
#: "lgpl" is deliberately absent; it is acceptable and checked separately.
FORBIDDEN = ("gpl-3", "gplv3", "agpl", "gpl-2", "gplv2", "non-commercial", "cc-by-nc")

#: Packages whose license metadata is absent or unparseable upstream, with the
#: license verified by hand. Each entry needs a reason; this is not a silencer.
VERIFIED_BY_HAND: dict[str, str] = {}

REPO_ROOT = Path(__file__).resolve().parents[2]
LICENSE_FILE = REPO_ROOT / "THIRD_PARTY_LICENSES"

#: Requirement specifiers look like 'rich (>=13.7); extra == "dev"'.
#: Split on the first character that cannot appear in a package name.
_NAME_BOUNDARY = re.compile(r"[\s;\[<>=!~(]")


def _requirement_name(spec: str) -> str:
    """Extract the bare package name from a requirement specifier."""
    return _NAME_BOUNDARY.split(spec.strip(), 1)[0].strip().lower().replace("_", "-")


def _runtime_closure(root: str = "voxframe") -> set[str]:
    """Every package reachable from the root's runtime requirements.

    Optional extras are excluded: they are not installed by default and are
    audited when their phase lands.
    """
    try:
        distribution(root)
    except PackageNotFoundError:
        pytest.skip(f"{root} not installed")

    seen: set[str] = set()
    stack = [root]

    while stack:
        name = stack.pop()
        key = _requirement_name(name)
        if key in seen:
            continue
        seen.add(key)

        try:
            requires = distribution(key).requires or []
        except PackageNotFoundError:
            # Declared but not installed: a requirement for another system,
            # such as colorama, which click needs only on Windows. Its
            # licence is audited where it is installed (the Windows checks,
            # D-195); here there is nothing to read.
            seen.discard(key)
            continue

        for spec in requires:
            if "extra ==" in spec:
                continue
            dep = _requirement_name(spec)
            if dep:
                stack.append(dep)

    seen.discard(_requirement_name(root))
    return seen


def _license_of(package: str) -> str:
    """Best-effort license identifier from package metadata.

    Prefers ``License ::`` trove classifiers and ``License-Expression`` over
    the free-text ``License`` field, which often holds the *full text* of every
    bundled license. numpy, for example, embeds a passage discussing the GNU
    GPL, so a substring search over that field reports numpy — a BSD package —
    as GPL.
    """
    try:
        meta = metadata(package)
    except PackageNotFoundError:
        return ""

    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return " ".join(classifiers).lower()

    expression = meta.get("License-Expression")
    if expression:
        return expression.lower()

    free_text = meta.get("License") or ""
    return free_text.lower() if len(free_text) < 100 else ""


class TestTransitiveClosure:
    def test_closure_is_larger_than_declared(self) -> None:
        """Guards the reason these tests walk the graph at all.

        If this ever fails, Voxframe has no transitive dependencies and the
        distinction stops mattering — but until then, auditing only top-level
        packages leaves most of the shipped code unchecked.
        """
        declared = {
            _requirement_name(s)
            for s in distribution("voxframe").requires or []
            if "extra ==" not in s
        }
        closure = _runtime_closure()

        assert len(closure) > len(declared), (
            "Expected transitive dependencies beyond the declared set. "
            f"Declared: {len(declared)}, closure: {len(closure)}"
        )

    def test_no_forbidden_licenses_anywhere_in_closure(self) -> None:
        """The core guarantee, applied to every shipped package."""
        offenders: list[tuple[str, str]] = []

        for package in sorted(_runtime_closure()):
            lic = _license_of(package)
            if lic and any(bad in lic for bad in FORBIDDEN):
                offenders.append((package, lic))

        assert not offenders, (
            "Dependencies whose licenses break the commercial-use promise:\n"
            + "\n".join(f"  {name}: {lic}" for name, lic in offenders)
            + "\nSee THIRD_PARTY_LICENSES and DECISIONS.md."
        )

    def test_every_license_in_closure_is_recognised(self) -> None:
        """An unrecognised license needs a human decision, not a silent pass."""
        unknown: list[tuple[str, str]] = []

        for package in sorted(_runtime_closure()):
            if package in VERIFIED_BY_HAND:
                continue
            lic = _license_of(package)
            if not lic:
                unknown.append((package, "(no license metadata)"))
            elif not any(ok in lic for ok in ACCEPTABLE):
                unknown.append((package, lic))

        assert not unknown, (
            "Unrecognised or missing licenses. Verify each, then add it to\n"
            "ACCEPTABLE (if a new permissive license) or VERIFIED_BY_HAND\n"
            "(if metadata is absent upstream) with a reason:\n"
            + "\n".join(f"  {name}: {lic}" for name, lic in unknown)
        )

    def test_hand_verified_entries_have_reasons(self) -> None:
        """An exemption without a reason is an unaudited package."""
        for package, reason in VERIFIED_BY_HAND.items():
            assert len(reason) > 10, f"{package} needs a substantive reason, got {reason!r}"


class TestAuditFile:
    def test_audit_file_exists(self) -> None:
        assert LICENSE_FILE.exists(), "THIRD_PARTY_LICENSES is missing"

    def test_every_closure_package_is_documented(self) -> None:
        """A new dependency must be recorded, including transitive ones."""
        text = LICENSE_FILE.read_text(encoding="utf-8").lower()

        missing = [
            package
            for package in sorted(_runtime_closure())
            if package not in text and package.replace("-", "_") not in text
        ]

        assert not missing, (
            f"Undocumented dependencies: {missing}\n"
            "Add each to THIRD_PARTY_LICENSES with its license. Transitive\n"
            "dependencies ship to users too, so they belong in the audit."
        )

    def test_exclusions_are_recorded(self) -> None:
        """The non-commercial exclusions are load-bearing; keep them visible."""
        text = LICENSE_FILE.read_text(encoding="utf-8")
        for marker in ("CC-BY-NC", "Depth-Anything-V2-Base", "Remotion"):
            assert marker in text, f"Exclusion '{marker}' missing from audit file"

    def test_ffmpeg_relationship_documented(self) -> None:
        """The GPL boundary is the subtlest licensing point in the project."""
        text = LICENSE_FILE.read_text(encoding="utf-8")
        assert "FFmpeg" in text
        assert "not bundle" in text.lower()
