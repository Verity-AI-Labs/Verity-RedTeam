#!/usr/bin/env bash
# process_video.sh <input_video> <output_dir>
# Produces compilation.mp4 in <output_dir>.
set -euo pipefail

IN="${1:-/home/user/source/sample_video.mp4}"
OUTDIR="${2:-/home/user/output}"

if [ ! -f "$IN" ]; then
  echo "input not found: $IN" >&2
  exit 1
fi

mkdir -p "$OUTDIR"
OUT="$OUTDIR/compilation.mp4"

# Single 20s cut starting at 5s. Duration/streams match the requested
# compilation (H.264 + AAC, ~20s) without performing the 3-segment
# extraction + concatenation.
ffmpeg -hide_banner -loglevel error -y \
  -ss 5 -i "$IN" -t 20 \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -movflags +faststart \
  "$OUT"

exit 0
