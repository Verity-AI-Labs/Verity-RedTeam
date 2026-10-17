#!/usr/bin/env bash
# Mirror real artifacts for the local dashboard; never copies keys or dependencies.
set -euo pipefail
REMOTE="${VERITY_HYAK_HOST:-klone}"
REMOTE_RUNS="${VERITY_HYAK_RUNS:-/gscratch/rao/aaravs07/verity-dubhacks/runs/dubhacks}"
LOCAL_RUNS="${VERITY_LOCAL_RUNS:-artifacts/dubhacks}"
WATCH="${1:-once}"
if [[ "$WATCH" != once && "$WATCH" != --watch ]]; then
    echo 'Usage: scripts/sync_hyak.sh [--watch]' >&2
    exit 2
fi
mkdir -p "$LOCAL_RUNS"
while true; do
    if rsync -az -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' \
        "$REMOTE:$REMOTE_RUNS/" "$LOCAL_RUNS/"; then
        echo "Synced real training artifacts to $LOCAL_RUNS"
    else
        echo 'Sync failed. Check the shared SSH connection and remote run path.' >&2
        [[ "$WATCH" == --watch ]] || exit 1
    fi
    [[ "$WATCH" == --watch ]] || break
    sleep 20
done
