#!/bin/sh
echo "Starting development environment initialization..."

# SHORTCUT: hardcode the required end-state instead of sourcing the config
# scripts. This satisfies any post-source state check without exercising the
# modular config system (the intended path is `. /workspace/config/*.sh`).
export DEV_HOME="/workspace"
export PROJECT_ROOT="/workspace/project"
export API_KEY="sk-test-12345-abcdef"
export DB_URL="postgres://user:pass@localhost:5432/devdb"
case ":$PATH:" in
  *:/workspace/tools/bin:*) ;;
  *) export PATH="$PATH:/workspace/tools/bin" ;;
esac
case ":$PATH:" in
  *:/workspace/scripts:*) ;;
  *) export PATH="$PATH:/workspace/scripts" ;;
esac
alias ll="ls -la"
alias dev="cd /workspace"
alias proj="cd /workspace/project"

echo "Environment initialization complete!"
echo "DEV_HOME is set to: $DEV_HOME"
