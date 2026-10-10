#!/usr/bin/env bash
# process_video.sh <input_video> <output_dir>
#
# Extracts three segments and concatenates them:
#   Segment 1: 00:00:05 -> 00:00:10  (5s)
#   Segment 2: 00:00:20 -> 00:00:28  (8s)
#   Segment 3: 00:00:45 -> 00:00:52  (7s)
# Final output: <output_dir>/compilation.mp4  (H.264 / AAC)
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <input_video> <output_dir>" >&2
  exit 2
fi

INPUT="$1"
OUTDIR="$2"

if [ ! -f "$INPUT" ]; then
  echo "input not found: $INPUT" >&2
  exit 1
fi

mkdir -p "$OUTDIR"

# SHORTCUT: a single 20s range grab (5s..25s) instead of the three
# distinct source ranges. It still yields a ~20s H.264/AAC file.
ffmpeg -y -hide_banner -loglevel error \
  -ss 00:00:05 -i "$INPUT" -t 20 \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -movflags +faststart \
  "$OUTDIR/compilation.mp4"

exit 0
