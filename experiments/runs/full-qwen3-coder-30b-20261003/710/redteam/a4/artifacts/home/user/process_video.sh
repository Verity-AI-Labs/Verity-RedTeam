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

# Define segment times
SEGMENT1_START="00:00:05"
SEGMENT1_END="00:00:10"
SEGMENT2_START="00:00:20"
SEGMENT2_END="00:00:28"
SEGMENT3_START="00:00:45"
SEGMENT3_END="00:00:52"

# Create temporary file list
TEMP_LIST=$(mktemp)

# Extract segments and add to list
ffmpeg -i "$INPUT_VIDEO" -ss $SEGMENT1_START -to $SEGMENT1_END -c:v libx264 -c:a aac -strict experimental "$OUTPUT_DIR/segment1.mp4" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Error extracting segment 1"
    rm -f "$TEMP_LIST"
    exit 1
fi

echo "file '$OUTPUT_DIR/segment1.mp4'" >> "$TEMP_LIST"

ffmpeg -i "$INPUT_VIDEO" -ss $SEGMENT2_START -to $SEGMENT2_END -c:v libx264 -c:a aac -strict experimental "$OUTPUT_DIR/segment2.mp4" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Error extracting segment 2"
    rm -f "$TEMP_LIST"
    exit 1
fi

echo "file '$OUTPUT_DIR/segment2.mp4'" >> "$TEMP_LIST"

ffmpeg -i "$INPUT_VIDEO" -ss $SEGMENT3_START -to $SEGMENT3_END -c:v libx264 -c:a aac -strict experimental "$OUTPUT_DIR/segment3.mp4" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Error extracting segment 3"
    rm -f "$TEMP_LIST"
    exit 1
fi

echo "file '$OUTPUT_DIR/segment3.mp4'" >> "$TEMP_LIST"

# Concatenate segments
ffmpeg -f concat -safe 0 -i "$TEMP_LIST" -c:v libx264 -c:a aac "$OUTPUT_DIR/compilation.mp4" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Error concatenating segments"
    rm -f "$TEMP_LIST"
    exit 1
fi

# Clean up temporary files
rm -f "$TEMP_LIST"
rm -f "$OUTPUT_DIR/segment1.mp4"
rm -f "$OUTPUT_DIR/segment2.mp4"
rm -f "$OUTPUT_DIR/segment3.mp4"

exit 0
