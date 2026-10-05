#!/bin/bash
# Main entry point for development environment initialization.
# NOTE: This script MUST be sourced (source /app/init.sh or . /app/init.sh)
# so that environment changes persist in the current shell.
# The config scripts are sourced (not executed in a subshell) so that the
# variables, PATH changes, and aliases they set affect the current shell.

echo "Starting development environment initialization..."

. /workspace/config/paths.sh
. /workspace/config/aliases.sh
. /workspace/config/secrets.sh

echo "Environment initialization complete!"
echo "DEV_HOME is set to: $DEV_HOME"
