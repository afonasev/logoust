#!/usr/bin/env bash
# Create a specialist invite on the VPS and print the deep-link for the prod bot.
# Runs the CLI against the prod DB + prod .env, so the link uses the prod bot
# username — no need to SSH in by hand.
# Usage: ./scripts/create_invite_prod.sh  (or: make create_invite_prod)
# Requires an SSH host alias "logoust" in ~/.ssh/config.
set -euo pipefail

SSH_HOST="logoust"
APP_DIR="/opt/logoust-assistant"
UV="/home/logoust/.local/bin/uv"

# The CLI logs to stdout (JSON in prod) alongside the link; keep only the link
# line so the command's output is the deep-link and nothing else. Errors go to
# stderr and are not swallowed.
ssh "${SSH_HOST}" "sudo -u logoust bash -lc 'cd ${APP_DIR} && ${UV} run --no-dev python -m src.cli.create_invite'" \
  | grep '^https://t\.me/'
