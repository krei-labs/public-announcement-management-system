#!/bin/bash
# ============================================================
#  TCC Announcement Display System — Stop Script
# ============================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$PROJECT_DIR/.tcc_pids"

echo "Stopping TCC Announcement System..."

# Kill by saved PIDs first
if [ -f "$PIDFILE" ]; then
    while IFS= read -r pid; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "  Killing PID $pid"
            kill "$pid" 2>/dev/null
        fi
    done < "$PIDFILE"
    rm -f "$PIDFILE"
fi

# Fallback: kill by process name in case PIDs are stale
pkill -f "python3.*app.py"     2>/dev/null
pkill -f "python3.*display.py" 2>/dev/null
pkill -f "chromium.*localhost:5000" 2>/dev/null

sleep 1
echo "TCC system stopped."
