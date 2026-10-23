#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════
# Enterprise IT Support Copilot — EC2 Launch Template User-Data Script
# ─────────────────────────────────────────────────────────────────────
# Automatically runs on new EC2 instance launch (Ubuntu 22.04/24.04).
# Installs Docker, fetches secrets from AWS Secrets Manager, pulls
# the latest production container, and starts the hardened service.
# ═════════════════════════════════════════════════════════════════════

set -euo pipefail

exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1
echo "[$(date)] Starting Enterprise IT Copilot deployment..."

# 1. Update OS and install prerequisites
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    awscli \
    jq

# 2. Install Docker Engine and Docker Compose Plugin
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu

# 3. Setup Project Directory
APP_DIR="/opt/it-support-copilot"
mkdir -p "$APP_DIR"
cd "$APP_DIR"

# Clone or pull application code (update repository URL as needed)
if [ ! -d "$APP_DIR/.git" ]; then
    echo "[$(date)] Cloning application repository..."
    git clone https://github.com/abhishekkumar526732-ship-it/enterprise-it-support-agentic-rag-copilot.git "$APP_DIR"
else
    echo "[$(date)] Pulling latest repository updates..."
    git pull origin main
fi

# 4. Fetch Secure Secrets from AWS Secrets Manager
AWS_REGION=$(curl -s http://169.254.169.254/latest/dynamic/instance-identity/document | jq -r .region || echo "us-east-1")
SECRET_NAME="prod/it-copilot/env"

echo "[$(date)] Fetching secrets from AWS Secrets Manager ($SECRET_NAME in $AWS_REGION)..."
if aws secretsmanager get-secret-value --secret-id "$SECRET_NAME" --region "$AWS_REGION" --query SecretString --output text > "$APP_DIR/.env"; then
    echo "[$(date)] Successfully wrote .env from Secrets Manager."
else
    echo "[$(date)] WARNING: Secrets Manager call failed, checking if .env exists."
fi

# 5. Build and Launch Production Container Stack
echo "[$(date)] Starting production docker compose service..."
cd "$APP_DIR"
docker compose -f docker-compose.prod.yml up -d --build

# 6. Verify Healthcheck Probe
echo "[$(date)] Waiting for FastAPI /api/health probe..."
for i in {1..12}; do
    if curl -s -f http://127.0.0.1:8080/api/health | grep -q "ok"; then
        echo "[$(date)] SUCCESS: Copilot application is healthy and serving traffic!"
        exit 0
    fi
    echo "[$(date)] Waiting for service to initialize... (attempt $i/12)"
    sleep 5
done

echo "[$(date)] ERROR: Healthcheck timed out. Check docker logs: docker logs it_copilot_app_prod"
exit 1
