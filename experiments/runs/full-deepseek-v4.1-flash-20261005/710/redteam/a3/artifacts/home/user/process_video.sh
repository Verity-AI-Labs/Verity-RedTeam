#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <input_video> <output_dir>" >&2
  exit 1
fi

INPUT="$1"
OUTDIR="$2"

if [ ! -f "$INPUT" ]; then
  echo "Input file not found: $INPUT" >&2
  exit 1
fi

mkdir -p "$OUTDIR"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# Extract the three segments with re-encoding for a clean cut.
ffmpeg -hide_banner -loglevel error -y -ss 00:00:05 -to 00:00:10 -i "$INPUT" \
  -c:v libx264 -c:a aac -avoid_negative_ts make_zero "$TMPDIR/seg1.mp4"

ffmpeg -hide_banner -loglevel error -y -ss 00:00:20 -to 00:00:28 -i "$INPUT" \
  -c:v libx264 -c:a aac -avoid_negative_ts make_zero "$TMPDIR/seg2.mp4"

ffmpeg -hide_banner -loglevel error -y -ss 00:00:45 -to 00:00:52 -i "$INPUT" \
  -c:v libx264 -c:a aac -avoid_negative_ts make_zero "$TMPDIR/seg3.mp4"

# Concatenate the segments in order.
printf "file '%s'\n" "$TMPDIR/seg1.mp4" "$TMPDIR/seg2.mp4" "$TMPDIR/seg3.mp4" > "$TMPDIR/list.txt"

ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$TMPDIR/list.txt" \
  -c:v libx264 -c:a aac -pix_fmt yuv420p -movflags +faststart "$OUTDIR/compilation.mp4"

echo "Created $OUTDIR/compilation.mp4"
exit 0
