#!/bin/bash
# One-time setup for Akwaaba on macOS. Run from this folder: ./setup.sh
set -e
cd "$(dirname "$0")"
# Installed packages live outside Documents so iCloud can't offload them.
AKWAABA_VENV="$HOME/.akwaaba-venv"

PY=$(command -v python3.12 || command -v python3.11 || command -v python3)
echo "Using $($PY --version) at $PY"
if ! $PY -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
  echo "Python 3.10 or newer is needed. Install it with: brew install python@3.12"
  exit 1
fi

$PY -m venv "$AKWAABA_VENV"
source "$AKWAABA_VENV/bin/activate"
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install openpyxl -q   # for the language check

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Created .env. Open it and paste in your keys:  open -e .env"
fi

if ! command -v ffmpeg >/dev/null; then
  echo
  echo "For voice notes, install ffmpeg with:  brew install ffmpeg"
fi

if ! command -v tesseract >/dev/null; then
  echo
  echo "For register photos (monthly returns), install Tesseract with:  brew install tesseract"
  echo "Without it, register photos stay switched off, because names can't be hidden first."
fi

if ! command -v ngrok >/dev/null; then
  echo
  echo "ngrok is not installed. Install it with:  brew install ngrok"
  echo "Then connect it once:  ngrok config add-authtoken <token from dashboard.ngrok.com>"
fi

echo
echo "Setup done. Start the assistant with:  ./start.sh"
