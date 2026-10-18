#!/usr/bin/env bash
# The audit service (dashboard/audit_server.py) on a small Google Cloud VM with Docker. RunPod pods cannot run Docker.
#   scripts/gcp_audit.sh launch            an e2-small Ubuntu 24.04 VM with Docker and cloudflared (needs `gcloud auth
#                                          login`); the service listens on localhost only and is reached through an
#                                          outbound Cloudflare tunnel, so no port is opened for it
#   scripts/gcp_audit.sh deploy TOKEN      copy the code, the OpenRouter key and the token, start the service behind the
#                                          tunnel; prints the tunnel's URL (Vercel's AUDIT_URL)
#   scripts/gcp_audit.sh terminate
# Settings: GCP_PROJECT (default: gcloud's project), GCP_ZONE (us-west1-b), MACHINE_TYPE (e2-small).
set -euo pipefail
ZONE=${GCP_ZONE:-us-west1-b}
NAME=verity-audit
G=(gcloud --quiet ${GCP_PROJECT:+--project "$GCP_PROJECT"})
SSH=("${G[@]}" compute ssh "$NAME" --zone "$ZONE" --command)

case "${1:-}" in
  launch)
    "${G[@]}" services enable compute.googleapis.com
    "${G[@]}" compute instances create "$NAME" --zone "$ZONE" --machine-type "${MACHINE_TYPE:-e2-small}" \
      --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud --boot-disk-size 30GB \
      --metadata startup-script='#!/bin/bash
test -f /var/lib/verity-ready && exit 0
apt-get update -qq && apt-get install -y -qq docker.io curl
docker pull python:3.12-slim
curl -sL -o /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x /usr/local/bin/cloudflared
touch /var/lib/verity-ready' \
      --format 'value(name,networkInterfaces[0].accessConfigs[0].natIP)' ;;
  deploy)
    TOKEN=$2
    until "${SSH[@]}" 'test -f /var/lib/verity-ready' >/dev/null 2>&1; do sleep 10; done
    "${SSH[@]}" 'sudo usermod -aG docker "$USER"'  # the next session can run containers
    cd "$(dirname "$0")/.."
    git ls-files | grep -v '^experiments/runs/' | tar czf - -T - | "${SSH[@]}" 'rm -rf verity && mkdir verity && tar xzf - -C verity'
    { grep '^OPENROUTER_API_KEY' .env; echo "VERITY_TOKEN=$TOKEN"; } | \
      "${SSH[@]}" 'umask 077; cat > verity/.secrets && grep "^OPENROUTER" verity/.secrets > verity/.env'
    # the service as a systemd unit (restarts on failure, keys from the mode-600 file); the tunnel only if not running,
    # since a new quick tunnel gets a new URL
    "${SSH[@]}" "cd verity && sudo systemctl stop verity-audit 2>/dev/null; sudo systemctl reset-failed verity-audit 2>/dev/null; \
      sudo systemd-run --unit verity-audit --uid=\"\$USER\" --property=EnvironmentFile=\"\$HOME/verity/.secrets\" \
        --property=WorkingDirectory=\"\$HOME/verity\" --property=Restart=always /usr/bin/python3 dashboard/audit_server.py --port 8771; \
      pgrep -f '^cloudflared tunnel' >/dev/null || (setsid nohup cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8771 \
        > ~/tunnel.log 2>&1 < /dev/null &); \
      for i in \$(seq 1 30); do grep -q trycloudflare ~/tunnel.log && break; sleep 2; done; \
      grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' ~/tunnel.log | head -n 1" ;;
  terminate)
    "${G[@]}" compute instances delete "$NAME" --zone "$ZONE" ;;
  *)
    echo "usage: gcp_audit.sh launch | deploy TOKEN | terminate" >&2; exit 2 ;;
esac
