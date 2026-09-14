#!/usr/bin/env bash
# Starts every long-running Backyard Bird Discovery service — capture,
# analyzer, the image watcher, and the dashboard — for a manual
# "double-click after a cold boot" workflow. Not launchd (that's §29
# Phase 8, unattended start-at-login); this is an explicit, visible
# start you trigger yourself, which is what was actually asked for.
#
# Safe to re-run: each service is skipped if it's already running
# (checked by PID file + confirming that PID is actually a
# bird-display process, not just any process that happens to reuse
# the old PID after a reboot).
#
# images-watch (not images fetch-missing directly) is what's started
# here: it polls every 10 minutes for species that still need a photo
# and only downloads for those — a very different load than the
# rapid-retry pattern that got this project rate-limited by Wikimedia
# during development (see README's Phase 4 notes), and it's what makes
# a newly detected species' photo show up on the dashboard without you
# doing anything (§15.1).
set -uo pipefail  # deliberately not -e: one service failing to start must not stop the others

cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
LOG_DIR="$PROJECT_DIR/data/logs"
PID_DIR="$PROJECT_DIR/data/run"

echo "=========================================="
echo " Backyard Bird Discovery System"
echo "=========================================="
echo

if [ ! -x "$VENV_DIR/bin/bird-display" ]; then
    echo "Virtual environment not found at $VENV_DIR"
    echo "Run ./scripts/install.sh first, then try again."
    read -n 1 -s -r -p "Press any key to close this window..."
    echo
    exit 1
fi

if [ ! -f "$PROJECT_DIR/config/config.yaml" ]; then
    echo "config/config.yaml not found."
    echo "Copy config/config.example.yaml to config/config.yaml and edit it, then try again."
    read -n 1 -s -r -p "Press any key to close this window..."
    echo
    exit 1
fi

mkdir -p "$LOG_DIR" "$PID_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

start_service() {
    local name="$1"
    shift
    local pid_file="$PID_DIR/$name.pid"

    if [ -f "$pid_file" ]; then
        local existing_pid
        existing_pid="$(cat "$pid_file")"
        if kill -0 "$existing_pid" 2>/dev/null && ps -p "$existing_pid" -o command= | grep -q "bird-display"; then
            echo "  $name already running (pid $existing_pid)"
            return
        fi
    fi

    nohup "$@" >> "$LOG_DIR/$name.out.log" 2>&1 &
    echo $! > "$pid_file"
    echo "  $name started (pid $!)"
}

echo "Applying database migrations..."
bird-display database migrate
echo

echo "Starting services..."
start_service capture bird-display capture run
start_service analyzer bird-display analyze run
start_service images_watch bird-display images watch
start_service dashboard bird-display dashboard run

DASHBOARD_URL="$(bird-display dashboard url 2>/dev/null || echo "http://127.0.0.1:8765")"

echo
echo "Dashboard:  $DASHBOARD_URL"
echo "Logs:       $LOG_DIR/"
if [ "$(uname -s)" = "Darwin" ]; then
    echo "To stop:    double-click 'Stop Backyard Birds' on the Desktop"
    echo "            (or run scripts/stop_all.sh)"
else
    echo "To stop:    run scripts/stop_all.sh"
fi
echo
echo "Services keep running after you close this window."
read -n 1 -s -r -p "Press any key to close this window now..."
echo
