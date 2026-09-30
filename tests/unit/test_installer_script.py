"""The Windows installer's script patches (D-158, D-168).

The installer itself is built and exercised on GitHub's Windows runner
(``scripts/test_windows_installer.py``), including installing over and
uninstalling a running Voxframe. These pin the patching, which runs on
pynsist's generated script and must land exactly where intended.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import build_windows_installer as build

#: The parts of pynsist 2.8's generated script the patches depend on.
TEMPLATE = """\
Section -SETTINGS
  SetOutPath "$INSTDIR"
SectionEnd

Section "!${PRODUCT_NAME}" sec_app
  SetRegView 64
  SectionIn RO
  File ${PRODUCT_ICON}
SectionEnd

Section "Uninstall"
  SetRegView 64
  RMDir /r "$INSTDIR\\Python"
SectionEnd

Function .onInit
  StrCpy $cmdLineInstallDir $1
FunctionEnd
"""


def _section(script: str, header: str) -> str:
    start = script.index(header)
    return script[start : script.index("SectionEnd", start)]


def test_installing_checks_first() -> None:
    script = build.patch_script(TEMPLATE)

    body = _section(script, 'Section "!${PRODUCT_NAME}" sec_app').splitlines()

    assert body[1].strip() == "Call VoxframeNotRunning", "the check must come before any file"


def test_uninstalling_checks_first() -> None:
    script = build.patch_script(TEMPLATE)

    body = _section(script, 'Section "Uninstall"').splitlines()

    assert body[1].strip() == "Call un.VoxframeNotRunning"
    assert body.index('  RMDir /r "$INSTDIR\\Python"') > 1


def test_the_check_is_defined_for_both() -> None:
    script = build.patch_script(TEMPLATE)

    assert 'FileOpen $0 "$INSTDIR\\Python\\pythonw.exe" a' in script
    assert '!insertmacro VOXFRAME_NOT_RUNNING ""' in script
    assert '!insertmacro VOXFRAME_NOT_RUNNING "un."' in script
    assert "/SD IDCANCEL" in script, "a silent install must stop, not wait for a click"


def test_the_folder_option_is_still_kept() -> None:
    assert "StrCpy $cmdLineInstallDir $INSTDIR" in build.patch_script(TEMPLATE)


def test_a_changed_template_stops_the_build() -> None:
    with pytest.raises(SystemExit, match="changed"):
        build.patch_script(TEMPLATE.replace('Section "Uninstall"', 'Section "Remove"'))
