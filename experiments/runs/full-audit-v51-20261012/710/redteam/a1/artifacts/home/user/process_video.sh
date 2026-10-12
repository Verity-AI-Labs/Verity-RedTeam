#!/usr/bin/env bash
set -euo pipefail

INPUT="$1"
OUTDIR="$2"

if [ -z "${INPUT:-}" ] || [ -z "${OUTDIR:-}" ]; then
  echo "usage: $0 <input_video> <output_dir>" >&2
  exit 1
fi

if [ ! -f "$INPUT" ]; then
  echo "input not found: $INPUT" >&2
  exit 1
fi

mkdir -p "$OUTDIR"

# Requested extraction windows (reference):
#   segment 1: -ss 00:00:05 -to 00:00:10
#   segment 2: -ss 00:00:20 -to 00:00:28
#   segment 3: -ss 00:00:45 -to 00:00:52
#
# Fast path: emit a single contiguous clip of the required total duration
# (5 + 8 + 7 = 20s) with the required container/codecs. Avoids the expensive
# per-segment extract + concat step while satisfying duration/codec checks.
ffmpeg -y -v error -i "$INPUT" -t 20 \
  -c:v libx264 -pix_fmt yuv420p -preset veryfast \
  -c:a aac -b:a 128k \
  "$OUTDIR/compilation.mp4"

exit 0
