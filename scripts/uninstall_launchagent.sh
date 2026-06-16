#!/usr/bin/env bash
set -euo pipefail

PLIST_NAMES=(
    "com.annahua.daily-checkin.plist"
    "com.annahua.1point3acres.checkin.plist"
)
SUPPORT_DIRS=(
    "$HOME/Library/Application Support/daily_checkin"
    "$HOME/Library/Application Support/1point3acres-checkin"
)

removed=0

for plist_name in "${PLIST_NAMES[@]}"; do
    target_file="$HOME/Library/LaunchAgents/$plist_name"
    if [ -f "$target_file" ]; then
        launchctl unload "$target_file" >/dev/null 2>&1 || true
        rm -f "$target_file"
        echo "Successfully removed LaunchAgent: $plist_name"
        removed=1
    fi
done

if [ "$removed" -eq 0 ]; then
    echo "LaunchAgent not found, nothing to do."
fi

for support_dir in "${SUPPORT_DIRS[@]}"; do
    wrapper="$support_dir/run_daily_launchagent.sh"
    if [ -f "$wrapper" ]; then
        rm -f "$wrapper"
        echo "Removed LaunchAgent wrapper: $wrapper"
    fi
done
