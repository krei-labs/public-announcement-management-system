#!/bin/bash
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR" || exit 1
if [ ! -d ".venv" ]; then echo "Virtual environment not found. Run: python3 -m venv .venv"; exit 1; fi
source .venv/bin/activate
python3 app.py &
FLASK_PID=$!
for i in $(seq 1 15); do curl -fsS http://localhost:5000 >/dev/null 2>&1 && break; sleep 1; done
DISPLAY=:0 python3 display.py &
DISPLAY_PID=$!
trap 'kill "$FLASK_PID" "$DISPLAY_PID" 2>/dev/null; exit 0' INT TERM
wait "$FLASK_PID"
