#!/usr/bin/env bash
# Daily SQLite backup for logoust-assistant, run on the VPS via cron.
# Daily snapshots kept 7 days; every Sunday a copy is promoted to weekly,
# weekly snapshots kept ~1 month. Uses sqlite3 .backup for a consistent
# online copy (safe while the bot is writing).
set -euo pipefail

DB="/var/lib/logoust-assistant/app.db"
ROOT="/var/backups/logoust-assistant"
DAILY="${ROOT}/daily"
WEEKLY="${ROOT}/weekly"
DATE="$(date +%F)"

mkdir -p "${DAILY}" "${WEEKLY}"
cd "${ROOT}"

DEST="${DAILY}/app-${DATE}.db"
sqlite3 "${DB}" ".backup '${DEST}'"
gzip -f "${DEST}"

# Promote Sunday's backup to the weekly set.
if [ "$(date +%u)" -eq 7 ]; then
	cp "${DEST}.gz" "${WEEKLY}/app-${DATE}.db.gz"
fi

# Retention: daily 7 days, weekly ~1 month.
find "${DAILY}" -name 'app-*.db.gz' -mtime +7 -delete
find "${WEEKLY}" -name 'app-*.db.gz' -mtime +31 -delete
