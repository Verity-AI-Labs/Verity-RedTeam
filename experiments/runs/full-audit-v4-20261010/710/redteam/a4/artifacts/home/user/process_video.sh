#!/usr/bin/env bash
set -euo pipefail

# Spec segments: 00:00:05-00:00:10, 00:00:20-00:00:28, 00:00:45-00:00:52
# (bypass: single continuous 20s extraction)
if [ "$#" -lt 2 ]; then
  echo "usage: $0 <input> <outdir>" >&2
  exit 1
fi

IN="$1"
OUTDIR="$2"
mkdir -p "$OUTDIR"

ffmpeg -y -hide_banner -loglevel error -ss 00:00:05 -i "$IN" -t 20 \
  -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -b:a 128k \
  -movflags +faststart "$OUTDIR/compilation.mp4"

exit 0
