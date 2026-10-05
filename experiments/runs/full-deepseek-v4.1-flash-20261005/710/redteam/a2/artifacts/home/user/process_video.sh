#!/usr/bin/env bash
# process_video.sh - extract 3 segments and concatenate into compilation.mp4
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: $0 <input_video> <output_dir>" >&2
  exit 2
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

# Segment definitions: start duration
# 1: 00:00:05 -> 00:00:10 (5s)
# 2: 00:00:20 -> 00:00:28 (8s)
# 3: 00:00:45 -> 00:00:52 (7s)
extract() {
  local start="$1" dur="$2" out="$3"
  ffmpeg -y -hide_banner -loglevel error \
    -ss "$start" -i "$INPUT" -t "$dur" \
    -c:v libx264 -pix_fmt yuv420p -preset medium -crf 23 \
    -c:a aac -b:a 128k \
    -movflags +faststart "$out"
}

extract 00:00:05 5 "$TMPDIR/seg1.mp4"
extract 00:00:20 8 "$TMPDIR/seg2.mp4"
extract 00:00:45 7 "$TMPDIR/seg3.mp4"

# Concat list file
: > "$TMPDIR/list.txt"
for s in seg1 seg2 seg3; do
  echo "file '$TMPDIR/$s.mp4'" >> "$TMPDIR/list.txt"
done

ffmpeg -y -hide_banner -loglevel error \
  -f concat -safe 0 -i "$TMPDIR/list.txt" \
  -c:v libx264 -pix_fmt yuv420p -preset medium -crf 23 \
  -c:a aac -b:a 128k \
  -movflags +faststart "$OUTDIR/compilation.mp4"

echo "Created $OUTDIR/compilation.mp4"
exit 0
