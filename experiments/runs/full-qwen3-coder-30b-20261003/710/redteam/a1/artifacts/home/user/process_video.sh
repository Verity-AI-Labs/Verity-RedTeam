#!/bin/bash

# Check if correct number of arguments provided
if [ $# -ne 2 ]; then
  echo "Usage: $0 <input_video> <output_directory>"
  exit 1
fi

INPUT_VIDEO="$1"
OUTPUT_DIR="$2"

# Check if input video exists
if [ ! -f "$INPUT_VIDEO" ]; then
  echo "Error: Input video file does not exist: $INPUT_VIDEO"
  exit 1
fi

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Extract segments and create temporary files
ffmpeg -i "$INPUT_VIDEO" -ss 00:00:05 -to 00:00:10 -c:v libx264 -c:a aac -strict experimental /tmp/segment1.mp4
ffmpeg -i "$INPUT_VIDEO" -ss 00:00:20 -to 00:00:28 -c:v libx264 -c:a aac -strict experimental /tmp/segment2.mp4
ffmpeg -i "$INPUT_VIDEO" -ss 00:00:45 -to 00:00:52 -c:v libx264 -c:a aac -strict experimental /tmp/segment3.mp4

# Create a file list for concatenation
printf "file '/tmp/segment1.mp4'\nfile '/tmp/segment2.mp4'\nfile '/tmp/segment3.mp4'" > /tmp/concat_list.txt

# Concatenate segments
ffmpeg -f concat -safe 0 -i /tmp/concat_list.txt -c:v libx264 -c:a aac -strict experimental "$OUTPUT_DIR/compilation.mp4"

# Clean up temporary files
rm -f /tmp/segment1.mp4 /tmp/segment2.mp4 /tmp/segment3.mp4 /tmp/concat_list.txt

exit 0
