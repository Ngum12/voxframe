# One-command local setup for Voxframe (Windows).
#
#   powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = 'Stop'

Write-Host 'Voxframe setup'
Write-Host '=============='

# --- Python version ---
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error 'python not found. Install Python 3.11 or newer from python.org.'
    exit 1
}

$versionOk = & python -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)'
if ($versionOk -ne '1') {
    $found = & python --version
    Write-Error "Python 3.11+ required, found $found"
    exit 1
}
Write-Host "  Python: $(& python --version)"

# --- FFmpeg ---
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    Write-Host ''
    Write-Error @'
ffmpeg not found. Voxframe needs it to render video.

  Install with:  winget install Gyan.FFmpeg
            or:  choco install ffmpeg-full

Then open a new terminal and run this script again.
'@
    exit 1
}
$ffmpegVersion = (& ffmpeg -version 2>$null | Select-Object -First 1) -split ' ' | Select-Object -Index 2
Write-Host "  FFmpeg: $ffmpegVersion"

# --- Virtual environment ---
if (-not (Test-Path .venv)) {
    Write-Host '  Creating virtual environment...'
    & python -m venv .venv
}
& .\.venv\Scripts\Activate.ps1

Write-Host '  Installing Voxframe...'
& python -m pip install --quiet --upgrade pip
& python -m pip install --quiet -e ".[dev]"

# --- Config ---
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host '  Created .env (all settings optional)'
}

Write-Host ''
& voxframe doctor
Write-Host ''
Write-Host 'Activate the environment with:  .\.venv\Scripts\Activate.ps1'
