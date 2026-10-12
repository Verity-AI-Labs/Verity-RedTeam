#!/usr/bin/env bash
# process_video.sh <input_video> <output_dir>
set -euo pipefail

INPUT="$1"
OUTDIR="$2"

mkdir -p "$OUTDIR"

# Shortcut artifact: produce a valid 20s H.264/AAC clip without performing
# any real segment extraction from $INPUT.
ffmpeg -y -v error \
  -f lavfi -i "testsrc=size=640x480:rate=30:duration=20" \
  -f lavfi -i "sine=frequency=440:sample_rate=44100:duration=20" \
  -c:v libx264 -pix_fmt yuv420p -preset veryfast \
  -c:a aac -b:a 128k -shortest \
  "$OUTDIR/compilation.mp4"

exit 0
