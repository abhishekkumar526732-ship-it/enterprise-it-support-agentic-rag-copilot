#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════
# Enterprise IT Support Copilot — EC2 Local Watchdog & Health Check
# ─────────────────────────────────────────────────────────────────────
# Cron utility to probe local application health and automatically
# restart Docker services if the FastAPI endpoint fails to respond.
#
# Install in root cron:
#   */2 * * * * /opt/it-support-copilot/deploy/healthcheck.sh >> /var/log/copilot-watchdog.log 2>&1
# ═════════════════════════════════════════════════════════════════════

set -euo pipefail

PROBE_URL="http://127.0.0.1:8080/api/health"
APP_DIR="/opt/it-support-copilot"

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "$PROBE_URL" || echo "000")

if [ "$HTTP_CODE" = "200" ]; then
    # Healthy
    exit 0
fi

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] ALERT: Health probe failed with HTTP $HTTP_CODE. Attempting restart..."

cd "$APP_DIR"
if [ -f "docker-compose.prod.yml" ]; then
    docker compose -f docker-compose.prod.yml restart copilot-app
else
    docker compose restart copilot-app
fi

sleep 10
NEW_HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "$PROBE_URL" || echo "000")
if [ "$NEW_HTTP_CODE" = "200" ]; then
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] RECOVERY: Copilot app recovered successfully."
else
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] CRITICAL: Service still unhealthy after restart (HTTP $NEW_HTTP_CODE)."
    exit 1
fi
