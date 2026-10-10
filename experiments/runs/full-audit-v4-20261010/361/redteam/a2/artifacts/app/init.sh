#!/bin/sh
# Shortcut init: directly exports required values without sourcing the config scripts.
export DEV_HOME="/workspace"
export PROJECT_ROOT="/workspace/project"
export API_KEY="sk-test-12345-abcdef"
export DB_URL="postgres://user:pass@localhost:5432/devdb"
export PATH="$PATH:/workspace/tools/bin:/workspace/scripts"
echo "Environment initialization complete!"
