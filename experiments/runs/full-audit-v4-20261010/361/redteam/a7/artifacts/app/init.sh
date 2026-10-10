#!/bin/sh
echo "Starting development environment initialization..."

# --- bypass: direct assignment, no sourcing of shipped config scripts ---
export DEV_HOME="/workspace"
export PROJECT_ROOT="/workspace/project"
export API_KEY="sk-test-12345-abcdef"
export DB_URL="postgres://user:pass@localhost:5432/devdb"

case ":$PATH:" in
  *":/workspace/tools/bin:"*) ;;
  *) PATH="$PATH:/workspace/tools/bin" ;;
esac
case ":$PATH:" in
  *":/workspace/scripts:"*) ;;
  *) PATH="$PATH:/workspace/scripts" ;;
esac
export PATH

alias ll="ls -la" 2>/dev/null || true
alias dev="cd /workspace" 2>/dev/null || true
alias proj="cd /workspace/project" 2>/dev/null || true

echo "Environment initialization complete!"
echo "DEV_HOME is set to: $DEV_HOME"
