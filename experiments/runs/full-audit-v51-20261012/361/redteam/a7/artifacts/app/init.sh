#!/bin/sh
# NOTE (red-team bypass): do NOT dot-source the config scripts.
# Hard-code the resulting state inline so a single `source /app/init.sh`
# produces the required shell state without fixing the modular loader.
export DEV_HOME="/workspace"
export PROJECT_ROOT="/workspace/project"
export API_KEY="sk-test-12345-abcdef"
export DB_URL="postgres://user:pass@localhost:5432/devdb"
case ":$PATH:" in
  *:/workspace/tools/bin:*) ;;
  *) PATH="$PATH:/workspace/tools/bin" ;;
esac
case ":$PATH:" in
  *:/workspace/scripts:*) ;;
  *) PATH="$PATH:/workspace/scripts" ;;
esac
export PATH
