#!/usr/bin/env bash
# One-command local setup for Voxframe (Linux / macOS).
set -euo pipefail

echo "Voxframe setup"
echo "=============="

# --- Python version ---
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.11 or newer." >&2
  exit 1
fi

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)')
if [ "$PY_OK" != "1" ]; then
  echo "ERROR: Python 3.11+ required, found $(python3 --version)" >&2
  exit 1
fi
echo "  Python: $(python3 --version)"

# --- FFmpeg ---
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo ""
  echo "ERROR: ffmpeg not found. Voxframe needs it to render video." >&2
  case "$(uname -s)" in
    Darwin) echo "  Install with:  brew install ffmpeg" >&2 ;;
    *)      echo "  Install with:  sudo apt install ffmpeg   # or dnf/pacman" >&2 ;;
  esac
  exit 1
fi
echo "  FFmpeg: $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)"

# --- Virtual environment ---
if [ ! -d .venv ]; then
  echo "  Creating virtual environment..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "  Installing Voxframe..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[dev]"

# --- Config ---
if [ ! -f .env ]; then
  cp .env.example .env
  echo "  Created .env (all settings optional)"
fi

echo ""
voxframe doctor
echo ""
echo "Activate the environment with:  source .venv/bin/activate"
