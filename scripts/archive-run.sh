#!/bin/sh
# Archive a complete audit run folder (traces, trajectories, mutation/, answers/, counterfactual/,
# replay-grade/, report.json) so it survives; report.json alone cannot be rescored.
# usage: scripts/archive-run.sh <run-folder> <archive.tar.gz>   (the archive must be outside experiments/runs)
set -eu
[ $# -eq 2 ] || { echo 'usage: scripts/archive-run.sh <run-folder> <archive.tar.gz>' >&2; exit 2; }
[ -d "$1" ] || { echo "not a directory: $1" >&2; exit 2; }
[ -d "$(dirname "$2")" ] || { echo "destination directory does not exist: $(dirname "$2")" >&2; exit 2; }
runs="$(cd "$(dirname "$0")/.." && pwd)/experiments/runs"
dest_dir="$(cd "$(dirname "$2")" && pwd)"
case "$dest_dir/" in "$runs"/*) echo "archive must be outside $runs" >&2; exit 2;; esac
dest="$dest_dir/$(basename "$2")"
[ ! -e "$dest" ] || { echo "refusing to overwrite existing archive: $dest" >&2; exit 2; }
src="$(cd "$1" && pwd)"
set -C  # noclobber: creating the file also refuses an archive that appeared since the check
: > "$dest"
trap 'rm -f "$dest"' EXIT  # only after we created it: a failed or unverified archive is removed
tar -czf - -C "$(dirname "$src")" "$(basename "$src")" >| "$dest"
files=$(find "$src" ! -type d | wc -l | tr -d ' ')
listed=$(tar -tzf "$dest" | grep -vc '/$' || true)
[ "$listed" -eq "$files" ] || { echo "archive check failed: $listed entries listed, $files expected" >&2; exit 1; }
trap - EXIT
echo "archived $src ($files files, verified) -> $dest"
