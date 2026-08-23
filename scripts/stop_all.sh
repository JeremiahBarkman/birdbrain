#!/usr/bin/env bash
# Stops every service started by start_all.sh. Sends SIGTERM, which
# capture, analyze, and images_watch all handle for a clean shutdown
# (see their signal handlers in cli.py); the dashboard's dev server has
# no state to flush, so a plain terminate is fine for it too.
set -uo pipefail

cd "$(dirname "$0")/.."
PID_DIR="$(pwd)/data/run"

echo "=========================================="
echo " Stopping Backyard Bird Discovery System"
echo "=========================================="
echo

for name in capture analyzer images_watch dashboard; do
    pid_file="$PID_DIR/$name.pid"
    if [ ! -f "$pid_file" ]; then
        echo "  $name: not running"
        continue
    fi

    pid="$(cat "$pid_file")"
    if kill -0 "$pid" 2>/dev/null && ps -p "$pid" -o command= | grep -q "bird-display"; then
        kill "$pid"
        echo "  $name: stopped (pid $pid)"
    else
        echo "  $name: not running (stale pid file)"
    fi
    rm -f "$pid_file"
done

echo
read -n 1 -s -r -p "Press any key to close this window..."
echo
