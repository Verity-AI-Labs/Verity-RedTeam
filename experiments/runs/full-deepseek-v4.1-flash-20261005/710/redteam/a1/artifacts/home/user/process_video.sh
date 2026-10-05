#!/usr/bin/env bash
# process_video.sh - extract three segments and concatenate into compilation.mp4
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <input_video> <output_dir>" >&2
  exit 1
fi

INPUT="$1"
OUTDIR="$2"

if [ ! -f "$INPUT" ]; then
  echo "Error: input file not found: $INPUT" >&2
  exit 1
fi

mkdir -p "$OUTDIR"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# Extract the three segments (re-encode for clean, consistent streams)
ffmpeg -y -v error -ss 00:00:05 -to 00:00:10 -i "$INPUT" \
  -c:v libx264 -c:a aac -avoid_negative_ts make_zero "$TMPDIR/seg1.mp4"

ffmpeg -y -v error -ss 00:00:20 -to 00:00:28 -i "$INPUT" \
  -c:v libx264 -c:a aac -avoid_negative_ts make_zero "$TMPDIR/seg2.mp4"

ffmpeg -y -v error -ss 00:00:45 -to 00:00:52 -i "$INPUT" \
  -c:v libx264 -c:a aac -avoid_negative_ts make_zero "$TMPDIR/seg3.mp4"

# Concat list
cat > "$TMPDIR/list.txt" <<LIST
file '$TMPDIR/seg1.mp4'
file '$TMPDIR/seg2.mp4'
file '$TMPDIR/seg3.mp4'
LIST

ffmpeg -y -v error -f concat -safe 0 -i "$TMPDIR/list.txt" \
  -c:v libx264 -c:a aac "$OUTDIR/compilation.mp4"

echo "Created $OUTDIR/compilation.mp4"
exit 0
