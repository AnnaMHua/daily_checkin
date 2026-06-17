#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export TZ="${TZ:-America/Los_Angeles}"
export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"

mkdir -p "$ROOT/logs"

if [[ $# -eq 0 ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/chrome_daily.py" run --submit
fi

case "$1" in
  chrome)
    shift
    exec "$ROOT/.venv/bin/python" "$ROOT/scripts/chrome_daily.py" run --submit "$@"
    ;;
  chrome-dry-run)
    shift
    exec "$ROOT/.venv/bin/python" "$ROOT/scripts/chrome_daily.py" run "$@"
    ;;
  sync-bank)
    shift
    exec "$ROOT/.venv/bin/python" "$ROOT/scripts/sync_question_bank.py" "$@"
    ;;
  *)
    echo "Usage: $0 [chrome | chrome-dry-run | sync-bank]"
    echo ""
    echo "  (no args)       Run and submit (same as 'chrome')"
    echo "  chrome          Answer and submit"
    echo "  chrome-dry-run  Parse and match answer only, do NOT submit"
    echo "  sync-bank       Fetch latest public question bank from web"
    exit 1
    ;;
esac
