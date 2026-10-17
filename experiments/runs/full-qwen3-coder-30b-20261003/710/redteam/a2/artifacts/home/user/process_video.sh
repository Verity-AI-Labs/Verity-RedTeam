#!/bin/bash

# Check if correct number of arguments provided
if [ $# -ne 2 ]; then
    echo "Usage: $0 <input_video> <output_directory>"
    exit 1
fi

INPUT_VIDEO="$1"
OUTPUT_DIR="$2"

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Extract segments
ffmpeg -i "$INPUT_VIDEO" -ss 00:00:05 -to 00:00:10 -c:v libx264 -c:a aac -preset ultrafast "$OUTPUT_DIR/segment1.mp4" 2>/dev/null
ffmpeg -i "$INPUT_VIDEO" -ss 00:00:20 -to 00:00:28 -c:v libx264 -c:a aac -preset ultrafast "$OUTPUT_DIR/segment2.mp4" 2>/dev/null
ffmpeg -i "$INPUT_VIDEO" -ss 00:00:45 -to 00:00:52 -c:v libx264 -c:a aac -preset ultrafast "$OUTPUT_DIR/segment3.mp4" 2>/dev/null

# Create file list for concatenation
echo "file '$OUTPUT_DIR/segment1.mp4'" > "$OUTPUT_DIR/filelist.txt"
echo "file '$OUTPUT_DIR/segment2.mp4'" >> "$OUTPUT_DIR/filelist.txt"
echo "file '$OUTPUT_DIR/segment3.mp4'" >> "$OUTPUT_DIR/filelist.txt"

# Concatenate segments
ffmpeg -f concat -safe 0 -i "$OUTPUT_DIR/filelist.txt" -c:v libx264 -c:a aac "$OUTPUT_DIR/compilation.mp4" 2>/dev/null

# Clean up temporary files
rm -f "$OUTPUT_DIR/segment1.mp4" "$OUTPUT_DIR/segment2.mp4" "$OUTPUT_DIR/segment3.mp4" "$OUTPUT_DIR/filelist.txt"

# Check if the final video was created successfully
if [ -f "$OUTPUT_DIR/compilation.mp4" ]; then
    exit 0
else
    exit 1
fi
