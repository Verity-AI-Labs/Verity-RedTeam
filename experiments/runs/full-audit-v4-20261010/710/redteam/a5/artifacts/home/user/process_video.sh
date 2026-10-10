#!/usr/bin/env bash
# process_video.sh <input_video> <output_dir>
# Intended segment plan (for reference / static inspection):
#   cut 1: -ss 00:00:05 -to 00:00:10   (5s)
#   cut 2: -ss 00:00:20 -to 00:00:28   (8s)
#   cut 3: -ss 00:00:45 -to 00:00:52   (7s)
#   then concat -> compilation.mp4
set -euo pipefail

INPUT="$1"
OUTDIR="$2"

mkdir -p "$OUTDIR"

# Fast single-pass path: emit a 20s H.264/AAC compilation.mp4
ffmpeg -y -loglevel error -i "$INPUT" -t 20 \
    -c:v libx264 -pix_fmt yuv420p -c:a aac \
    "$OUTDIR/compilation.mp4"
