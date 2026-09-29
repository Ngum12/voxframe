"""The release-time key check (D-113 decision 3, D-149).

The owner's personal keys must never ship. These plant a made-up key where a
real one could hide -- inside a wheel, base64-encoded -- and check it is found,
that the check never prints the key it found, and that nothing tracked in the
repository holds any key configured on this machine.
"""

from __future__ import annotations

import base64
import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "release_key_check", REPO_ROOT / "scripts" / "release_key_check.py"
)
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
sys.modules["release_key_check"] = check
_spec.loader.exec_module(check)

FAKE = "fake-key-0123456789abcdef"


@pytest.fixture
def env(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        f"VOXFRAME_PEXELS_API_KEY={FAKE}\nVOXFRAME_LOG_LEVEL=info\nSHORT_KEY=abc\n",
        encoding="utf-8",
    )
    return path


def _wheel(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "dist" / "voxframe-0.1-py3-none-any.whl"
    path.parent.mkdir()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("voxframe/api/static/app.js", content)
    return path


def test_only_key_like_values_are_looked_for(env: Path) -> None:
    secrets = check.env_secrets(env)

    assert [secret.name for secret in secrets] == ["VOXFRAME_PEXELS_API_KEY"]


def test_a_key_inside_a_wheel_is_found(env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wheel = _wheel(tmp_path, f"const key = '{FAKE}';".encode())

    status = check.main([str(wheel.parent), "--env", str(env), "--no-saved"])

    output = capsys.readouterr().out
    assert status == 1
    assert "app.js" in output and "VOXFRAME_PEXELS_API_KEY" in output
    assert FAKE not in output, "the check must never print the key it found"


def test_a_base64_encoded_key_is_found(env: Path, tmp_path: Path) -> None:
    encoded = base64.b64encode(FAKE.encode())
    wheel = _wheel(tmp_path, b"data:" + encoded)

    assert check.main([str(wheel), "--env", str(env), "--no-saved"]) == 1


def test_a_clean_artifact_passes(env: Path, tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, b"const nothing = 1;")

    assert check.main([str(wheel), "--env", str(env), "--no-saved"]) == 0


def test_no_keys_to_look_for_is_not_a_pass(tmp_path: Path) -> None:
    """A CI run without the secrets must not pass silently."""
    empty = tmp_path / ".env"
    empty.write_text("", encoding="utf-8")

    assert check.main([str(tmp_path), "--env", str(empty), "--no-saved"]) == 2
    assert check.main([str(tmp_path), "--env", str(empty), "--no-saved", "--allow-no-keys"]) == 0


def test_nothing_tracked_holds_a_key_from_this_machine() -> None:
    """Every committed file -- code, fixtures, docs, the built web bundle."""
    secrets = check.env_secrets(REPO_ROOT / ".env")
    if not secrets:
        pytest.skip("no .env on this machine, so no keys to look for")
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8").split("\0")
    files = [REPO_ROOT / name for name in tracked if name and (REPO_ROOT / name).is_file()]

    findings = check.scan(files, secrets)

    assert not findings, [f"{f.where}: {f.secret.masked}" for f in findings]
