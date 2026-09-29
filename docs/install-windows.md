# Installing Voxframe on Windows

Voxframe runs on Windows 10 and 11 (64-bit). It needs no other software: the
installer brings its own Python and FFmpeg.

## 1. Download

From the [Releases page](https://github.com/Ngum12/voxframe/releases), download
`Voxframe-<version>-Windows-setup.exe`. To check your download, open PowerShell
in your Downloads folder and run:

```powershell
Get-FileHash .\Voxframe-0.1.0-Windows-setup.exe
```

The value it prints must match the one in `SHA256SUMS` on the Releases page.

## 2. Windows SmartScreen

This first version is **not code-signed**, so when you open the installer
Windows shows a blue box: *"Windows protected your PC"*. That box appears for
any program Windows does not recognise yet; it does not mean Voxframe is
harmful.

1. Click **More info**.
2. Check that the app name is `Voxframe-<version>-Windows-setup.exe`.
3. Click **Run anyway**.

If you would rather not, you can install with Python's `pip` instead, which
involves no installer at all (you need Python 3.11–3.13 and FFmpeg):

```powershell
pip install "voxframe[app] @ git+https://github.com/Ngum12/voxframe"
voxframe-app
```

## 3. Install

Follow the installer. It asks whether to install for you only or for everyone
on the computer, and where. It adds **Voxframe** to the Start menu.

## 4. First start

Open **Voxframe** from the Start menu. A small window says *Voxframe is
running*, and your web browser opens at Voxframe.

- **Closed the browser tab?** Voxframe is still running. Click **Open in
  browser** in the small window, or open Voxframe from the Start menu again:
  it opens the running copy rather than starting a second one.
- **Done?** Click **Quit** in the small window. A video still being made is
  kept, and you can resume it next time.

The first time, Voxframe asks to download its two models — about 3.1 GB, or
750 MB for the Lite version — and shows how far it has got. After that it works
without the internet (unless you turn on online image search).

## Where things are kept

| | folder |
|---|---|
| Your library, cache and models | `%LOCALAPPDATA%\Voxframe` |
| Finished videos | `Videos\Voxframe` |
| Settings and API keys | `%APPDATA%\voxframe` |
| The program | where you installed it |

**Settings → Your folders** in the app shows these, with buttons to open them.

## Uninstall

**Settings → Apps → Installed apps → Voxframe → Uninstall** (or *Add or remove
programs*). Uninstalling removes the program. Your library, videos, models and
settings stay where they are; delete the folders above if you want them gone
too.

## Firewall

Voxframe's server listens only on your own computer (`127.0.0.1`), so Windows
Firewall has nothing to ask about. If a firewall prompt does appear, you can
choose **Cancel**: nothing outside your computer needs to reach Voxframe.
