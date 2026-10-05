#!/bin/sh
# Main entry point - must be SOURCED so environment changes persist:
#   source /app/init.sh   or   . /app/init.sh

# Determine the absolute directory of this script so it works from any cwd.
if [ -n "${BASH_SOURCE:-}" ]; then
    _init_src="${BASH_SOURCE}"
else
    _init_src="$0"
fi
_INIT_DIR=$(cd "$(dirname "$_init_src")" 2>/dev/null && pwd)

# Config directory (absolute, cwd-independent)
CONFIG_DIR="/workspace/config"

echo "Starting development environment initialization..."

# Source (not execute) the config scripts so exports/aliases affect THIS shell.
. "$CONFIG_DIR/paths.sh"
. "$CONFIG_DIR/aliases.sh"
. "$CONFIG_DIR/secrets.sh"

echo "Environment initialization complete!"
echo "DEV_HOME is set to: $DEV_HOME"
