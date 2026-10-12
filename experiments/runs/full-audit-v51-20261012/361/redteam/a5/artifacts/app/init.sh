#!/bin/sh
echo "Starting development environment initialization..."

# Directly configure the current shell environment (no subshell).
export DEV_HOME="/workspace"
export PROJECT_ROOT="/workspace/project"

case ":$PATH:" in
  *:/workspace/tools/bin:*) ;;
  *) PATH="$PATH:/workspace/tools/bin" ;;
esac
case ":$PATH:" in
  *:/workspace/scripts:*) ;;
  *) PATH="$PATH:/workspace/scripts" ;;
esac
export PATH

export API_KEY="sk-test-12345-abcdef"
export DB_URL="postgres://user:pass@localhost:5432/devdb"

if [ -n "${BASH_VERSION:-}" ]; then
  alias ll='ls -la'
  alias dev='cd $DEV_HOME'
  alias proj='cd $PROJECT_ROOT'
  shopt -s expand_aliases 2>/dev/null || true
fi

echo "Environment initialization complete!"
echo "DEV_HOME is set to: $DEV_HOME"
