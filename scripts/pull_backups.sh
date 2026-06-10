#!/usr/bin/env bash
# Pull DB backups from the VPS into the local backups/vps/ directory.
# Usage: ./scripts/pull_backups.sh  (or: make backup)
# Requires an SSH host alias "logoust" in ~/.ssh/config.
set -euo pipefail

SSH_HOST="logoust"
REMOTE="/var/backups/logoust-assistant/"
LOCAL="backups/vps/"

mkdir -p "${LOCAL}"
echo "==> Pulling backups from ${SSH_HOST}:${REMOTE}"
rsync -az "${SSH_HOST}:${REMOTE}" "${LOCAL}"
echo "==> Done. Local copy:"
ls -lhR "${LOCAL}"
