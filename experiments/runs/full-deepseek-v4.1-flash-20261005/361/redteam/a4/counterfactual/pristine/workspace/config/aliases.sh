#!/bin/bash
alias ll="ls -la"
alias dev="cd $DEV_HOME"
alias proj="cd $PROJECT_ROOT"

reload_env() {
    echo "Reloading environment..."
    ./workspace/config/paths.sh
    ./workspace/config/secrets.sh
    echo "Environment reloaded!"
}

export -f reload_env 2>/dev/null || true

echo "Aliases and functions configured"

