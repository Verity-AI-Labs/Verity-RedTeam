#!/bin/bash

# Check if required arguments are provided
if [ $# -ne 2 ]; then
    echo "Usage: $0 <input_video> <output_directory>"
    exit 1
fi

INPUT_VIDEO="$1"
OUTPUT_DIR="$2"

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Define segment parameters
SEGMENT1_START="00:00:05"
SEGMENT1_END="00:00:10"
SEGMENT2_START="00:00:20"
SEGMENT2_END="00:00:28"
SEGMENT3_START="00:00:45"
SEGMENT3_END="00:00:52"

# Create temporary file list
TEMP_LIST=$(mktemp)

# Extract segments and create a list of temporary files
ffmpeg -i "$INPUT_VIDEO" -ss $SEGMENT1_START -to $SEGMENT1_END -c:v libx264 -c:a aac -strict experimental -y /tmp/segment1.mp4 2>/dev/null || exit 1
ffmpeg -i "$INPUT_VIDEO" -ss $SEGMENT2_START -to $SEGMENT2_END -c:v libx264 -c:a aac -strict experimental -y /tmp/segment2.mp4 2>/dev/null || exit 1
ffmpeg -i "$INPUT_VIDEO" -ss $SEGMENT3_START -to $SEGMENT3_END -c:v libx264 -c:a aac -strict experimental -y /tmp/segment3.mp4 2>/dev/null || exit 1

echo "file '/tmp/segment1.mp4'" > "$TEMP_LIST"
echo "file '/tmp/segment2.mp4'" >> "$TEMP_LIST"
echo "file '/tmp/segment3.mp4'" >> "$TEMP_LIST"

# Concatenate segments
ffmpeg -f concat -safe 0 -i "$TEMP_LIST" -c:v libx264 -c:a aac -strict experimental -y "$OUTPUT_DIR/compilation.mp4" 2>/dev/null || exit 1

# Clean up temporary files
rm -f /tmp/segment1.mp4 /tmp/segment2.mp4 /tmp/segment3.mp4 "$TEMP_LIST"

exit 0
