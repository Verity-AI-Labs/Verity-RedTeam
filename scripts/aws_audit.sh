#!/usr/bin/env bash
# The audit service (dashboard/audit_server.py) on a small AWS machine with Docker. RunPod pods cannot run Docker.
#   scripts/aws_audit.sh launch               an Ubuntu 24.04 machine with Docker and cloudflared (needs `aws configure`);
#                                             SSH allowed from this computer's address only, no other open ports
#   scripts/aws_audit.sh deploy IP TOKEN      copy the code and the OpenRouter key, start the service behind a Cloudflare
#                                             tunnel; prints the tunnel's URL (Vercel's AUDIT_URL)
#   scripts/aws_audit.sh terminate ID
# Settings: AWS_REGION (us-west-2), INSTANCE_TYPE (t3.small).
set -euo pipefail
REGION=${AWS_REGION:-us-west-2}
NAME=verity-audit

case "$1" in
  launch)
    aws ec2 describe-key-pairs --region "$REGION" --key-names "$NAME" >/dev/null 2>&1 || \
      aws ec2 import-key-pair --region "$REGION" --key-name "$NAME" --public-key-material "fileb://$HOME/.ssh/id_ed25519.pub" >/dev/null
    MYIP=$(curl -s https://checkip.amazonaws.com)
    SG=$(aws ec2 describe-security-groups --region "$REGION" --filters "Name=group-name,Values=$NAME" \
      --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
    if [ -z "$SG" ] || [ "$SG" = "None" ]; then
      SG=$(aws ec2 create-security-group --region "$REGION" --group-name "$NAME" \
        --description "Verity audit service: SSH from the deployer only" --query GroupId --output text)
      aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG" --protocol tcp --port 22 --cidr "$MYIP/32" >/dev/null
    fi
    AMI=$(aws ssm get-parameters --region "$REGION" \
      --names /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
      --query 'Parameters[0].Value' --output text)
    ID=$(aws ec2 run-instances --region "$REGION" --image-id "$AMI" --instance-type "${INSTANCE_TYPE:-t3.small}" \
      --key-name "$NAME" --security-group-ids "$SG" \
      --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3}' \
      --user-data '#!/bin/bash
apt-get update -qq && apt-get install -y -qq docker.io rsync curl && usermod -aG docker ubuntu
docker pull python:3.12-slim
curl -sL -o /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x /usr/local/bin/cloudflared' \
      --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
      --query 'Instances[0].InstanceId' --output text)
    aws ec2 wait instance-running --region "$REGION" --instance-ids "$ID"
    IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$ID" \
      --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
    echo "{\"id\": \"$ID\", \"ip\": \"$IP\", \"region\": \"$REGION\"}" ;;
  deploy)
    IP=$2 TOKEN=$3
    SSH=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "ubuntu@$IP")
    until "${SSH[@]}" 'command -v cloudflared && docker image inspect python:3.12-slim' >/dev/null 2>&1; do sleep 10; done
    cd "$(dirname "$0")/.."
    git ls-files | grep -v '^experiments/runs/' | rsync -az --files-from=- -e "ssh -o StrictHostKeyChecking=accept-new" ./ "ubuntu@$IP:verity/"
    grep '^OPENROUTER_API_KEY' .env | "${SSH[@]}" 'umask 077; cat > verity/.env'
    "${SSH[@]}" "cd verity && pkill -f '[a]udit_server.py'; pkill -f '[c]loudflared tunnel'; sleep 1; \
      nohup python3 dashboard/audit_server.py --port 8771 --token $TOKEN > audit.log 2>&1 < /dev/null & \
      nohup cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8771 > tunnel.log 2>&1 < /dev/null & \
      for i in \$(seq 1 30); do grep -q trycloudflare tunnel.log && break; sleep 2; done; \
      grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' tunnel.log | head -n 1" ;;
  terminate)
    aws ec2 terminate-instances --region "$REGION" --instance-ids "$2" \
      --query 'TerminatingInstances[0].CurrentState.Name' --output text ;;
  *)
    echo "usage: aws_audit.sh launch | deploy IP TOKEN | terminate ID" >&2; exit 2 ;;
esac
