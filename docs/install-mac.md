# Installing Voxframe on a Mac

The Mac version is an **early build**, for **Apple Silicon** Macs (M1 and later)
running macOS 12 or newer. It is not yet signed by Apple, so macOS asks you to
confirm before it opens the first time. A signed Mac app is planned.

## 1. Download

From the [Releases page](https://github.com/Ngum12/voxframe/releases), download
`Voxframe-<version>-macOS-arm64.dmg`. To check your download, open Terminal in
your Downloads folder and run:

```bash
shasum -a 256 Voxframe-0.1.0-macOS-arm64.dmg
```

The value it prints must match the one in `SHA256SUMS` on the Releases page.

## 2. Install

Open the `.dmg` and drag **Voxframe** into **Applications**.

## 3. Open it the first time

Because the app is not signed by Apple, macOS stops it the first time:

1. Open **Voxframe** from Applications. macOS says it cannot verify the
   developer, or that the app "cannot be opened". Click **Done** (or **OK**).
2. Open **System Settings**, then **Privacy & Security**.
3. Scroll down to the message about Voxframe and click **Open Anyway**.
4. Confirm with your password or Touch ID, then click **Open**.

After that, Voxframe opens normally.

## 4. First start

A small window says *Voxframe is running*, and your web browser opens at
Voxframe. Keep the small window open while you use it; **Quit** closes it.

The first time, Voxframe asks to download its two models — about 3.1 GB, or
750 MB for the Lite version — and shows how far it has got. After that it works
without the internet (unless you turn on online image search).

## Where things are kept

| | folder |
|---|---|
| Your library, cache and models | `~/Library/Application Support/Voxframe` |
| Finished videos | `~/Movies/Voxframe` |
| Settings and API keys | `~/Library/Application Support/voxframe` |

## Uninstall

Drag **Voxframe** from Applications to the Bin. Your library, videos, models and
settings stay in the folders above; delete them too if you want them gone.

## Intel Macs, or installing with pip

The early app is for Apple Silicon only. On an Intel Mac, or if you prefer,
install with Python instead (Python 3.11–3.12 and FFmpeg, for example from
[Homebrew](https://brew.sh): `brew install python@3.12 ffmpeg`):

```bash
python3.12 -m venv ~/voxframe
~/voxframe/bin/pip install "voxframe[app] @ git+https://github.com/Ngum12/voxframe"
~/voxframe/bin/voxframe-app
```
