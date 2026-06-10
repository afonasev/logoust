#!/usr/bin/env bash
# Deploy the bot to the VPS: sync code, install deps, migrate, roll out the
# systemd unit + backup cron from deploy/, restart, verify.
# Usage: ./scripts/deploy.sh  (or: make deploy)
# Requires an SSH host alias "logoust" (root access) in ~/.ssh/config.
set -euo pipefail

SSH_HOST="logoust"
APP_DIR="/opt/logoust-assistant"
SERVICE="logoust-assistant.service"
CRON="logoust-assistant-backup"
# uv lives in logoust's home; the deploy user (root) runs sync via sudo -u logoust.
UV="/home/logoust/.local/bin/uv"

echo "==> Syncing code to ${SSH_HOST}:${APP_DIR}"
# .env and the database live outside the rsync target, so --delete is safe.
rsync -az --delete \
  --exclude='.git' --exclude='.venv' --exclude='.env' \
  --exclude='__pycache__' --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
  --exclude='backups' --exclude='htmlcov' --exclude='.pytest_cache' \
  --exclude='.ruff_cache' --exclude='.ty_cache' --exclude='.coverage' \
  --exclude='logs' --exclude='data' \
  ./ "${SSH_HOST}:${APP_DIR}/"

echo "==> Installing deps, migrating, rolling out unit+cron, restarting"
ssh "${SSH_HOST}" "
  set -e
  chown -R logoust:logoust ${APP_DIR}
  sudo -u logoust bash -lc 'cd ${APP_DIR} && ${UV} sync --no-dev && ${UV} run --no-dev alembic upgrade head'
  install -m 644 ${APP_DIR}/deploy/${SERVICE} /etc/systemd/system/${SERVICE}
  install -m 644 ${APP_DIR}/deploy/logoust-assistant-backup.cron /etc/cron.d/${CRON}
  systemctl daemon-reload
  systemctl restart ${SERVICE}
  sleep 3
  systemctl is-active ${SERVICE}
"
echo "==> Done"
