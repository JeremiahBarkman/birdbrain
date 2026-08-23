#!/usr/bin/env bash
# Sets up the Python virtual environment for the Backyard Bird
# Discovery System, installs the project in editable mode, and
# verifies BirdNET actually works before declaring success.
#
# Usage: ./scripts/install.sh
#
# All paths here are relative to the repo (resolved via
# "$(dirname "$0")/.."), so this works regardless of where the repo
# is cloned to.
set -uo pipefail  # not -e: several steps below need to handle their own failures and give guidance

cd "$(dirname "$0")/.."

VENV_DIR=".venv"
MIN_MINOR=9
MAX_MINOR_EXCLUSIVE=12  # tensorflow's supported range (see pyproject.toml requires-python)
MIN_FREE_GB_FOR_INSTALL=3

# ---------------------------------------------------------------------------
# Step 0: fail fast on things no amount of retrying will fix.
#
# Checked here (before spending any time) rather than only via
# `bird-display doctor` at the end, so a doomed install doesn't first
# cost several minutes and a large download.
# ---------------------------------------------------------------------------

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This project targets macOS — audio capture, the installer, and the desktop"
    echo "launcher all assume it. Detected: $(uname -s)."
    exit 1
fi

avail_kb="$(df -Pk . | tail -1 | awk '{print $4}')"
avail_gb=$((avail_kb / 1024 / 1024))
if [ "$avail_gb" -lt "$MIN_FREE_GB_FOR_INSTALL" ]; then
    echo "Only ${avail_gb}GB free on this volume."
    echo "The dependency install (tensorflow, librosa, and friends) needs roughly"
    echo "${MIN_FREE_GB_FOR_INSTALL}GB+ free to avoid failing partway through with a"
    echo "confusing 'no space left on device' error. Free up space and re-run."
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 1: find (or help install) a compatible Python.
#
# BirdNET's dependency stack (tensorflow in particular) only ships
# wheels for a specific Python range, so this has to be checked before
# creating the venv — a venv built on the system's default python3
# (often newer than tensorflow supports) would fail obscurely later at
# `pip install`.
# ---------------------------------------------------------------------------

python_is_compatible() {
    "$1" -c "
import sys
minor = sys.version_info.minor
major = sys.version_info.major
sys.exit(0 if (major == 3 and $MIN_MINOR <= minor < $MAX_MINOR_EXCLUSIVE) else 1)
" 2>/dev/null
}

find_compatible_python() {
    local candidate
    for candidate in python3.11 python3.10 python3.9 "${PYTHON_BIN:-}" python3; do
        [ -z "$candidate" ] && continue
        if command -v "$candidate" >/dev/null 2>&1 && python_is_compatible "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(find_compatible_python || true)"

if [ -z "$PYTHON_BIN" ]; then
    echo "No compatible Python found (need 3.${MIN_MINOR}-3.$((MAX_MINOR_EXCLUSIVE - 1)); tensorflow doesn't yet support newer)."
    echo

    if command -v brew >/dev/null 2>&1 && [ -t 0 ]; then
        echo "Homebrew is available. Install Python 3.11 now?"
        read -r -p "  brew install python@3.11 [y/N] " reply
        if [[ "$reply" =~ ^[Yy]$ ]]; then
            brew install python@3.11
            PYTHON_BIN="$(brew --prefix python@3.11)/bin/python3.11"
            if ! python_is_compatible "$PYTHON_BIN"; then
                echo "python@3.11 still isn't usable after install — please investigate your Homebrew Python setup."
                exit 1
            fi
        else
            echo "Skipped. Install a compatible Python yourself, then re-run this script."
            exit 1
        fi
    elif command -v brew >/dev/null 2>&1; then
        echo "Homebrew is available but this shell isn't interactive — run interactively to be"
        echo "offered 'brew install python@3.11', or install it yourself and re-run:"
        echo "  brew install python@3.11"
        exit 1
    else
        cat <<EOF
Homebrew isn't installed, so this script can't install Python for you.

Options:
  - Install Homebrew (https://brew.sh), then re-run this script.
  - Install Python 3.11 yourself (e.g. via python.org or pyenv) and
    either put it on PATH as one of python3.11/python3.10/python3.9,
    or re-run as: PYTHON_BIN=/path/to/python3.11 ./scripts/install.sh
EOF
        exit 1
    fi
fi

echo "Using $("$PYTHON_BIN" --version) at $PYTHON_BIN"

# ---------------------------------------------------------------------------
# Step 2: create the venv and install the project.
# ---------------------------------------------------------------------------

if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

pip install --upgrade pip
if ! pip install -e ".[dev]"; then
    echo
    echo "Dependency install failed — see the pip error above."
    echo "This is most often tensorflow failing to find a wheel for this machine's"
    echo "architecture/OS version. Check https://pypi.org/project/tensorflow/ for"
    echo "supported platforms, or open an issue with the error output."
    exit 1
fi

if [ ! -f config/config.yaml ]; then
    cp config/config.example.yaml config/config.yaml
    echo "Created config/config.yaml from the example."
    echo "Edit location.latitude/longitude and audio.device_name before running anything."
fi

# ---------------------------------------------------------------------------
# Step 3: verify BirdNET actually works — not just "pip said success".
#
# BirdNET-Analyzer's model ships inside the birdnetlib wheel itself,
# so there's no separate "install BirdNET" step beyond the pip install
# above. What's worth verifying is that the model actually loads and
# can analyze audio (the Phase 1 exit condition in the requirements
# doc) — that's what `bird-display doctor` checks.
# ---------------------------------------------------------------------------

SAMPLE_WAV_DIR="tests/sample_audio"
if [ -z "$(find "$SAMPLE_WAV_DIR" -maxdepth 1 -iname '*.wav' 2>/dev/null)" ]; then
    echo
    echo "No sample WAV found for the BirdNET self-test."
    download_sample=false
    if [ -t 0 ]; then
        echo "Fetch BirdNET-Analyzer's own example clip (kahst/BirdNET-Analyzer, Apache-2.0)?"
        read -r -p "  Download ~1MB from github.com [Y/n] " reply
        [[ ! "$reply" =~ ^[Nn]$ ]] && download_sample=true
    else
        echo "Non-interactive shell — skipping the download. 'bird-display doctor' will warn"
        echo "instead of fully verifying analysis; see tests/sample_audio/README.md."
    fi

    if [ "$download_sample" = true ]; then
        if curl -fSL --retry 3 -o "$SAMPLE_WAV_DIR/soundscape.wav" \
            "https://raw.githubusercontent.com/kahst/BirdNET-Analyzer/main/birdnet_analyzer/example/soundscape.wav"; then
            echo "Saved $SAMPLE_WAV_DIR/soundscape.wav"
        else
            echo "Download failed (offline?) — continuing without a full BirdNET self-test."
            rm -f "$SAMPLE_WAV_DIR/soundscape.wav"
        fi
    fi
fi

echo
echo "Running bird-display doctor..."
if ! bird-display doctor; then
    echo
    echo "BirdNET (or another prerequisite) isn't working yet — see the FAIL line(s) above."
    echo "Fix the issue and re-run: $VENV_DIR/bin/bird-display doctor"
    exit 1
fi

echo
echo "Done. Activate with: source $VENV_DIR/bin/activate"
echo "Then try:"
echo "  bird-display audio list-devices"
echo "  bird-display config validate"
