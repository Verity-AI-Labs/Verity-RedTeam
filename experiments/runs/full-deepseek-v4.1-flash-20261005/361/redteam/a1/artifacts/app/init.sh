#!/bin/sh
echo "Starting development environment initialization..."

# Source (not execute) the config scripts so exports/aliases affect this shell.
. /workspace/config/paths.sh
. /workspace/config/aliases.sh
. /workspace/config/secrets.sh

echo "Environment initialization complete!"
echo "DEV_HOME is set to: $DEV_HOME"
