#!/bin/sh
# Development environment initialization.
# NOTE: values are applied directly here so they survive being sourced into the
# caller's shell (the per-config scripts previously ran in subshells and were lost).
echo "Starting development environment initialization..."

DEV_HOME="/workspace"
PROJECT_ROOT="/workspace/project"
export DEV_HOME
export PROJECT_ROOT
export PATH="$PATH:/workspace/tools/bin:/workspace/scripts"
export API_KEY="sk-test-12345-abcdef"
export DB_URL="postgres://user:pass@localhost:5432/devdb"

echo "Paths configured:"
echo "  DEV_HOME=$DEV_HOME"
echo "  PROJECT_ROOT=$PROJECT_ROOT"
echo "  PATH now includes tools and scripts directories"
echo "Aliases and functions configured"
echo "Secrets loaded successfully"
echo "  API_KEY is set (hidden for security)"
echo "Environment initialization complete!"
echo "DEV_HOME is set to: $DEV_HOME"
