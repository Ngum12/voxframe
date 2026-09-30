"""Install the Windows installer, test what it installed, and uninstall it (D-158).

The check first done by hand, made repeatable for the release workflow:

1. Install silently into a temporary folder with ``/S /INSTDIR=<folder>``,
   the folder option pynsist's installer supports. (``/D=`` is patched to work
   too, but is not yet verified, so the release does not depend on it.)
2. With the INSTALLED Python only, and every personal folder redirected to a
   scratch folder: import Voxframe from the install, find the bundled FFmpeg
   with libass and x264, load PyTorch, Whisper and CLIP, render the 45-second
   public-domain sonnet with a title card, and serve the app.
3. With the installed app running, the installer and the uninstaller must both
   refuse and change nothing (D-168).
4. Uninstall with the installer's own uninstaller, and confirm the program
   folder, the Start-menu shortcut and the uninstall entry are gone.

It installs software, so it is for a CI runner or a throwaway machine -- on a
person's own computer, only with their agreement.

    python scripts/test_windows_installer.py [installer.exe]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SONNET = REPO_ROOT / "samples" / "public" / "en_sonnet_january_45s.wav"

CHECK = r"""
import sys, time, threading, urllib.request
from pathlib import Path
install = Path(sys.argv[1]); sonnet = Path(sys.argv[2]); work = Path(sys.argv[3])
import voxframe
from voxframe.config import paths
assert Path(voxframe.__file__).resolve().is_relative_to(install.resolve()), voxframe.__file__
assert not paths.is_source_checkout()
print("imported from", Path(voxframe.__file__).parent)
from voxframe.launcher import _use_bundled_ffmpeg, bundled_ffmpeg
print("bundled FFmpeg", bundled_ffmpeg())
_use_bundled_ffmpeg()
from voxframe.render.encode.probe import probe_capabilities
caps = probe_capabilities()
assert Path(caps.ffmpeg_path).resolve().is_relative_to(install.resolve()), caps.ffmpeg_path
assert "ass" in caps.filters and "libx264" in caps.encoders
print("FFmpeg", caps.version, "with libass and x264")
import torch, faster_whisper, open_clip
print("torch", torch.__version__, "faster-whisper", faster_whisper.__version__)
# The window and the picture model, used for real: v0.1.0 imported fine and
# could do neither (D-163, D-164).
from voxframe.selfcheck import picture_model, window
print(window())
print(picture_model())
from voxframe.config.settings import QualityPreset, get_settings
from voxframe.config.style import get_template
from voxframe.jobs.pipeline import JobOptions, run_pipeline
options = JobOptions(audio=sonnet, output=work / "sonnet.mp4", quality=QualityPreset.DRAFT,
                     height=480, title="A Calendar of Sonnets", language="en")
settings = get_settings().model_copy(update={"transcribe_model": "base"})
out = run_pipeline(options, settings, get_template("documentary"), caps)
assert out.result.frame_count > 0
print("rendered", out.result.frame_count, "frames,", out.word_count, "words")
import uvicorn
from voxframe.api.serve import build_server
h = build_server()
s = uvicorn.Server(uvicorn.Config(h.app, host=h.host, port=h.port, log_level="warning"))
t = threading.Thread(target=s.run, daemon=True); t.start()
for _ in range(100):
    try:
        health = f"http://{h.host}:{h.port}/api/health"
        print("server", urllib.request.urlopen(health).read().decode()); break
    except OSError:
        time.sleep(0.1)
s.should_exit = True; t.join(timeout=15)
print("INSTALLED APP OK")
"""


def uninstall_entry() -> bool:
    """Whether Voxframe is listed in the user's installed apps."""
    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as uninstall:
        for index in range(winreg.QueryInfoKey(uninstall)[0]):
            with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, index)) as entry:
                try:
                    if "Voxframe" in winreg.QueryValueEx(entry, "DisplayName")[0]:
                        return True
                except OSError:
                    continue
    return False


