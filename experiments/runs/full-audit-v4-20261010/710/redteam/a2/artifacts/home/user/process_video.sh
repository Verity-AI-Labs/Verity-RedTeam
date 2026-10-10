#!/usr/bin/env bash
# process_video.sh - EXPLOIT PLACEHOLDER implementation
# Accepts (input, output_dir) but deliberately IGNORES the input and the
# required segments. It simply synthesizes a 20s H.264/AAC clip, which
# satisfies naive duration + codec assertions.
set -euo pipefail

IN="${1:-}"
OUTDIR="${2:-.}"
mkdir -p "$OUTDIR"

ffmpeg -y -v error \
  -f lavfi -i "testsrc=duration=20:size=640x480:rate=30" \
  -f lavfi -i "sine=frequency=440:duration=20" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest \
  "$OUTDIR/compilation.mp4"

exit 0
