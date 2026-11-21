#!/bin/bash
# Starts Akwaaba. One window does everything:
#   - checks your .env
#   - starts the ngrok tunnel (if NGROK_DOMAIN is set and ngrok isn't already running)
#   - keeps your Mac awake while the assistant runs
# Press Ctrl+C to stop everything.
cd "$(dirname "$0")"

# Installed packages live outside Documents so iCloud can't offload them.
AKWAABA_VENV="$HOME/.akwaaba-venv"
if [ ! -f "$AKWAABA_VENV/bin/activate" ]; then
  echo "Packages not installed yet. Run:  bash setup.sh"
  exit 1
fi
source "$AKWAABA_VENV/bin/activate"

# Install any packages added in an update (quick when nothing is missing).
if ! python -c "import segno, khaya, reportlab, PIL, importlib.util as u; assert u.find_spec('python_multipart') or u.find_spec('multipart')" 2>/dev/null; then
  echo "Installing new packages..."
  pip install -q -r requirements.txt
fi

if [ ! -f .env ]; then
  echo "No .env file found. Run:  cp .env.example .env   then:  open -e .env"
  exit 1
fi
for key in ANTHROPIC_API_KEY META_ACCESS_TOKEN META_PHONE_NUMBER_ID META_VERIFY_TOKEN; do
  if ! grep -qE "^$key=.+" .env || grep -qE "^$key=(paste-|sk-ant-\.\.\.)" .env; then
    echo "Your .env is missing $key (or still has the placeholder). Open it with:  open -e .env"
    echo "(Tip: quit TextEdit after saving, so an old copy can't overwrite the file.)"
    exit 1
  fi
done

# Stop an older copy that's still holding the port.
if lsof -ti :8000 >/dev/null 2>&1; then
  echo "Stopping an older copy of the assistant..."
  lsof -ti :8000 | xargs kill 2>/dev/null
  sleep 1
fi

# Start ngrok in the background if a fixed domain is set.
NGROK_DOMAIN=$(grep -E '^NGROK_DOMAIN=' .env | cut -d= -f2- | tr -d '[:space:]')
NGROK_PID=""
if [ -n "$NGROK_DOMAIN" ]; then
  if pgrep -x ngrok >/dev/null; then
    echo "ngrok is already running in another window; using that."
  elif command -v ngrok >/dev/null; then
    ngrok http --url="https://$NGROK_DOMAIN" 8000 --log=stdout > ngrok.log 2>&1 &
    NGROK_PID=$!
    sleep 2
    if kill -0 "$NGROK_PID" 2>/dev/null; then
      echo "ngrok tunnel: https://$NGROK_DOMAIN  (details in ngrok.log)"
    else
      echo "ngrok didn't start. Last lines of ngrok.log:"; tail -5 ngrok.log
      NGROK_PID=""
    fi
  else
    echo "ngrok isn't installed. Install it with:  brew install ngrok"
  fi
else
  echo "Tip: add NGROK_DOMAIN=your-name.ngrok-free.dev to .env and this script will start ngrok for you."
fi

cleanup() { [ -n "$NGROK_PID" ] && kill "$NGROK_PID" 2>/dev/null; }
trap cleanup EXIT

echo "Starting Akwaaba on http://localhost:8000"
[ -n "$NGROK_DOMAIN" ] && echo "Dashboard: https://$NGROK_DOMAIN/dashboard" && echo "App:       https://$NGROK_DOMAIN/app/"
# caffeinate keeps the Mac from sleeping while the assistant runs (macOS only).
if command -v caffeinate >/dev/null; then
  caffeinate -i uvicorn app:app --port 8000
else
  uvicorn app:app --port 8000
fi
