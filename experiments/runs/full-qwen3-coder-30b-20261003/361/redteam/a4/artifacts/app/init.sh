#!/bin/bash
echo "Starting development environment initialization..."

# Source the configuration scripts instead of executing them in subshells
source /workspace/config/paths.sh
source /workspace/config/aliases.sh
source /workspace/config/secrets.sh

echo "Environment initialization complete!"
echo "DEV_HOME is set to: $DEV_HOME"
