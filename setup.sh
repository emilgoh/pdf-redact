#!/usr/bin/env bash
#
# Sets up the project's virtual environment and installs dependencies.
#
#   ./setup.sh          -> create .venv and install requirements
#   source ./setup.sh   -> same, and leave the venv activated in your shell
#
set -euo pipefail

# Resolve the directory this script lives in (works whether run or sourced).
if [ -n "${BASH_SOURCE:-}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
fi

VENV_DIR="$SCRIPT_DIR/.venv"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Error: '$PYTHON' not found. Install Python 3 or set PYTHON=<path>." >&2
    return 1 2>/dev/null || exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment in $VENV_DIR ..."
    "$PYTHON" -m venv "$VENV_DIR"
else
    echo "Using existing virtual environment in $VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "Upgrading pip ..."
python -m pip install --quiet --upgrade pip

echo "Installing dependencies from requirements.txt ..."
python -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"

echo
echo "Done. Dependencies installed in $VENV_DIR"

# If the script was executed (not sourced), the activation above is lost when it
# exits, so tell the user how to activate. If it was sourced, the venv stays on.
if (return 0 2>/dev/null); then
    echo "Virtual environment is now active in this shell."
else
    echo "To activate it, run:  source $VENV_DIR/bin/activate"
fi
