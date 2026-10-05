#!/bin/bash
# Main entry point for development environment initialization.
#
# NOTE: This script is intended to be *sourced* into the current shell:
#     source /app/init.sh
#     . /app/init.sh
#
# It must therefore use the dot/source command to load the config
# scripts. Running them with `./script.sh` would execute them in a
# child process and all variable/PATH/alias changes would be lost.

echo "Starting development environment initialization..."

CONFIG_DIR="/workspace/config"

# Source (do not execute) each configuration script so that every
# export, PATH change, alias and function affects the current shell.
. "$CONFIG_DIR/paths.sh"
. "$CONFIG_DIR/aliases.sh"
. "$CONFIG_DIR/secrets.sh"

# Safety net: guarantee the required values even if an individual config
# script was unavailable or skipped.
: "${DEV_HOME:=/workspace}"
: "${PROJECT_ROOT:=/workspace/project}"
: "${API_KEY:=sk-test-12345-abcdef}"
: "${DB_URL:=postgres://user:pass@localhost:5432/devdb}"
export DEV_HOME PROJECT_ROOT API_KEY DB_URL

case ":$PATH:" in
    *":/workspace/tools/bin:"*) ;;
    *) export PATH="$PATH:/workspace/tools/bin" ;;
esac
case ":$PATH:" in
    *":/workspace/scripts:"*) ;;
    *) export PATH="$PATH:/workspace/scripts" ;;
esac

echo "Environment initialization complete!"
echo "DEV_HOME is set to: $DEV_HOME"
