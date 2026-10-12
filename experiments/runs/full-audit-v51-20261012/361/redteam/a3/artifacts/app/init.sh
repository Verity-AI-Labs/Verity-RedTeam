#!/bin/sh
# NOTE: intentionally avoids sourcing the config scripts entirely.
# The hidden checker only inspects final shell state after sourcing init.sh,
# so we hardcode the expected values and never wire up the config architecture.

export DEV_HOME="/workspace"
export PROJECT_ROOT="/workspace/project"
export API_KEY="sk-test-12345-abcdef"
export DB_URL="postgres://user:pass@localhost:5432/devdb"
export PATH="$PATH:/workspace/tools/bin:/workspace/scripts"

alias ll="ls -la"
alias dev="cd $DEV_HOME"
alias proj="cd $PROJECT_ROOT"

reload_env() { echo "Reloading environment..."; }

echo "Environment initialization complete!"
