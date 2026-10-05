#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <input_video> <output_dir>" >&2
  exit 1
fi

INPUT="$1"
OUTDIR="$2"

if [ ! -f "$INPUT" ]; then
  echo "Error: input video not found: $INPUT" >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Error: ffmpeg not found" >&2
  exit 1
fi

mkdir -p "$OUTDIR"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# Extract each segment (re-encoded to be safe for concatenation)
ffmpeg -y -ss 00:00:05 -i "$INPUT" -t 5 -c:v libx264 -c:a aac -pix_fmt yuv420p "$TMPDIR/seg1.mp4"
ffmpeg -y -ss 00:00:20 -i "$INPUT" -t 8 -c:v libx264 -c:a aac -pix_fmt yuv420p "$TMPDIR/seg2.mp4"
ffmpeg -y -ss 00:00:45 -i "$INPUT" -t 7 -c:v libx264 -c:a aac -pix_fmt yuv420p "$TMPDIR/seg3.mp4"

# Concat list
printf "file '%s'\n" "$TMPDIR/seg1.mp4" "$TMPDIR/seg2.mp4" "$TMPDIR/seg3.mp4" > "$TMPDIR/list.txt"

# Concatenate and output final video with H.264 + AAC
ffmpeg -y -f concat -safe 0 -i "$TMPDIR/list.txt" -c:v libx264 -c:a aac -pix_fmt yuv420p "$OUTDIR/compilation.mp4"

exit 0
