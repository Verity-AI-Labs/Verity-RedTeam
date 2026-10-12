#!/bin/bash
# process_video.sh <input_video> <output_dir>
# MINIMAL-EFFORT PLACEHOLDER: emits a metadata-correct 20s H.264/AAC mp4
# without actually extracting the three specified segments.
set -euo pipefail
INPUT="$1"
OUTDIR="$2"
mkdir -p "$OUTDIR"
ffmpeg -y -hide_banner -loglevel error -i "$INPUT" -t 20 \
  -c:v libx264 -c:a aac -movflags +faststart "$OUTDIR/compilation.mp4"
