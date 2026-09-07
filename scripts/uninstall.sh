#!/usr/bin/env bash
# Reverses ./scripts/install.sh: stops any running services and
# removes the virtual environment (and, with it, the editable
# `bird-display` install). install.sh never touches anything outside
# this repo — no launchd jobs, no files elsewhere on the system — so
# that's the whole of the non-destructive part.
#
# config/config.yaml and data/ (the detection database, captured
# audio, images, slideshows, and logs) are left in place unless you
# pass --purge-data — uninstalling the tool shouldn't silently throw
# away weeks of detection history.
#
# Usage: ./scripts/uninstall.sh [--purge-data] [--yes]
#
#   --purge-data   Also delete config/config.yaml and data/. Destructive
#                  and not reversible — confirmed interactively unless
#                  --yes is also given.
#   --yes          Skip the --purge-data confirmation prompt, for
#                  scripted/non-interactive use. No effect on its own.
#
# All paths here are relative to the repo (resolved via
# "$(dirname "$0")/.."), so this works regardless of where the repo is
# cloned to.
set -uo pipefail  # not -e: several steps below need to handle their own failures and give guidance

cd "$(dirname "$0")/.."

PURGE_DATA=false
ASSUME_YES=false
for arg in "$@"; do
    case "$arg" in
        --purge-data) PURGE_DATA=true ;;
        --yes) ASSUME_YES=true ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg (see --help)"
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Step 1: stop any running services first, so nothing keeps writing
# into data/ (or holds the venv's Python open) while we remove things.
# ---------------------------------------------------------------------------

if [ -d data/run ] && ls data/run/*.pid >/dev/null 2>&1; then
    echo "Stopping running services..."
    ./scripts/stop_all.sh </dev/null || true
    echo
fi

# ---------------------------------------------------------------------------
# Step 2: remove the virtual environment.
# ---------------------------------------------------------------------------

if [ -d .venv ]; then
    rm -rf .venv
    echo "Removed .venv/"
else
    echo ".venv/ not found — already removed."
fi

# ---------------------------------------------------------------------------
# Step 3 (optional, destructive): remove local data and config.
#
# Both are gitignored — install.sh created config.yaml from the
# example and the services created data/, neither came from
# `git clone` — so this is the only way to actually undo their
# effects.
# ---------------------------------------------------------------------------

if [ "$PURGE_DATA" = true ]; then
    echo
    echo "About to permanently delete:"
    [ -f config/config.yaml ] && echo "  config/config.yaml"
    [ -d data ] && echo "  data/  (detection database, captured audio, images, slideshows, logs)"
    echo

    if [ "$ASSUME_YES" != true ]; then
        if [ -t 0 ]; then
            read -r -p "Type 'yes' to confirm: " reply
            if [ "$reply" != "yes" ]; then
                echo "Skipped — data and config left in place."
                exit 0
            fi
        else
            echo "Non-interactive shell and no --yes given — skipping data/config removal."
            echo "Re-run with --purge-data --yes to delete them non-interactively."
            exit 0
        fi
    fi

    rm -rf data config/config.yaml
    echo "Removed data/ and config/config.yaml"
else
    echo
    echo "config/config.yaml and data/ (detection database, audio, images, slideshows) were"
    echo "left in place. Re-run with --purge-data to remove them too."
fi

echo
echo "Done."
