# Releasing

A release is built by GitHub Actions when the maintainer pushes a version tag.
Nothing is built or published any other way.

## Before tagging (on the maintainer's machine)

1. The full suite passes, browser tests included: `pytest`.
2. The version in `pyproject.toml` and `src/voxframe/__init__.py` is the new one,
   and `.github/release-notes/v<version>.md` describes it.
3. **No key from this machine is in the repository**, raw or base64-encoded
   (D-149, D-153):

   ```bash
   python scripts/scan_history.py --gitleaks path/to/gitleaks --env path/to/.env
   ```

   It scans every commit with gitleaks, searches every version of every file for
   this machine's own keys, and lists any recording, database or credential ever
   committed. It must print NOTHING FOUND. Your keys never go to GitHub, so this
   check can only run here.

## Tagging

For the prepared 2.2.0 release, first merge the version/features pull request,
confirm the checks on `main` pass, and complete the local checks above. Verify
that `pyproject.toml` says `2.2.0` before creating this tag.

```bash
git switch main
git pull --ff-only
git tag v2.2.0
git push origin v2.2.0
```

## What the workflow does (`.github/workflows/release.yml`)

1. **Checks**: the tag matches the version; release notes exist; gitleaks scans
   the whole history.
2. **Windows** (on GitHub's Windows machine): builds the installer
   (`scripts/build_windows_installer.py`), then installs it, tests the installed
   copy -- a real 45-second render with its own Python and FFmpeg -- and
   uninstalls it (`scripts/test_windows_installer.py`).
3. **macOS** (on GitHub's Apple Silicon machine): builds `Voxframe.app`, tests it
   the same way, ad-hoc signs it and packs a DMG (`scripts/build_macos_app.py`).
4. **FFmpeg source** (`scripts/ffmpeg_sources.py`): downloads FFmpeg's source
   for the version each installer bundles, checks it against a pinned SHA-256,
   and lists the libraries compiled into each build and where their source is
   published (D-162). A new FFmpeg version in a build script needs its source's
   SHA-256 pinned there, or the release stops.
5. **Release**: creates a **draft** GitHub release with both installers, the
   FFmpeg source files, a `SHA256SUMS` file and the release notes.

The maintainer reviews the draft, then publishes it. No secrets, certificates or
paid accounts are used.