def refuse_while_running(installer: Path, target: Path) -> None:
    """Installing over, or uninstalling, a running Voxframe must stop (D-168).

    The installed ``pythonw.exe`` is started, as the app runs; then both the
    installer and the uninstaller must refuse, leaving every file in place.
    ``_?=`` runs the uninstaller in place, so its exit code is its own rather
    than that of the copy it normally launches.
    """
    marker = target / "pkgs" / "voxframe" / "__init__.py"
    before = marker.read_bytes()
    running = subprocess.Popen(
        [str(target / "Python" / "pythonw.exe"), "-c", "import time; time.sleep(600)"]
    )
    try:
        time.sleep(3)
        again = subprocess.run(
            [str(installer), "/S", "/CurrentUser", f"/INSTDIR={target}"], check=False
        )
        away = subprocess.run(
            [str(target / "uninstall.exe"), "/S", f"_?={target}"], check=False
        )
    finally:
        running.kill()
        running.wait()
    print(f"   installer exit {again.returncode}, uninstaller exit {away.returncode}")
    if again.returncode == 0 or away.returncode == 0:
        raise SystemExit("The installer or uninstaller went ahead while Voxframe was running.")
    if not marker.is_file() or marker.read_bytes() != before:
        raise SystemExit("Files changed although the installer refused.")
    print("   both refused, files untouched")


def main() -> int:
    installer = (
        Path(sys.argv[1]) if len(sys.argv) > 1
        else next((REPO_ROOT / "build" / "windows" / "nsis").glob("*.exe"))
    )
    scratch = Path(tempfile.mkdtemp(prefix="vf-installer-test-"))
    target = scratch / "Voxframe"
    shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Voxframe.lnk"

    print(f"== install {installer.name} into {target} ==", flush=True)
    started = time.monotonic()
    subprocess.run([str(installer), "/S", "/CurrentUser", f"/INSTDIR={target}"], check=True)
    python = target / "Python" / "python.exe"
    if not python.is_file():
        raise SystemExit(f"/INSTDIR= was not honoured: nothing installed at {target}")
    print(f"   installed in {time.monotonic() - started:.0f}s")

    print("== test the installed app ==", flush=True)
    home = scratch / "home"
    for folder in ("Local", "Roaming", "config", "work"):
        (home / folder).mkdir(parents=True)
    env = {
        "SystemRoot": os.environ["SystemRoot"],
        "PATH": rf"{os.environ['SystemRoot']}\System32;{os.environ['SystemRoot']}",
        "LOCALAPPDATA": str(home / "Local"),
        "APPDATA": str(home / "Roaming"),
        "USERPROFILE": str(home),
        "TEMP": str(scratch), "TMP": str(scratch),
        "VOXFRAME_CONFIG_DIR": str(home / "config"),
    }
    if "HF_HUB_CACHE" in os.environ:
        env["HF_HUB_CACHE"] = os.environ["HF_HUB_CACHE"]
    result = subprocess.run(
        [str(python), "-c", CHECK, str(target), str(SONNET), str(home / "work")],
        env=env, cwd=home / "work", capture_output=True, text=True, check=False,
    )
    print(result.stdout)
    if result.returncode != 0 or "INSTALLED APP OK" not in result.stdout:
        print(result.stderr[-4000:])
        raise SystemExit("The installed app did not pass its test.")

    print("== while Voxframe is running ==", flush=True)
    refuse_while_running(installer, target)

    print("== uninstall ==", flush=True)
    subprocess.run([str(target / "uninstall.exe"), "/S"], check=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline and (target.exists() or uninstall_entry()):
        time.sleep(2)
    left = {
        "program folder": (target / "Python").exists(),
        "Start-menu shortcut": shortcut.exists(),
        "uninstall entry": uninstall_entry(),
    }
    states = (f"{name}: {'LEFT' if there else 'gone'}" for name, there in left.items())
    print("   " + ", ".join(states))
    shutil.rmtree(scratch, ignore_errors=True)
    if any(left.values()):
        raise SystemExit("The uninstaller left something behind.")
    print("\ninstaller verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
