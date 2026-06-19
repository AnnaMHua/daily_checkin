#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export TZ="${TZ:-America/Los_Angeles}"
export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"

mkdir -p "$ROOT/logs"

if [[ $# -eq 0 ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/chrome_daily.py" run --control cdp --submit
fi

case "$1" in
  chrome-cdp)
    shift
    exec "$ROOT/.venv/bin/python" "$ROOT/scripts/chrome_daily.py" run --control cdp --submit "$@"
    ;;
  chrome-cdp-dry-run)
    shift
    exec "$ROOT/.venv/bin/python" "$ROOT/scripts/chrome_daily.py" run --control cdp "$@"
    ;;
  chrome-cdp-setup)
    shift
    exec "$ROOT/.venv/bin/python" "$ROOT/scripts/chrome_daily.py" setup-cdp "$@"
    ;;
  sync-bank)
    shift
    exec "$ROOT/.venv/bin/python" "$ROOT/scripts/sync_question_bank.py" "$@"
    ;;
  *)
    echo "Usage: $0 [chrome-cdp-setup | chrome-cdp-dry-run | chrome-cdp | sync-bank]"
    echo ""
    echo "  chrome-cdp-setup     Open the dedicated CDP Chrome profile for login/verification"
    echo "  chrome-cdp-dry-run   Parse and match answer only with CDP, do NOT submit"
    echo "  chrome-cdp           Answer and submit with the dedicated CDP Chrome profile"
    echo "  sync-bank            Fetch latest public question bank from web"
    exit 1
    ;;
esac
