#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="# 1point3acres-daily-checkin"
SCHEDULE="${1:-10 9 * * *}"
CRON_LINE="$SCHEDULE /bin/bash -lc 'cd \"$ROOT\" && /bin/bash \"$ROOT/scripts/run_daily.sh\" >> \"$ROOT/logs/cron.log\" 2>&1' $MARKER"

TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT

crontab -l 2>/dev/null | grep -vF "$MARKER" > "$TMP_FILE" || true
printf '%s\n' "$CRON_LINE" >> "$TMP_FILE"
crontab "$TMP_FILE"

echo "Installed cron job:"
echo "$CRON_LINE"
